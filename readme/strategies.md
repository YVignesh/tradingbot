# Trading Strategies — Reference Manual

All strategies inherit from `DirectionalStrategy` and support BUY / SELL / SHORT / COVER signals.
Set the active strategy in `config.json` under `strategy.name`.

---

## Quick Reference

| Key | Strategy | Style | Best Interval | Best Market |
|-----|----------|-------|---------------|-------------|
| `ema_crossover` | EMA 9/21 Crossover | Trend | 5m, 15m | Trending |
| `macd_rsi_trend` | MACD + RSI Trend | Trend + Momentum | 15m, 1h | Trending |
| `vwap_pullback` | VWAP Pullback | Mean Reversion | 5m, 15m | Intraday |
| `bollinger_breakout` | Bollinger Squeeze | Volatility Breakout | 15m, 1h | Ranging → Breakout |
| `supertrend` | Supertrend ATR | Trend | 15m, 1h | Any trending |
| `rsi_reversal` | RSI Mean Reversion | Counter-trend | 15m, 1h | Oscillating |
| `orb` | Opening Range Breakout | Momentum | 5m, 15m | Intraday open |
| `stochastic_crossover` | Stochastic %K/%D | Oscillator | 15m, 1h | Oscillating |
| `three_ema_trend` | Triple EMA Alignment | Trend | 1h, Daily | Strong trends |
| `pivot_bounce` | Daily Pivot Bounce | S/R Breakout | 5m, 15m | Intraday |

---

## 1. EMA Crossover (`ema_crossover`)

**Concept:** The classic dual-EMA crossover. When the fast EMA (9) crosses above the slow EMA (21), the market has shifted to a short-term bullish regime. Works best in strongly trending markets; generates whipsaws in sideways conditions.

**Signals:**
- `BUY` when EMA9 > EMA21 and was below
- `SELL` when EMA9 < EMA21 (exit long)
- `SHORT` when EMA9 < EMA21 and flat
- `COVER` when EMA9 > EMA21 (exit short)

**Config example:**
```json
{
  "strategy": {
    "name": "ema_crossover",
    "symbol": "RELIANCE-EQ",
    "exchange": "NSE",
    "interval": "FIFTEEN_MINUTE",
    "ema_fast": 9,
    "ema_slow": 21
  }
}
```

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `ema_fast` | 9 | Fast EMA period |
| `ema_slow` | 21 | Slow EMA period |

**Best used with:** `momentum` screener, `momentum_weighted` allocator

---

## 2. MACD + RSI Trend (`macd_rsi_trend`)

**Concept:** Combines a trend filter (EMA50) with MACD momentum and RSI confirmation. Requires all three conditions to align before entering — fewer but higher-quality signals. Significantly reduces false entries in choppy markets.

**Logic:**
- **BUY:** close > EMA50 AND MACD above signal AND histogram positive AND RSI ≥ 55
- **SHORT:** close < EMA50 AND MACD below signal AND histogram negative AND RSI ≤ 45
- **SELL:** Any of trend/MACD/RSI conditions break for longs
- **COVER:** Same for shorts

**Config example:**
```json
{
  "strategy": {
    "name": "macd_rsi_trend",
    "symbol": "SBIN-EQ",
    "exchange": "NSE",
    "interval": "FIFTEEN_MINUTE",
    "trend_ema": 50,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "rsi_period": 14,
    "rsi_long_threshold": 55,
    "rsi_short_threshold": 45,
    "rsi_exit_long": 50,
    "rsi_exit_short": 50
  }
}
```

**Best used with:** `breakout` or `quality_trend` screener

---

## 3. VWAP Pullback (`vwap_pullback`)

**Concept:** VWAP (Volume-Weighted Average Price) is the institutional benchmark price for the day. Price pulling back to VWAP in an uptrend and reclaiming it is a high-probability long entry — institutions often defend VWAP.

**Logic:**
- **BUY:** Price pulls back to VWAP and closes above it with RSI recovering
- **SHORT:** Price pulls back to VWAP from above and closes below it
- Works best early in the session (before 12:00 PM IST)

**Config example:**
```json
{
  "strategy": {
    "name": "vwap_pullback",
    "symbol": "NIFTY-EQ",
    "exchange": "NSE",
    "interval": "FIVE_MINUTE"
  }
}
```

**Best used with:** `high_rvol` screener (high volume confirms VWAP importance)

---

## 4. Bollinger Band Breakout (`bollinger_breakout`)

**Concept:** Bollinger Bands squeeze (bands narrow) indicates low volatility and compression. When price breaks out of the squeeze, the move is typically sharp and sustained. The squeeze is detected when band width drops to a multi-period low.

**Config example:**
```json
{
  "strategy": {
    "name": "bollinger_breakout",
    "symbol": "HDFCBANK-EQ",
    "exchange": "NSE",
    "interval": "ONE_HOUR",
    "bb_period": 20,
    "bb_std": 2.0,
    "squeeze_period": 50
  }
}
```

**Best used with:** `vcp` screener (both detect compression before breakout)

---

## 5. Supertrend (`supertrend`)

**Concept:** The Supertrend indicator draws a dynamic support/resistance line based on ATR. When price crosses above the line (direction flips bullish), a strong up-move has begun. When it flips bearish, exit and potentially reverse. Very popular among Indian retail traders on Angel One and Zerodha.

**Logic:**
- `BUY` on direction flip: bearish (-1) → bullish (+1)
- `SHORT` on direction flip: bullish (+1) → bearish (-1)
- The line itself acts as a trailing stop once in position

**Config example:**
```json
{
  "strategy": {
    "name": "supertrend",
    "symbol": "TATAMOTORS-EQ",
    "exchange": "NSE",
    "interval": "FIFTEEN_MINUTE",
    "supertrend_period": 10,
    "supertrend_multiplier": 3.0
  }
}
```

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `supertrend_period` | 10 | ATR period for Supertrend |
| `supertrend_multiplier` | 3.0 | ATR band multiplier (higher = wider, fewer flips) |

**Tips:**
- Period 7, Multiplier 3.0 = more signals (intraday)
- Period 10, Multiplier 3.0 = balanced (swing)
- Period 14, Multiplier 4.0 = fewer signals (positional)

**Best used with:** `quality_trend` screener, `rank_decay` allocator

---

## 6. RSI Mean Reversion (`rsi_reversal`)

**Concept:** When RSI drops below 30 (oversold), the stock has been sold too aggressively and a bounce is likely. Enter when RSI exits the oversold zone (crosses back above 30) with a trend filter (price above EMA50) to avoid catching falling knives. Exit when RSI reaches the neutral/overbought zone.

**Logic:**
- **BUY:** RSI crosses from <30 to >30 AND close > EMA50
- **SHORT:** RSI crosses from >70 to <70 AND close < EMA50
- **SELL:** RSI ≥ 65 (overbought zone) OR price drops below EMA50
- **COVER:** RSI ≤ 35 (oversold zone) OR price rises above EMA50

**Config example:**
```json
{
  "strategy": {
    "name": "rsi_reversal",
    "symbol": "WIPRO-EQ",
    "exchange": "NSE",
    "interval": "ONE_HOUR",
    "trend_ema": 50,
    "rsi_period": 14,
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "rsi_exit_long": 65,
    "rsi_exit_short": 35
  }
}
```

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `trend_ema` | 50 | EMA for trend direction filter |
| `rsi_period` | 14 | RSI lookback |
| `rsi_oversold` | 30 | Enter long when RSI crosses above this |
| `rsi_overbought` | 70 | Enter short when RSI crosses below this |
| `rsi_exit_long` | 65 | Exit long when RSI reaches here |
| `rsi_exit_short` | 35 | Exit short when RSI reaches here |

**Best used with:** `mean_reversion` screener, `equal_weight` allocator

---

## 7. Opening Range Breakout (`orb`)

**Concept:** The first N bars of the session define the Opening Range. This range captures overnight uncertainty and early price discovery. A break above the high signals bullish conviction; a break below the low signals bearish conviction. Very effective for stocks with catalysts or high pre-market interest.

**Logic:**
- Wait for first `orb_bars` candles to form the range
- `BUY` when close breaks above the OR high
- `SHORT` when close breaks below the OR low
- `SELL` if price falls back below the OR low (failed breakout)
- `COVER` if price rises back above the OR high (failed breakdown)

**Config example:**
```json
{
  "strategy": {
    "name": "orb",
    "symbol": "INFY-EQ",
    "exchange": "NSE",
    "interval": "FIVE_MINUTE",
    "orb_bars": 3,
    "orb_rsi_filter": true,
    "rsi_period": 14
  }
}
```

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `orb_bars` | 3 | Number of opening candles to form the range (3 bars × 5min = 15min range) |
| `orb_rsi_filter` | false | Add RSI > 50 filter for longs, RSI < 50 for shorts |
| `rsi_period` | 14 | RSI period (used only if orb_rsi_filter is true) |

**Tips:**
- On 5-minute charts: `orb_bars: 3` = 15-minute range, `orb_bars: 6` = 30-minute range
- On 15-minute charts: `orb_bars: 2` = 30-minute range
- Works best on high-volume days (earnings, events)
- Requires DatetimeIndex — works automatically in both live and backtest

**Best used with:** `gap_momentum` or `high_rvol` screener

---

## 8. Stochastic Crossover (`stochastic_crossover`)

**Concept:** The Stochastic Oscillator measures where price sits within its recent range. %K crossing above %D from the oversold zone (<25) signals that selling pressure is exhausted. Unlike plain RSI, stochastic crossovers give earlier signals with less lag.

**Logic:**
- **BUY:** %K crosses above %D while %K is in oversold zone AND close > EMA50
- **SHORT:** %K crosses below %D while %K is in overbought zone AND close < EMA50
- **SELL:** %K reaches extreme overbought (≥80) OR trend breaks
- **COVER:** %K reaches extreme oversold (≤20) OR trend breaks

**Config example:**
```json
{
  "strategy": {
    "name": "stochastic_crossover",
    "symbol": "ICICIBANK-EQ",
    "exchange": "NSE",
    "interval": "FIFTEEN_MINUTE",
    "trend_ema": 50,
    "stoch_k": 14,
    "stoch_d": 3,
    "stoch_oversold": 25,
    "stoch_overbought": 75,
    "stoch_exit_long": 80,
    "stoch_exit_short": 20
  }
}
```

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `trend_ema` | 50 | Trend direction filter EMA |
| `stoch_k` | 14 | %K lookback period |
| `stoch_d` | 3 | %D smoothing period |
| `stoch_oversold` | 25 | Oversold threshold |
| `stoch_overbought` | 75 | Overbought threshold |

**Best used with:** `mean_reversion` screener, `atr_based` allocator

---

## 9. Triple EMA Trend (`three_ema_trend`)

**Concept:** Requires all three EMAs (fast, mid, slow) to be stacked in the correct order before taking a trade. This eliminates entries in mixed or transitioning markets. Only enter when a fresh fast/mid crossover occurs while the full stack is aligned — the highest-quality trend entries.

**Logic:**
- **BUY:** EMA8 > EMA21 > EMA55 AND EMA8 just crossed above EMA21
- **SHORT:** EMA8 < EMA21 < EMA55 AND EMA8 just crossed below EMA21
- **SELL:** EMA8 crosses below EMA21 (stack breaks)
- **COVER:** EMA8 crosses above EMA21 (stack breaks)

**Config example:**
```json
{
  "strategy": {
    "name": "three_ema_trend",
    "symbol": "HCLTECH-EQ",
    "exchange": "NSE",
    "interval": "ONE_HOUR",
    "ema_fast": 8,
    "ema_mid": 21,
    "ema_slow": 55
  }
}
```

**Parameters:**
| Parameter | Default | Description |
|-----------|---------|-------------|
| `ema_fast` | 8 | Fast EMA (entry timing) |
| `ema_mid` | 21 | Middle EMA (crossover signal) |
| `ema_slow` | 55 | Slow EMA (trend filter) |

**Best used with:** `quality_trend` screener, `concentrated` or `rank_decay` allocator

---

## 10. Pivot Bounce (`pivot_bounce`)

**Concept:** Floor trader pivot levels (computed from prior session OHLC) are widely watched by institutional and retail participants. When price breaks above R1, former resistance becomes support — enter long. When price breaks below S1, enter short. Exit when price fails to hold and returns to the pivot.

**Pivot Formulas:**
```
Pivot (P) = (High + Low + Close) / 3   [prior session]
R1 = 2×P − Low
S1 = 2×P − High
R2 = P + (High − Low)
S2 = P − (High − Low)
```

**Config example:**
```json
{
  "strategy": {
    "name": "pivot_bounce",
    "symbol": "AXISBANK-EQ",
    "exchange": "NSE",
    "interval": "FIVE_MINUTE"
  }
}
```

**Tips:**
- Combine with standard `sl_points` / `tp_points` in the risk section for tight exits
- Best used in the first 2 hours of the session when pivot levels have maximum relevance
- Works with DatetimeIndex (intraday) or falls back to rolling 20-bar pivots

**Best used with:** `breakout` or `multi_factor` screener

---

## Risk Config Recommendations by Strategy

```json
{
  "risk": {
    "capital": 100000,
    "max_risk_pct": 5.0,
    "daily_loss_limit": 2000,
    "sl_points": 10,
    "tp_points": 20,
    "max_qty": 500,
    "max_trades_per_day": 8,
    "max_consecutive_losses": 3,
    "trailing_sl": {
      "enabled": true,
      "mode": "atr",
      "value": 1.5,
      "activation_gap": 5.0,
      "atr_period": 14
    }
  }
}
```

| Strategy | Recommended TSL mode | TSL value | Notes |
|----------|---------------------|-----------|-------|
| `supertrend` | `points` or `atr` | 1.5–2.0 | Supertrend itself is the trailing SL; TSL adds buffer |
| `ema_crossover` | `atr` | 1.5 | ATR-based TSL locks in profits on fast moves |
| `orb` | `points` | fixed | Use tight fixed SL = OR range / 2 |
| `three_ema_trend` | `pct` | 1.0–1.5% | Smooth trends allow wider TSL |
| `rsi_reversal` | disabled | — | Strategy exits on RSI level; TSL can cause early exits |
| `pivot_bounce` | `points` | medium | Exit at pivot level is sufficient |
