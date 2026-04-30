"""
utils/market_regime.py — Market Regime Filter
==============================================
Classifies the broad market (e.g. Nifty 50) as TRENDING or CHOPPY using
ADX + ATR-relative-range. Trend-following strategies should gate entries
on regime == TRENDING to avoid whipsaw losses.

Usage (live):
    regime = MarketRegimeFilter(config["regime_filter"])
    regime.update(session)           # call once per loop tick / bar
    if regime.allows_entry():
        ... place order ...

Usage (backtest):
    regime = MarketRegimeFilter(cfg)
    regime.update_from_df(nifty_df)  # precomputed daily/intraday bars
    if regime.allows_entry():
        ...
"""

from __future__ import annotations

import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional

from indicators.trend import adx
from indicators.volatility import atr as compute_atr
from utils import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
_log = get_logger(__name__)


class MarketRegimeFilter:
    """
    Determines if the broad market is in a trending or choppy regime.

    Config keys (all under `regime_filter` in config.json):
        enabled        : bool  — master switch (default False)
        index_symbol   : str   — instrument to monitor (default "NIFTY")
        index_exchange : str   — exchange for lookup (default "NSE")
        index_token    : str   — instrument token (default "99926000")
        adx_period     : int   — ADX lookback (default 14)
        adx_threshold  : float — ADX below this = choppy (default 20.0)
        atr_period     : int   — ATR lookback (default 14)
        atr_range_min  : float — min ATR/close ratio × 100 to confirm trend (default 0.5)
        lookback_bars  : int   — how many recent bars to compute on (default 50)
        interval       : str   — candle interval for index data (default "FIFTEEN_MINUTE")
    """

    def __init__(self, config: dict) -> None:
        self.enabled = bool(config.get("enabled", False))
        self.index_symbol = str(config.get("index_symbol", "NIFTY"))
        self.index_exchange = str(config.get("index_exchange", "NSE"))
        self.index_token = str(config.get("index_token", "99926000"))
        self.adx_period = int(config.get("adx_period", 14))
        self.adx_threshold = float(config.get("adx_threshold", 20.0))
        self.atr_period = int(config.get("atr_period", 14))
        self.atr_range_min = float(config.get("atr_range_min", 0.5))
        self.lookback_bars = int(config.get("lookback_bars", 50))
        self.interval = str(config.get("interval", "FIFTEEN_MINUTE"))

        # Current state
        self._regime: str = "UNKNOWN"  # TRENDING | CHOPPY | UNKNOWN
        self._adx_value: float = 0.0
        self._atr_pct: float = 0.0
        self._last_update: Optional[datetime] = None

    @property
    def regime(self) -> str:
        return self._regime

    @property
    def adx_value(self) -> float:
        return self._adx_value

    @property
    def atr_pct(self) -> float:
        return self._atr_pct

    def allows_entry(self) -> tuple[bool, str]:
        """
        Check if the current regime allows new entries.

        Returns:
            (allowed, reason) — True if trending or filter disabled
        """
        if not self.enabled:
            return True, ""
        if self._regime == "TRENDING":
            return True, ""
        if self._regime == "UNKNOWN":
            return True, "regime unknown — allowing entry"
        return False, (
            f"market regime CHOPPY (ADX={self._adx_value:.1f} < {self.adx_threshold}, "
            f"ATR%={self._atr_pct:.2f}%)"
        )

    def update(self, session) -> str:
        """
        Fetch recent index candles from broker and recompute regime.
        Call once per strategy loop iteration (rate limited by loop_interval).

        Args:
            session: authenticated AngelSession

        Returns:
            Current regime string: "TRENDING" | "CHOPPY"
        """
        if not self.enabled:
            return self._regime

        try:
            from broker.market_data import get_candles_n_days, candles_to_dataframe
            needed_bars = self.lookback_bars + self.adx_period + 10
            days = max(5, (needed_bars * 3) // 75 + 2)
            candles = get_candles_n_days(
                session,
                self.index_exchange,
                self.index_token,
                days=days,
                interval=self.interval,
            )
            if candles:
                df = candles_to_dataframe(candles)
            else:
                df = None
            if df is not None and len(df) >= self.adx_period + 5:
                return self._classify(df)
            else:
                _log.warning("Regime filter: insufficient index data — allowing entries")
                self._regime = "UNKNOWN"
        except Exception as exc:
            _log.warning("Regime filter update failed: %s — allowing entries", exc)
            self._regime = "UNKNOWN"

        return self._regime

    def update_from_df(self, df: pd.DataFrame) -> str:
        """
        Recompute regime from a pre-built DataFrame (backtest path).
        Expects columns: high, low, close.

        Args:
            df: OHLC DataFrame with at least `adx_period + 5` rows

        Returns:
            Current regime string
        """
        if not self.enabled:
            return self._regime
        if df is None or len(df) < self.adx_period + 5:
            self._regime = "UNKNOWN"
            return self._regime
        return self._classify(df)

    def _classify(self, df: pd.DataFrame) -> str:
        """Compute ADX and ATR% from the tail of the DataFrame."""
        adx_line, _, _ = adx(df["high"], df["low"], df["close"], self.adx_period)
        atr_line = compute_atr(df["high"], df["low"], df["close"], self.atr_period)

        current_adx = float(adx_line.iloc[-1]) if pd.notna(adx_line.iloc[-1]) else 0.0
        current_atr = float(atr_line.iloc[-1]) if pd.notna(atr_line.iloc[-1]) else 0.0
        current_close = float(df["close"].iloc[-1])
        atr_pct = (current_atr / current_close * 100) if current_close > 0 else 0.0

        self._adx_value = current_adx
        self._atr_pct = atr_pct
        self._last_update = datetime.now(IST)

        if current_adx >= self.adx_threshold and atr_pct >= self.atr_range_min:
            self._regime = "TRENDING"
        else:
            self._regime = "CHOPPY"

        _log.info(
            "Regime: %s  ADX=%.1f (threshold=%.1f)  ATR%%=%.2f%% (min=%.2f%%)",
            self._regime, current_adx, self.adx_threshold, atr_pct, self.atr_range_min,
        )
        return self._regime

    def status(self) -> dict:
        """Return current regime state for logging/monitoring."""
        return {
            "regime": self._regime,
            "adx": round(self._adx_value, 2),
            "atr_pct": round(self._atr_pct, 3),
            "adx_threshold": self.adx_threshold,
            "atr_range_min": self.atr_range_min,
            "last_update": self._last_update.isoformat() if self._last_update else None,
        }
