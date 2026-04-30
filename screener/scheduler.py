"""
screener/scheduler.py — Daily screener scheduling and caching
=============================================================
Runs the screener once per day and locks the selected symbols.

Scheduling rules:
  1. Today's cache exists → always use it (even if stale within the day).
  2. Inside market window (08:00–16:00 IST) and no today's cache → run screener.
  3. Outside market window and no today's cache → use previous cache and wait
     for tomorrow's window (avoids 403s that AngelOne returns on candle data
     outside trading hours).
  4. Screener runs but >80% of symbols error → treat as API failure, fall back
     to previous cache without overwriting it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from broker.instruments import InstrumentMaster
from screener.filters import evaluate_symbol
from screener.registry import get_screener
from screener.universe import load_universe
from utils import get_logger

IST = timezone(timedelta(hours=5, minutes=30))

# Screener only runs inside these IST hours; outside them use cached results.
_MARKET_OPEN_HOUR  = 8   # 08:00 IST — safe to fetch candle data
_MARKET_CLOSE_HOUR = 16  # 16:00 IST — market settled, data available


class ScreenerScheduler:
    def __init__(self, config: dict, cache_path: str = "data/cache/screener_selection.json"):
        self.config = config
        self.cfg = config.get("screener", {})
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = get_logger(__name__)
        self._screener = get_screener(config)

    def is_enabled(self) -> bool:
        return bool(self.cfg.get("enabled", False))

    def resolve_symbols(self, session, force: bool = False) -> list[dict]:
        if not self.is_enabled():
            return []

        today_key = datetime.now(IST).strftime("%Y-%m-%d")
        cached = self._load_cache()

        # Always prefer a valid today's cache
        if not force and cached and cached.get("trade_date") == today_key:
            syms = list(cached.get("symbols", []))
            self.log.info("Using cached screener selection (%s): %d symbols", today_key, len(syms))
            return syms

        # Outside market hours: candle APIs return 403 — use previous cache instead
        now_ist = datetime.now(IST)
        in_market_hours = _MARKET_OPEN_HOUR <= now_ist.hour < _MARKET_CLOSE_HOUR
        if not force and not in_market_hours:
            prev_syms = list(cached.get("symbols", [])) if cached else []
            if prev_syms:
                self.log.warning(
                    "Screener skipped outside market hours (%02d:%02d IST) — "
                    "using previous cache from %s (%d symbols)",
                    now_ist.hour, now_ist.minute,
                    cached.get("trade_date", "?"), len(prev_syms),
                )
            else:
                self.log.warning(
                    "Screener skipped outside market hours (%02d:%02d IST) and no "
                    "previous cache — bot will idle until 08:00 IST",
                    now_ist.hour, now_ist.minute,
                )
            return prev_syms

        # Run the screener; get back (symbols, was_api_failure)
        selected, api_failure = self._run_screener(session)

        if api_failure:
            # Most symbols errored — don't trust the result or overwrite the cache
            prev_syms = list(cached.get("symbols", [])) if cached else []
            self.log.warning(
                "Screener aborted due to widespread API errors — "
                "falling back to previous cache from %s (%d symbols)",
                cached.get("trade_date", "none") if cached else "none",
                len(prev_syms),
            )
            return prev_syms

        # Successful run (even if 0 stocks qualified — that's a legitimate result)
        self._save_cache({"trade_date": today_key, "symbols": selected})
        return selected

    def _run_screener(self, session) -> tuple[list[dict], bool]:
        """
        Returns (selected_symbols, api_failure).
        api_failure=True when >80% of universe symbols raised exceptions
        (indicating an API outage rather than genuine lack of qualifying stocks).
        """
        watchlist = self.cfg.get("watchlist", [])
        if not watchlist:
            return [], False

        default_exchange = str(self.cfg.get("default_exchange", "NSE")).upper()
        universe = load_universe(watchlist, default_exchange=default_exchange)
        if not universe:
            return [], False

        master = InstrumentMaster()
        master.load()

        # Resolve user-supplied tickers to canonical AngelOne symbols (e.g. SBIN → SBIN-EQ)
        resolved_universe = []
        for item in universe:
            canonical = master.resolve_symbol(item["exchange"], item["symbol"])
            if canonical:
                resolved_universe.append({**item, "symbol": canonical})
            else:
                self.log.debug(
                    "Symbol %s:%s not in instruments master — skipping",
                    item["exchange"], item["symbol"],
                )
        universe = resolved_universe
        if not universe:
            return [], False

        total   = len(universe)
        errors  = 0
        candidates = []

        for item in universe:
            token = master.get_token(item["exchange"], item["symbol"])
            if not token:
                errors += 1
                continue
            try:
                metrics = evaluate_symbol(
                    session,
                    exchange=item["exchange"],
                    symbol=item["symbol"],
                    token=token,
                    lookback_days=int(self.cfg.get("lookback_days", 45)),
                    min_price=float(self.cfg.get("min_price", 0.0)),
                    max_price=float(self.cfg.get("max_price", 0.0)),
                    min_avg_volume=float(self.cfg.get("min_avg_volume", 0.0)),
                    min_atr=float(self.cfg.get("min_atr", 0.0)),
                    max_atr=float(self.cfg.get("max_atr", 0.0)),
                    max_gap_pct=float(self.cfg.get("max_gap_pct", 0.0)),
                    screener=self._screener,
                )
            except Exception as exc:
                self.log.warning("Screener failed for %s:%s (%s)", item["exchange"], item["symbol"], exc)
                errors += 1
                continue
            if metrics is not None:
                candidates.append(metrics)

        # >80% errors → API is down, not a real screening result
        if total > 0 and errors / total > 0.8:
            self.log.error(
                "Screener: %d/%d symbols errored — treating as API failure",
                errors, total,
            )
            return [], True

        top_n  = int(self.cfg.get("top_n", 5))
        ranked = self._screener.rank(candidates, top_n=top_n)
        selected = [
            {"exchange": item["exchange"], "symbol": item["symbol"], "score": item["score"]}
            for item in ranked
        ]
        self.log.info(
            "Screener: %d/%d candidates qualified → selected %d: %s",
            len(candidates), total - errors, len(selected),
            [s["symbol"] for s in selected],
        )
        return selected, False

    def _load_cache(self) -> dict:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.log.warning("Could not read screener cache (%s)", exc)
            return {}

    def _save_cache(self, payload: dict) -> None:
        self.cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
