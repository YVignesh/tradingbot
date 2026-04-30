"""
ai/orchestrator.py — 3-Window AI Coordinator
================================================
Manages the three daily AI sessions:
  1. Pre-Market  (08:50 IST) — strategy, symbols, params
  2. Mid-Day     (12:30 IST) — review morning, adjust params
  3. Post-Market (15:30 IST) — review day, extract lessons

All AI outputs pass through GuardRail before being applied.
The bot calls these methods from bot_runtime.py at the right times.

Usage:
    orchestrator = AIOrchestrator(config)
    plan = orchestrator.pre_market(screener_picks, regime_state, journal_path)
    adjustments = orchestrator.mid_day(trades_so_far, active_syms, regime_state)
    orchestrator.post_market(all_trades, regime_state)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ai.client import AIClient
from ai.guardrails import GuardRail
from ai.lessons import LessonStore
from ai.news import MarketNewsCollector
from ai.prompts import (
    PRE_MARKET_SYSTEM,
    MID_DAY_SYSTEM,
    POST_MARKET_SYSTEM,
    build_pre_market_prompt,
    build_mid_day_prompt,
    build_post_market_prompt,
)
from utils import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
_log = get_logger("ai.orchestrator")


class AIOrchestrator:
    """
    Coordinates the 3 daily AI windows.

    All AI modules share a single AIClient instance.
    All outputs are validated through GuardRail.
    """

    def __init__(self, config: dict):
        self.config = config
        ai_cfg = config.get("ai", {})
        self.enabled = bool(ai_cfg.get("enabled", False))

        # Shared instances
        self.client = AIClient(config)
        self.guardrail = GuardRail(config)
        self.lessons = LessonStore(
            lookback_days=int(ai_cfg.get("lessons_lookback_days", 7)),
        )
        self.news = MarketNewsCollector(config)

        # Today's state
        self._today_str = ""
        self._day_plan: dict = {}
        self._mid_day_adjustments: dict = {}
        self._trades_collected: list[dict] = []

        # Available strategies (from registry)
        try:
            from strategies.registry import STRATEGIES
            self._available_strategies = sorted(STRATEGIES.keys())
        except ImportError:
            self._available_strategies = []

    # ──────────────────────────────────────────────────────────
    # Window 1: Pre-Market
    # ──────────────────────────────────────────────────────────

    def pre_market(
        self,
        screener_picks: list[dict],
        regime_state: dict,
        journal_path: str = "data/journal/trades.sqlite3",
    ) -> dict:
        """
        Pre-market AI session. Called once after screener runs (~08:50 IST).

        Args:
            screener_picks: today's screener results [{symbol, score, close, atr}, ...]
            regime_state: {regime, adx, atr_pct}
            journal_path: path to SQLite trade journal

        Returns:
            DayPlan dict with strategy, symbols, risk_params, reasoning.
            Empty dict if AI disabled or call fails.
        """
        self._today_str = datetime.now(IST).strftime("%Y-%m-%d")
        self._trades_collected = []
        self._mid_day_adjustments = {}

        if not self.enabled:
            self._day_plan = {}
            return {}

        _log.info("=== AI Pre-Market Session ===")

        # Gather context
        news_ctx = self.news.collect_pre_market()
        yesterday_stats = self._get_yesterday_stats(journal_path)
        lessons_block = self.lessons.format_recent_for_prompt()
        rules_block = self.lessons.format_rules_for_prompt()

        universe = [p.get("symbol", "") for p in screener_picks]

        try:
            prompt = build_pre_market_prompt(
                news_block=news_ctx.to_prompt_block(),
                lessons_block=lessons_block,
                rules_block=rules_block,
                yesterday_stats=yesterday_stats,
                screener_picks=screener_picks,
                regime_state=regime_state,
                current_config=self.config,
                available_strategies=self._available_strategies,
            )
            raw = self.client.generate_json(prompt, system=PRE_MARKET_SYSTEM)

        except Exception as exc:
            _log.warning("Pre-market AI call failed: %s", exc)
            self._day_plan = {}
            return {}

        if not raw:
            _log.warning("Pre-market AI returned empty response")
            self._day_plan = {}
            return {}

        # Validate through guardrails
        plan = self._apply_guardrails_to_plan(raw, universe)
        self._day_plan = plan
        self.lessons.save_day_plan(self._today_str, plan)

        _log.info(
            "Day plan: strategy=%s confidence=%s%% — %s",
            plan.get("strategy", "?"),
            plan.get("confidence", "?"),
            plan.get("reasoning", ""),
        )
        return plan

    def _apply_guardrails_to_plan(self, raw: dict, universe: list[str]) -> dict:
        """Validate and sanitize the pre-market plan."""
        plan = {}

        # Strategy
        strategy = raw.get("strategy", "")
        validated_strat = self.guardrail.validate_strategy_name(
            strategy, self._available_strategies,
        )
        if validated_strat:
            plan["strategy"] = validated_strat

        # Symbols to prefer
        prefer = raw.get("symbols_to_prefer", [])
        if isinstance(prefer, list):
            plan["symbols_to_prefer"] = self.guardrail.validate_symbol_list(
                prefer, universe, window="pre_market",
            )

        # Symbols to avoid
        avoid = raw.get("symbols_to_avoid", [])
        if isinstance(avoid, list):
            plan["symbols_to_avoid"] = self.guardrail.validate_symbol_list(
                avoid, universe, window="pre_market",
            )

        # Risk params
        risk_params = raw.get("risk_params", {})
        if isinstance(risk_params, dict):
            current_risk = self.config.get("risk", {})
            plan["risk_params"] = self.guardrail.validate_risk_params(
                risk_params, current_risk, window="pre_market",
            )

        plan["confidence"] = min(100, max(0, int(raw.get("confidence", 50))))
        plan["reasoning"] = str(raw.get("reasoning", ""))[:500]
        plan["market_outlook"] = str(raw.get("market_outlook", ""))[:100]

        return plan

    # ──────────────────────────────────────────────────────────
    # Window 2: Mid-Day
    # ──────────────────────────────────────────────────────────

    def mid_day(
        self,
        trades_so_far: list[dict],
        active_symbols: list[str],
        regime_state: dict,
    ) -> dict:
        """
        Mid-day AI review. Called once at ~12:30 IST.

        Args:
            trades_so_far: completed trades from the morning
            active_symbols: currently active symbol list
            regime_state: current {regime, adx, atr_pct}

        Returns:
            Adjustments dict with param_changes, symbols_to_drop, reasoning.
        """
        if not self.enabled:
            return {}

        _log.info("=== AI Mid-Day Review ===")
        self._trades_collected = list(trades_so_far)

        current_risk = self.config.get("risk", {})
        current_params = {
            "sl_atr_multiplier": current_risk.get("sl_atr_multiplier", 1.5),
            "tp_atr_multiplier": current_risk.get("tp_atr_multiplier", 3.0),
            "max_risk_pct": current_risk.get("max_risk_pct", 2.0),
        }

        try:
            prompt = build_mid_day_prompt(
                day_plan=self._day_plan,
                trades_so_far=trades_so_far,
                current_params=current_params,
                active_symbols=active_symbols,
                regime_state=regime_state,
            )
            raw = self.client.generate_json(prompt, system=MID_DAY_SYSTEM)

        except Exception as exc:
            _log.warning("Mid-day AI call failed: %s", exc)
            return {}

        if not raw:
            return {}

        # Validate param changes
        adjustments = {}
        param_changes = raw.get("param_changes", {})
        if isinstance(param_changes, dict) and param_changes:
            adjustments["param_changes"] = self.guardrail.validate_risk_params(
                param_changes, current_params, window="mid_day",
            )

        # Validate symbols to drop
        symbols_drop = raw.get("symbols_to_drop", [])
        if isinstance(symbols_drop, list) and symbols_drop:
            adjustments["symbols_to_drop"] = self.guardrail.validate_symbol_list(
                symbols_drop, active_symbols, window="mid_day",
            )

        adjustments["hold_positions"] = bool(raw.get("hold_positions", True))
        adjustments["reasoning"] = str(raw.get("reasoning", ""))[:500]

        self._mid_day_adjustments = adjustments

        _log.info(
            "Mid-day adjustments: params=%s drops=%s — %s",
            adjustments.get("param_changes", {}),
            adjustments.get("symbols_to_drop", []),
            adjustments.get("reasoning", ""),
        )
        return adjustments

    # ──────────────────────────────────────────────────────────
    # Window 3: Post-Market
    # ──────────────────────────────────────────────────────────

    def post_market(
        self,
        all_trades: list[dict],
        regime_state: dict,
    ) -> dict:
        """
        Post-market AI review. Called once after market close (~15:30 IST).

        Saves lessons and extracts rules for future sessions.

        Args:
            all_trades: all completed trades for the day
            regime_state: end-of-day {regime, adx, atr_pct}

        Returns:
            Lessons dict (also saved to data/ai/lessons/).
        """
        if not self.enabled:
            return {}

        _log.info("=== AI Post-Market Review ===")

        current_risk = self.config.get("risk", {})
        current_params = {
            "sl_atr_multiplier": current_risk.get("sl_atr_multiplier", 1.5),
            "tp_atr_multiplier": current_risk.get("tp_atr_multiplier", 3.0),
            "max_risk_pct": current_risk.get("max_risk_pct", 2.0),
        }

        try:
            prompt = build_post_market_prompt(
                day_plan=self._day_plan,
                mid_day_adjustments=self._mid_day_adjustments,
                all_trades=all_trades,
                regime_state=regime_state,
                current_params=current_params,
            )
            raw = self.client.generate_json(prompt, system=POST_MARKET_SYSTEM)

        except Exception as exc:
            _log.warning("Post-market AI call failed: %s", exc)
            return {}

        if not raw:
            return {}

        # Save lessons (validated fields only)
        lessons = {
            "day_pnl": float(raw.get("day_pnl", 0)),
            "win_rate": float(raw.get("win_rate", 0)),
            "total_trades": int(raw.get("total_trades", len(all_trades))),
            "lessons": [str(l)[:200] for l in raw.get("lessons", [])[:10]],
            "param_suggestions": {},
            "rules_to_add": [str(r)[:200] for r in raw.get("rules_to_add", [])[:5]],
            "strategy_assessment": str(raw.get("strategy_assessment", ""))[:300],
            "mid_day_changes_helped": raw.get("mid_day_changes_helped"),
            "tomorrow_focus": str(raw.get("tomorrow_focus", ""))[:200],
            "strategy_used": self._day_plan.get("strategy", "?"),
        }

        # Validate param suggestions through guardrails (logged only, not applied)
        param_suggestions = raw.get("param_suggestions", {})
        if isinstance(param_suggestions, dict):
            lessons["param_suggestions"] = self.guardrail.validate_risk_params(
                param_suggestions, current_params, window="post_market",
            )

        self.lessons.save_lessons(self._today_str, lessons)

        _log.info(
            "Post-market lessons: %d lessons, %d rules proposed — %s",
            len(lessons["lessons"]),
            len(lessons["rules_to_add"]),
            lessons.get("tomorrow_focus", ""),
        )

        # Log AI usage stats for the day
        usage = self.client.usage_stats()
        _log.info(
            "AI usage today: %d calls, %d input tokens, %d output tokens, avg %.0fms",
            usage["calls"], usage["input_tokens"], usage["output_tokens"],
            usage["avg_latency_ms"],
        )

        return lessons

    # ──────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────

    def _get_yesterday_stats(self, journal_path: str) -> dict:
        """Extract yesterday's trade stats from SQLite journal."""
        path = Path(journal_path)
        if not path.exists():
            return {}

        yesterday = (datetime.now(IST) - timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN gross_pnl > 0 THEN 1 ELSE 0 END) as winners,
                       SUM(gross_pnl) as day_pnl,
                       AVG(mae) as avg_mae,
                       AVG(mfe) as avg_mfe
                FROM trades
                WHERE date(exit_time) = ?
                """,
                (yesterday,),
            )
            row = cursor.fetchone()
            conn.close()

            if not row or row["total"] == 0:
                return {}

            total = row["total"]
            winners = row["winners"] or 0
            return {
                "total_trades": total,
                "win_rate": round(winners / total * 100, 1),
                "day_pnl": round(row["day_pnl"] or 0, 2),
                "avg_mae": round(row["avg_mae"] or 0, 2),
                "avg_mfe": round(row["avg_mfe"] or 0, 2),
            }
        except Exception as exc:
            _log.debug("Could not read yesterday stats: %s", exc)
            return {}

    def apply_day_plan(self, config: dict, plan: dict) -> dict:
        """
        Apply the AI's day plan to the config dict (in-memory only).

        Returns the modified config. Does NOT write to disk.
        """
        if not plan:
            return config

        # Apply strategy
        if "strategy" in plan:
            config.setdefault("strategy", {})["name"] = plan["strategy"]
            _log.info("AI set strategy → %s", plan["strategy"])

        # Apply risk params
        for key, value in plan.get("risk_params", {}).items():
            config.setdefault("risk", {})[key] = value
            _log.info("AI set risk.%s → %s", key, value)

        return config

    def apply_mid_day_adjustments(
        self,
        config: dict,
        adjustments: dict,
    ) -> tuple[dict, list[str]]:
        """
        Apply mid-day adjustments to config.

        Returns:
            (modified_config, symbols_to_drop)
        """
        if not adjustments:
            return config, []

        for key, value in adjustments.get("param_changes", {}).items():
            config.setdefault("risk", {})[key] = value
            _log.info("AI mid-day: risk.%s → %s", key, value)

        symbols_to_drop = adjustments.get("symbols_to_drop", [])
        return config, symbols_to_drop

    def get_regime_state(self, regime_filter) -> dict:
        """Extract regime state dict from MarketRegimeFilter instance."""
        return {
            "regime": regime_filter.regime if regime_filter.enabled else "UNKNOWN",
            "adx": regime_filter.adx_value if regime_filter.enabled else 0.0,
            "atr_pct": regime_filter.atr_pct if regime_filter.enabled else 0.0,
        }

    def collect_trades(self, trades: list[dict]) -> None:
        """Add completed trades to today's collection for post-market review."""
        self._trades_collected.extend(trades)

    def clear_trades(self) -> None:
        """Clear today's collected trades (called on day rollover)."""
        self._trades_collected.clear()

    def get_collected_trades(self) -> list[dict]:
        """Return a copy of today's collected trades."""
        return list(self._trades_collected)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "provider": self.client.provider,
            "model": self.client.model,
            "today": self._today_str,
            "has_day_plan": bool(self._day_plan),
            "has_mid_day": bool(self._mid_day_adjustments),
            "usage": self.client.usage_stats(),
        }
