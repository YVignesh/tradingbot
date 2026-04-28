# Trailing Stop Loss (TSL) — Beginner's Guide

## First, understand a regular Stop Loss

You buy SBIN at **₹500**. You set a stop loss at **₹495**.

- Price goes to ₹520 → great, profit
- Price falls back to ₹495 → SELL triggered, you exit at **₹495** (loss of ₹5)

The problem: **you locked in a ₹5 loss even though the price was once ₹520.** You gave back all the profit.

A **Trailing Stop Loss** fixes this. It moves the SL *upward* as the price rises, locking in profit.

---

## Mode 1 — `points` (fixed ₹ trail)

> SL always stays exactly ₹N below the highest price seen since entry.

**Config:**
```json
"trailing_sl": {
  "mode": "points",
  "value": 5.0,
  "activation_gap": 3.0
}
```

You buy SBIN at **₹500**.

| Price moves to | What happens | Trailing SL now |
|----------------|-------------|-----------------|
| ₹501, ₹502 | Below activation gap (₹3) — TSL sleeping | ₹0 (not active yet) |
| ₹503 | Gap hit! TSL activates. Peak = ₹503, trail = ₹5 | **₹498** |
| ₹510 | New peak. TSL moves up | **₹505** |
| ₹518 | New peak. TSL moves up | **₹513** |
| ₹515 | Price falling. Peak stays at ₹518 | **₹513** (unchanged) |
| ₹513 | Price hits SL → bot places SELL | **Exit at ₹513** |

**Result:** Entered at ₹500, exited at ₹513. **Profit = ₹13**, even though the trail was only ₹5.
Without TSL you'd have exited at the static SL of ₹495 — a **₹5 loss**.

**When to use:** Simple and predictable. Good for cheap stocks (₹50–₹300) where a fixed rupee amount makes sense.

---

## Mode 2 — `pct` (percentage trail)

> SL always stays X% below the highest price seen.

**Config:**
```json
"trailing_sl": {
  "mode": "pct",
  "value": 0.5,
  "activation_gap": 0.0
}
```
*(0.5% trail, activates immediately)*

You buy **Reliance** at **₹2800** (expensive stock — a fixed ₹5 trail is meaningless here).

| Price moves to | Trail = 0.5% of peak | Trailing SL now |
|----------------|----------------------|-----------------|
| ₹2800 (entry) | 0.5% × ₹2800 = ₹14 | **₹2786** |
| ₹2850 | 0.5% × ₹2850 = ₹14.25 | **₹2835.75** |
| ₹2900 | 0.5% × ₹2900 = ₹14.50 | **₹2885.50** |
| ₹2880 | Price falling, peak stays at ₹2900 | **₹2885.50** (unchanged) |
| ₹2885 | Price hits SL → SELL | **Exit at ₹2885.50** |

**Result:** Profit = ₹85.50 on a ₹2800 stock — the trail automatically scaled with the price.

**Why pct beats points for expensive stocks:** If you used `points=5` on Reliance, the SL would be just ₹5 on a ₹2800 stock — that's 0.18%, way too tight. Any tiny normal fluctuation would stop you out immediately. Pct mode adapts.

**When to use:** When trading stocks across very different price ranges, or when you want consistent risk as a % of price.

---

## Mode 3 — `atr` (ATR-based trail — the most professional)

> SL trails by (ATR at entry × multiplier). ATR = Average True Range = how much the stock *normally* moves per bar.

**Config:**
```json
"trailing_sl": {
  "mode": "atr",
  "value": 1.5,
  "activation_gap": 5.0
}
```
*(trail = 1.5× the ATR measured at entry)*

**What is ATR?**
If SBIN moves roughly ₹8 per 5-minute candle on average (based on last 14 bars), then ATR = ₹8.

You buy SBIN at **₹500**. ATR at that moment = **₹8**. Trail = 1.5 × ₹8 = **₹12**.

| Price moves to | Activation? | Peak | Trailing SL |
|----------------|-------------|------|-------------|
| ₹501–₹504 | No (gap = ₹5, not yet) | — | ₹0 |
| ₹505 | **Yes, activated** | ₹505 | ₹505 − ₹12 = **₹493** |
| ₹515 | — | ₹515 | ₹515 − ₹12 = **₹503** |
| ₹522 | — | ₹522 | ₹522 − ₹12 = **₹510** |
| ₹511 | Falling | ₹522 | **₹510** (unchanged) |
| ₹510 | SL hit → SELL | — | **Exit at ₹510** |

**Result:** Profit = ₹10.

Now imagine SBIN is in a choppy, volatile session where ATR = ₹20.
Same trade, but trail = 1.5 × ₹20 = **₹30**. The SL is now much wider — it gives the
trade more room to breathe without stopping you out on noise.

On a calm day with ATR = ₹5, trail = ₹7.50 — tighter, because the stock isn't moving much.

**The key insight: ATR mode adapts to market conditions automatically.**
Points and pct are fixed — they don't know if the market is calm or volatile today. ATR mode does.

**When to use:** Most real trading systems use ATR. It's the right choice when you run the bot over long periods with varying market conditions.

---

## The `activation_gap` explained simply

**Without activation gap:**

You buy at ₹500. TSL starts at ₹495. Price dips to ₹495.50 in the first 2 minutes → **stopped out immediately.**

That's bad. The price just had a normal small pullback right after entry (very common in intraday). You got kicked out before the trade even had a chance.

**With `activation_gap = 3.0`:**

TSL sleeps until price reaches **₹503** (entry + ₹3). Only then does it start trailing. Small post-entry dips don't trigger it.

Think of it as: *"Don't protect me until I'm at least ₹3 in profit."*

---

## How they work in the bot

```
You enter a trade (BUY fill confirmed)
       ↓
TSL arms itself at your fill price
       ↓
Every tick from WebSocket → bot checks current price against TSL
       ↓
If price rises → TSL moves up (locks in more profit)
If price falls → TSL stays put
       ↓
Price crosses below TSL → bot generates SELL signal immediately
```

The static SL order (placed on the exchange) stays active as a hard safety floor — it protects you if the bot crashes or loses connectivity. The TSL layer sits above it in software, moving up as the trade runs in your favour.

---

## Side-by-side comparison

| | `points` | `pct` | `atr` |
|---|----------|-------|-------|
| Trail distance | Fixed ₹ amount | % of peak price | ATR × multiplier |
| Adapts to stock price? | No | Yes | Yes |
| Adapts to volatility? | No | No | **Yes** |
| Easiest to understand? | **Yes** | Medium | Needs ATR knowledge |
| Best for | Cheap stocks, beginners | Mixed price range | All conditions, long-term |

---

## Recommended starting config

```json
"trailing_sl": {
  "enabled": true,
  "mode": "points",
  "value": 5.0,
  "activation_gap": 3.0,
  "atr_period": 14
}
```

Start with `points` mode. Run a backtest and look at the `TSL` column in exit reasons. If you see too many TSL hits on otherwise profitable trades, either increase `value` (wider trail) or increase `activation_gap` (give the trade more room at entry). Once you're comfortable, switch to `atr` mode with `value=1.5` for a smarter, self-adjusting trail.
