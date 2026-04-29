"""
strategies/vwap_pullback.py — Intraday VWAP pullback strategy
==============================================================
Uses anchored daily VWAP plus a trend stack to buy pullbacks in strength
and fade rallies into weakness on the short side.
"""

from __future__ import annotations

import pandas as pd

from indicators.momentum import rsi
from indicators.trend import ema
from indicators.volume import vwap
from strategies.directional import DirectionalStrategy


def _session_vwap(df: pd.DataFrame) -> pd.Series:
    parts: list[pd.Series] = []
    for _, group in df.groupby(df.index.tz_convert("Asia/Kolkata").date):
        parts.append(vwap(group["high"], group["low"], group["close"], group["volume"]))
    return pd.concat(parts).sort_index()


class VwapPullbackStrategy(DirectionalStrategy):
    NAME = "vwap_pullback"

    def __init__(self, config: dict):
        super().__init__(config)
        strat = config["strategy"]
        self.fast_ema = int(strat.get("fast_ema", 9))
        self.slow_ema = int(strat.get("slow_ema", 21))
        self.rsi_period = int(strat.get("rsi_period", 14))
        self.rsi_entry_floor = float(strat.get("rsi_entry_floor", 50))
        self.rsi_entry_ceiling = float(strat.get("rsi_entry_ceiling", 60))
        self.vwap_buffer_pct = float(strat.get("vwap_buffer_pct", 0.002))

    def required_history_bars(self) -> int:
        return max(self.fast_ema, self.slow_ema, self.rsi_period) + 3

    def prepare_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        prepared = df.copy()
        prepared["ema_fast"] = ema(prepared["close"], self.fast_ema)
        prepared["ema_slow"] = ema(prepared["close"], self.slow_ema)
        prepared["rsi"] = rsi(prepared["close"], self.rsi_period)
        prepared["session_vwap"] = _session_vwap(prepared)
        prepared["dist_vwap_pct"] = (prepared["close"] - prepared["session_vwap"]) / prepared["session_vwap"]
        return prepared

    def signal_from_prepared(self, df: pd.DataFrame, index: int, direction: str):
        close = float(df["close"].iloc[index])
        prev_close = float(df["close"].iloc[index - 1])
        ema_fast = float(df["ema_fast"].iloc[index])
        ema_slow = float(df["ema_slow"].iloc[index])
        vwap_value = float(df["session_vwap"].iloc[index])
        prev_vwap = float(df["session_vwap"].iloc[index - 1])
        rsi_value = float(df["rsi"].iloc[index])
        prev_rsi = float(df["rsi"].iloc[index - 1])

        uptrend = close > ema_slow and ema_fast > ema_slow
        downtrend = close < ema_slow and ema_fast < ema_slow

        long_reclaim = prev_close <= prev_vwap and close > vwap_value
        short_reject = prev_close >= prev_vwap and close < vwap_value

        long_bias = uptrend and long_reclaim and self.rsi_entry_floor <= rsi_value <= self.rsi_entry_ceiling and prev_rsi < rsi_value
        short_bias = downtrend and short_reject and (100 - self.rsi_entry_ceiling) <= rsi_value <= (100 - self.rsi_entry_floor) and prev_rsi > rsi_value

        if direction == "LONG":
            if close < ema_fast or close < vwap_value * (1 - self.vwap_buffer_pct) or rsi_value < self.rsi_entry_floor:
                return "SELL"
            return None
        if direction == "SHORT":
            if close > ema_fast or close > vwap_value * (1 + self.vwap_buffer_pct) or rsi_value > (100 - self.rsi_entry_floor):
                return "COVER"
            return None
        if long_bias:
            return "BUY"
        if short_bias:
            return "SHORT"
        return None

    def describe_bar(self, df: pd.DataFrame, index: int) -> str:
        close = float(df["close"].iloc[index])
        vwap_value = float(df["session_vwap"].iloc[index])
        ema_fast = float(df["ema_fast"].iloc[index])
        ema_slow = float(df["ema_slow"].iloc[index])
        rsi_value = float(df["rsi"].iloc[index])
        return (
            f"close=₹{close:.2f} VWAP={vwap_value:.2f} EMA{self.fast_ema}={ema_fast:.2f} "
            f"EMA{self.slow_ema}={ema_slow:.2f} RSI={rsi_value:.1f}"
        )
