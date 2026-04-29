"""
screener/scheduler.py — Daily screener scheduling and caching
=============================================================
Runs the screener once per day and locks the selected symbols.
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
        if not force and cached and cached.get("trade_date") == today_key:
            return list(cached.get("symbols", []))

        now = datetime.now(IST)
        start_h, start_m = map(int, str(self.cfg.get("run_window_start", "09:00")).split(":"))
        end_h, end_m = map(int, str(self.cfg.get("run_window_end", "09:10")).split(":"))
        window_start = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        window_end = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
        if now > window_end:
            self.log.warning("Screener window missed for %s - running at startup fallback", today_key)

        selected = self._run_screener(session)
        self._save_cache({"trade_date": today_key, "symbols": selected})
        return selected

    def _run_screener(self, session) -> list[dict]:
        watchlist = self.cfg.get("watchlist", [])
        if not watchlist:
            return []

        default_exchange = str(self.cfg.get("default_exchange", "NSE")).upper()
        universe = load_universe(watchlist, default_exchange=default_exchange)
        if not universe:
            return []

        master = InstrumentMaster()
        master.load()

        candidates = []
        for item in universe:
            token = master.get_token(item["exchange"], item["symbol"])
            if not token:
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
                continue
            if metrics is not None:
                candidates.append(metrics)

        top_n = int(self.cfg.get("top_n", 5))
        ranked = self._screener.rank(candidates, top_n=top_n)
        selected = [{"exchange": item["exchange"], "symbol": item["symbol"], "score": item["score"]} for item in ranked]
        self.log.info("Screener selected %d symbols: %s", len(selected), selected)
        return selected

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
