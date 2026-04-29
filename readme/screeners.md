# Stock Screeners — Reference Manual

Screeners select the best symbols to trade each day. They run once before market open
(09:00–09:10 IST by default) and lock in the symbol list for the session.
Set the active screener in `config.json` under `screener.strategy`.

---

## Quick Reference

| Key | Screener | Style | Best Paired Strategy | Market Condition |
|-----|----------|-------|---------------------|-----------------|
| `momentum` | 5-Day Momentum | Momentum | `ema_crossover`, `macd_rsi_trend` | Trending bull market |
| `mean_reversion` | RSI Oversold | Mean Reversion | `rsi_reversal`, `stochastic_crossover` | Oscillating, volatile |
| `breakout` | Near 20d High + Vol | Breakout | `bollinger_breakout`, `orb` | Quiet → explosive |
| `vcp` | Volatility Contraction | Breakout Setup | `bollinger_breakout`, `supertrend` | Pre-breakout |
| `high_rvol` | Relative Volume Surge | Catalyst/News | `orb`, `vwap_pullback` | Event-driven |
| `range_position` | Near N-Day High | Trend Continuation | `three_ema_trend`, `supertrend` | Sustained uptrend |
| `price_acceleration` | Momentum Acceleration | Early Momentum | `ema_crossover`, `macd_rsi_trend` | Emerging trends |
| `multi_factor` | Composite Score | All-round | Any | Any |
| `gap_momentum` | Gap Follow-through | Catalyst | `orb`, `vwap_pullback` | High-news days |
| `quality_trend` | Clean Uptrend | Trend Quality | `three_ema_trend`, `supertrend` | Low-volatility uptrend |

---

## Common Config (applies to all screeners)

```json
{
  "screener": {
    "enabled": true,
    "strategy": "momentum",
    "watchlist": ["nifty50"],
    "default_exchange": "NSE",
    "top_n": 5,
    "lookback_days": 45,
    "min_price": 100,
    "max_price": 3000,
    "min_avg_volume": 500000,
    "min_atr": 3,
    "max_atr": 60,
    "max_gap_pct": 9.5,
    "run_window_start": "09:00",
    "run_window_end": "09:10"
  }
}
```

**Base filters** (applied before any screener strategy):
- `min_price` / `max_price` — eliminates penny stocks and very expensive stocks
- `min_avg_volume` — ensures liquidity (default 500K shares/day)
- `min_atr` / `max_atr` — controls volatility range
- `max_gap_pct` — avoids stocks that gapped too far (hard to trade)

---

## 1. Momentum Screener (`momentum`)

**Concept:** Selects stocks with the strongest 5-day price momentum and volume expansion. Momentum in stocks persists — recent winners tend to keep winning (proven by Jegadeesh & Titman, 1993). This is the simplest and most robust screener.

**Score formula:**
```
score = momentum_5d × 0.6 + volume_spike × 25 − gap_pct × 0.5
```

**Config example:**
```json
{
  "screener": {
    "strategy": "momentum",
    "top_n": 5,
    "min_avg_volume": 1000000
  }
}
```

**Best with:** `ema_crossover`, `macd_rsi_trend` — enter the momentum as it continues

---

## 2. Mean Reversion Screener (`mean_reversion`)

**Concept:** Finds stocks that have been oversold (RSI < threshold) and are now below their SMA20, potentially near the lower Bollinger Band. These are candidates for a bounce back to mean. High-risk/high-reward in downtrending markets.

**Score formula:**
```
score = (threshold − RSI) × 1.5 + (−pct_from_sma20) × 2 + (0.5 − bb_pct_b) × 20
```

**Config example:**
```json
{
  "screener": {
    "strategy": "mean_reversion",
    "rsi_threshold": 40,
    "pct_below_sma": 0,
    "top_n": 5
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `rsi_threshold` | 40 | RSI must be below this to qualify |
| `pct_below_sma` | 0 | Min % below SMA20 (0 = don't require below) |

**Best with:** `rsi_reversal`, `stochastic_crossover`

---

## 3. Breakout Screener (`breakout`)

**Concept:** Finds stocks consolidating within 3% of their 20-day high with expanding volume. These stocks are coiling for a breakout. High probability of continuation when volume confirms.

**Score formula:**
```
score = −pct_from_high × 2 + vol_expansion × 15 − gap_pct × 0.5
```

**Config example:**
```json
{
  "screener": {
    "strategy": "breakout",
    "pct_near_high": 3.0,
    "vol_expansion_min": 1.2,
    "top_n": 5
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `pct_near_high` | 3.0 | Max % below 20-day high to qualify |
| `vol_expansion_min` | 1.2 | Min 5d/20d volume ratio |

**Best with:** `bollinger_breakout`, `orb`

---

## 4. VCP Screener (`vcp`)

**Concept:** Mark Minervini's Volatility Contraction Pattern. Finds stocks in uptrends where both price range and volume are contracting — the stock is "coiling" before a significant breakout. Characteristic: successive lower highs with tighter swings.

**Filters:**
- Price must be above SMA50 (uptrend requirement)
- Current 10d ATR / 30d ATR < `vcp_vol_contraction` (volatility shrinking)
- Current 5d volume / 20d volume < `vcp_vol_dryup` (volume drying up)
- 10d range / 30d range < `vcp_range_contraction` (range compressing)

**Config example:**
```json
{
  "screener": {
    "strategy": "vcp",
    "vcp_trend_sma": 50,
    "vcp_vol_contraction": 0.75,
    "vcp_vol_dryup": 0.85,
    "vcp_range_contraction": 0.70,
    "top_n": 5
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `vcp_trend_sma` | 50 | SMA period for uptrend check |
| `vcp_vol_contraction` | 0.75 | Max ATR ratio (10d/30d) to qualify |
| `vcp_vol_dryup` | 0.85 | Max volume ratio (5d/20d) to qualify |
| `vcp_range_contraction` | 0.70 | Max range ratio (10d/30d) to qualify |

**Best with:** `bollinger_breakout`, `supertrend`

---

## 5. High Relative Volume (`high_rvol`)

**Concept:** Stocks trading at 2× or more their average daily volume are experiencing unusual interest — institutional accumulation, news, earnings, or catalyst events. High RVOL is the single best predictor of large intraday moves.

**Filters:**
- Max of today's RVOL and 5-day RVOL must exceed `rvol_min`
- Optional: minimum 5-day momentum

**Score formula:**
```
score = rvol × 20 + momentum_5d × 0.4 + today_range_pct × 2
```

**Config example:**
```json
{
  "screener": {
    "strategy": "high_rvol",
    "rvol_min": 2.0,
    "rvol_lookback": 20,
    "rvol_mom_min": 0.0,
    "top_n": 5
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `rvol_min` | 2.0 | Minimum relative volume (2× average) |
| `rvol_lookback` | 20 | Baseline volume window in days |
| `rvol_mom_min` | 0.0 | Minimum 5-day momentum to filter noise |

**Best with:** `orb`, `vwap_pullback`

---

## 6. Range Position (`range_position`)

**Concept:** Stocks trading in the top 30% of their 100-day price range are in institutional accumulation zones. They've had every chance to drop but haven't — strong hands are holding. These stocks are set up for continuation and new highs.

**Filter:** `range_pct >= range_min_pct` (default 0.70 = top 30% of range)

**Score formula:**
```
score = range_pct × 50 + volume_spike × 10 + momentum_5d × 0.3 − gap_pct × 0.5
```

**Config example:**
```json
{
  "screener": {
    "strategy": "range_position",
    "range_days": 100,
    "range_min_pct": 0.70,
    "range_vol_confirm": true,
    "top_n": 5
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `range_days` | 100 | Lookback for high/low range |
| `range_min_pct` | 0.70 | Min position in range (0.7 = top 30%) |
| `range_vol_confirm` | true | Require volume_spike > 1.0 |

**Best with:** `three_ema_trend`, `supertrend`

---

## 7. Price Acceleration (`price_acceleration`)

**Concept:** Catches stocks where momentum is not just positive but *increasing*. If a stock returned 0.3% per day on average but today returned 1.2%, it's accelerating. This often signals early institutional buying before the move becomes obvious.

**Score formula:**
```
acceleration = today_return − avg_return(N days)
score = acceleration × 25 + volume_spike × 10 + ret_3d × 1.5
```

**Config example:**
```json
{
  "screener": {
    "strategy": "price_acceleration",
    "accel_period": 10,
    "accel_min": 0.3,
    "accel_vol_min": 1.2,
    "top_n": 5
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `accel_period` | 10 | Lookback for baseline average return |
| `accel_min` | 0.3 | Minimum acceleration % to qualify |
| `accel_vol_min` | 1.2 | Minimum volume spike to confirm |

**Best with:** `ema_crossover`, `macd_rsi_trend` on 15m timeframe

---

## 8. Multi-Factor Composite (`multi_factor`)

**Concept:** No single factor is always best. This screener combines five independent factors — momentum, trend, volume, breakout proximity, and price quality — into a composite score. Each factor is normalised 0–100 and weighted by config. The most flexible and robust screener for all-weather use.

**Factors:**
1. **Momentum (0–100):** 5-day price momentum
2. **Trend (0–100):** % above/below SMA50
3. **Volume (0–100):** Volume spike vs 20d average
4. **Breakout (0–100):** Proximity to 20-day high
5. **Quality (0–100):** Low ATR% (smooth price action)

**Config example:**
```json
{
  "screener": {
    "strategy": "multi_factor",
    "mf_trend_sma": 50,
    "mf_weight_momentum": 1.0,
    "mf_weight_trend": 1.0,
    "mf_weight_volume": 1.0,
    "mf_weight_breakout": 1.0,
    "mf_weight_quality": 0.5,
    "mf_min_score": 40,
    "top_n": 5
  }
}
```

**Customising weights example (favour trend + quality):**
```json
{
  "mf_weight_momentum": 0.5,
  "mf_weight_trend": 2.0,
  "mf_weight_volume": 0.5,
  "mf_weight_breakout": 1.0,
  "mf_weight_quality": 2.0
}
```

**Best with:** Any strategy — this is the general-purpose all-weather screener

---

## 9. Gap Momentum (`gap_momentum`)

**Concept:** Stocks that gap up significantly at open AND close near the high (close_ratio > 0.5) are showing institutional conviction. The gap is being defended. These stocks typically continue higher the next session. Opposite applies for gap-down stocks.

**Close ratio:** `(close − prev_close) / (open − prev_close)` — fraction of the gap maintained at close.

**Config example:**
```json
{
  "screener": {
    "strategy": "gap_momentum",
    "gap_min_pct": 1.5,
    "gap_close_ratio": 0.5,
    "gap_direction": "up",
    "gap_vol_min": 1.5,
    "top_n": 5
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `gap_min_pct` | 1.5 | Minimum gap size % |
| `gap_close_ratio` | 0.5 | Min fraction of gap held at close |
| `gap_direction` | "both" | "up", "down", or "both" |
| `gap_vol_min` | 1.5 | Minimum volume spike |

**Best with:** `orb` (trade the continuation of yesterday's gap), `vwap_pullback`

---

## 10. Quality Trend (`quality_trend`)

**Concept:** Selects stocks in clean, consistent uptrends with low noise. Criteria: price above SMA20 above SMA50 (full bull stack), low ATR% (smooth daily moves), and at least 6 up-close days in the last 10. These stocks are the best candidates for trend-following systems.

**Filters:**
- `price > SMA20 > SMA50` (bull stack)
- `ATR/close < max_atr_pct` (smooth trend)
- `up_days_10 >= min_up_days` (consistent direction)
- `momentum_5d >= min_momentum` (still moving)

**Config example:**
```json
{
  "screener": {
    "strategy": "quality_trend",
    "qt_sma_fast": 20,
    "qt_sma_slow": 50,
    "qt_max_atr_pct": 3.0,
    "qt_min_up_days": 6,
    "qt_min_momentum": 1.0,
    "top_n": 5
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `qt_max_atr_pct` | 3.0 | Maximum daily ATR as % of price |
| `qt_min_up_days` | 6 | Min up-close days in last 10 |
| `qt_min_momentum` | 1.0 | Min 5-day momentum % |

**Best with:** `three_ema_trend`, `supertrend`, `rank_decay` allocator

---

## Screener + Strategy Pairing Guide

```
Trending bull market:
  quality_trend → three_ema_trend + rank_decay

Pre-breakout / event-driven:
  vcp or breakout → bollinger_breakout + equal_weight
  high_rvol or gap_momentum → orb + equal_weight

Mean reversion / oscillating:
  mean_reversion → rsi_reversal + atr_based

Catalyst / news flow:
  high_rvol + gap_momentum → vwap_pullback + equal_weight

All-weather:
  multi_factor → macd_rsi_trend + risk_parity
```
