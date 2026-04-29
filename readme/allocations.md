# Capital Allocation Strategies — Reference Manual

Allocators decide how much capital to assign to each symbol selected by the screener.
They run once per trading day after the screener picks its symbols.
Set the active allocator in `config.json` under `allocation.strategy`.

---

## Quick Reference

| Key | Allocator | Style | Deployment | Best Use Case |
|-----|-----------|-------|------------|---------------|
| `equal_weight` | Equal Weight | Neutral | 100% | Simple, balanced portfolios |
| `momentum_weighted` | Score-proportional | Conviction | 100% | High confidence in screener ranking |
| `atr_based` | Inverse ATR Points | Risk | 100% | Mixed-price portfolios |
| `kelly` | Kelly Criterion | Mathematical | < 100% | Calibrated edge with known stats |
| `risk_parity` | Inverse ATR% | Risk Parity | 100% | Equal risk across symbols |
| `score_tiered` | Score Tiers | Tiered | 100% | Moderate conviction in ranking |
| `rank_decay` | Rank Exponential Decay | Conviction | 100% | Strong conviction in top picks |
| `volatility_targeting` | Target Vol% | Risk | < 100% | Capital preservation focus |
| `concentrated` | Top-pick focus | Conviction | 100% | Highest conviction, fewer symbols |
| `min_volatility` | Lowest-vol picks | Defensive | 100% | Risk-averse, smooth returns |

---

## Core Config Structure

```json
{
  "allocation": {
    "strategy": "equal_weight"
  }
}
```

Each allocator has its own optional parameters — add them inside the `"allocation"` block.

---

## 1. Equal Weight (`equal_weight`)

**Concept:** The simplest and most robust allocator. Pool ÷ N symbols. No assumptions about which symbol will perform best. Research consistently shows equal weight outperforms market-cap weight over long periods.

**Formula:** `capital_per_symbol = pool / n`

**Config:**
```json
{
  "allocation": {
    "strategy": "equal_weight"
  }
}
```

**When to use:**
- You don't trust the screener's ranking signal
- Portfolio has 3+ symbols
- Want simplicity and no parameter risk
- Backtesting — minimises overfitting

---

## 2. Momentum Weighted (`momentum_weighted`)

**Concept:** Allocates proportionally to screener score. The top-scoring stock gets more capital. If stock A scored 8.5 and stock B scored 4.5, A gets roughly 65% of the total capital. Assumes screener score predicts near-term returns.

**Formula:** `weight_i = (score_i − min_score + ε) / sum(shifted_scores)`

**Config:**
```json
{
  "allocation": {
    "strategy": "momentum_weighted"
  }
}
```

**When to use:**
- Screener score has strong predictive validity in backtests
- Using `momentum`, `price_acceleration`, or `multi_factor` screener

**Warning:** Can concentrate heavily in a single stock if scores are unequal. Monitor concentration.

---

## 3. ATR-Based (`atr_based`)

**Concept:** Inverse of ATR in points. Stocks with larger daily moves (high ATR) receive less capital; calmer stocks receive more. This ensures that each position contributes roughly equal point-risk to the portfolio. A stock with ATR₹50 gets half the capital of a stock with ATR₹25.

**Formula:** `weight_i = (1/ATR_i) / sum(1/ATR_j for all j)`

**Config:**
```json
{
  "allocation": {
    "strategy": "atr_based"
  }
}
```

**When to use:**
- Wide range of ATR across selected symbols (e.g. EICHERMOT ₹200 ATR vs SBIN ₹8 ATR)
- Want to avoid accidentally over-sizing in volatile names

---

## 4. Kelly Criterion (`kelly`)

**Concept:** The mathematically optimal bet size given known win rate and payoff ratio. Kelly maximises the geometric growth rate of capital. In practice, *half-Kelly* or *quarter-Kelly* is used to reduce volatility and avoid ruin from estimation error.

**Formula:**
```
f* = (b × p − q) / b
  b = avg_win / avg_loss
  p = win_rate
  q = 1 − p

Total deploy = min(f* × kelly_fraction, kelly_max_frac) × pool
Per symbol   = total_deploy / n
```

**Example:** win_rate=0.55, avg_win=₹150, avg_loss=₹100
```
b = 1.5
f* = (1.5 × 0.55 − 0.45) / 1.5 = 0.25   (25% Kelly)
Half-Kelly = 0.125 × pool = ₹12,500 on ₹1L
```

**Config:**
```json
{
  "allocation": {
    "strategy": "kelly",
    "kelly_win_rate": 0.55,
    "kelly_avg_win": 150,
    "kelly_avg_loss": 100,
    "kelly_max_frac": 0.50,
    "kelly_fraction": 0.5
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `kelly_win_rate` | 0.5 | Historical win rate (0–1) |
| `kelly_avg_win` | 100 | Average winning trade in ₹ |
| `kelly_avg_loss` | 80 | Average losing trade in ₹ (positive) |
| `kelly_max_frac` | 0.5 | Hard cap on total deployment fraction |
| `kelly_fraction` | 1.0 | Fractional Kelly (0.5 = half-Kelly) |

**When to use:**
- You have ≥50 trades of historical data to estimate parameters
- Use `kelly_fraction: 0.5` or lower in production

---

## 5. Risk Parity (`risk_parity`)

**Concept:** Each symbol contributes equal *percentage* volatility to the portfolio, using ATR/close as the daily vol proxy. Unlike `atr_based` (raw points), this is correct when symbols trade at very different price levels — a ₹500 stock with 3% daily vol and a ₹50 stock with 3% daily vol get the same capital.

**Formula:** `weight_i = (1 / vol%_i) / sum(1 / vol%_j)`
where `vol%_i = ATR_i / close_i`

**Config:**
```json
{
  "allocation": {
    "strategy": "risk_parity"
  }
}
```

**When to use:**
- Portfolio spans different price ranges (low-price vs high-price stocks)
- Want true equal-risk contribution
- Nifty50 universe with heterogeneous prices

---

## 6. Score Tiered (`score_tiered`)

**Concept:** Divides symbols into tiers by screener score. Top tier gets a multiplier above equal weight; bottom tier gets a fraction. Middle tier(s) get proportional weights. More granular than momentum_weighted but less extreme.

**Example with 6 symbols, 3 tiers (top 2 × equal, mid 1 ×, bottom 0.5 ×):**
```
Pool = ₹1,00,000 | 6 symbols | equal = ₹16,667
Top 2:    2 × ₹16,667 = ₹33,333 each
Middle 2: 1 × ₹16,667 = ₹16,667 each
Bottom 2: 0.5 × ₹16,667 = ₹8,333 each
Total: ₹33,333×2 + ₹16,667×2 + ₹8,333×2 = ₹1,16,666 → normalised to ₹1,00,000
```

**Config:**
```json
{
  "allocation": {
    "strategy": "score_tiered",
    "tier_count": 3,
    "tier_top_mult": 2.0,
    "tier_bottom_mult": 0.5
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `tier_count` | 3 | Number of tiers |
| `tier_top_mult` | 2.0 | Capital multiplier for top tier |
| `tier_bottom_mult` | 0.5 | Capital multiplier for bottom tier |

---

## 7. Rank Decay (`rank_decay`)

**Concept:** Capital decays exponentially with rank. Rank 1 gets the full base weight; rank 2 gets `decay_factor × rank1`; rank 3 gets `decay_factor² × rank1`. High decay (0.5) = very concentrated. Low decay (0.9) = nearly equal weight.

**Formula:** `weight_i = decay ^ (rank_i − 1)`

**Example with decay=0.75, 5 picks:**
```
Rank 1: 1.000 → 32% of pool
Rank 2: 0.750 → 24%
Rank 3: 0.563 → 18%
Rank 4: 0.422 → 13%
Rank 5: 0.316 → 10%
```

**Config:**
```json
{
  "allocation": {
    "strategy": "rank_decay",
    "rank_decay_factor": 0.75
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `rank_decay_factor` | 0.75 | Per-rank weight multiplier (0.5–0.95 range) |

**When to use:**
- Strong conviction in screener ranking
- Top pick must drive returns, others diversify
- Paired with `quality_trend`, `vcp`, or `breakout` screeners

---

## 8. Volatility Targeting (`volatility_targeting`)

**Concept:** Sizes each position so that your expected daily P&L volatility is a target % of the pool (e.g. 1%). If a stock has high ATR, you buy fewer shares. Portfolio total deployment may be below 100% on high-vol days — this is intentional (capital preservation).

**Formula:**
```
target_risk_per_symbol = pool × vol_target_pct / n
shares = target_risk_per_symbol / ATR
capital = shares × close
```

**Example:** pool=₹1L, target=1%, 5 symbols, RELIANCE (ATR=₹40, close=₹2400)
```
target_risk = ₹1L × 0.01 / 5 = ₹200
shares = ₹200 / ₹40 = 5 shares
capital = 5 × ₹2400 = ₹12,000
```

**Config:**
```json
{
  "allocation": {
    "strategy": "volatility_targeting",
    "vol_target_pct": 1.0,
    "vol_min_frac": 0.2
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `vol_target_pct` | 1.0 | Target portfolio daily vol as % of pool |
| `vol_min_frac` | 0.2 | Floor: min allocation as fraction of equal weight |

**When to use:**
- Capital preservation is the primary goal
- Trading during high-volatility periods (budget/Fed meetings, results season)
- Nifty50 stocks with large ATR differences

---

## 9. Concentrated (`concentrated`)

**Concept:** Allocates a fixed large percentage to the #1 pick, a medium percentage to #2, and splits the rest equally among remaining picks. Maximise returns by betting heavily on the highest-conviction pick while maintaining some diversification.

**Example:** pool=₹1L, 5 picks, top1=40%, top2=25%
```
Rank 1: ₹40,000
Rank 2: ₹25,000
Ranks 3–5: ₹35,000 / 3 = ₹11,667 each
```

**Config:**
```json
{
  "allocation": {
    "strategy": "concentrated",
    "conc_top1_pct": 0.40,
    "conc_top2_pct": 0.25,
    "conc_min_symbols": 3
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `conc_top1_pct` | 0.40 | Fraction of pool for rank-1 pick |
| `conc_top2_pct` | 0.25 | Fraction of pool for rank-2 pick |
| `conc_min_symbols` | 3 | Min picks needed; falls back to equal weight below this |

**When to use:**
- Using `vcp`, `breakout`, or `gap_momentum` screener (high-conviction individual setups)
- Running fewer than 5 symbols
- Experienced traders only — high concentration risk

---

## 10. Min Volatility (`min_volatility`)

**Concept:** From the screener's picks, select only the N least volatile symbols (by ATR/close) and allocate equally among them. Ignores the higher-volatility picks. This is the most defensive allocator — prioritises smooth, predictable positions over maximising expected return.

**Config:**
```json
{
  "allocation": {
    "strategy": "min_volatility",
    "minvol_top_n": 3,
    "minvol_equal": true
  }
}
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `minvol_top_n` | all picks | Number of lowest-vol picks to trade |
| `minvol_equal` | true | Equal weight among selected; false = inverse-vol weight |

**When to use:**
- High-volatility market environment
- Screener selects 8–10 symbols but you want to trade only the calmest 3–4
- Risk-averse or capital-preservation phase

---

## Allocation Strategy Decision Guide

```
Q: Do you want to deploy all capital?
├── YES → equal_weight, momentum_weighted, atr_based, risk_parity,
│         score_tiered, rank_decay, concentrated, min_volatility
└── NO  → kelly (deploys f* fraction), volatility_targeting (scales with vol)

Q: How much do you trust the screener's ranking?
├── Low  → equal_weight, min_volatility
├── Medium → score_tiered, rank_decay (factor=0.85+)
└── High → concentrated, rank_decay (factor=0.6), momentum_weighted

Q: What is your primary risk concern?
├── Per-symbol dollar volatility → atr_based
├── Per-symbol % volatility      → risk_parity
├── Overall portfolio volatility → volatility_targeting
└── Drawdown / ruin risk         → kelly (half-Kelly)

Q: Market environment?
├── Bull, trending    → rank_decay or concentrated
├── High volatility   → volatility_targeting or min_volatility
├── Uncertain / mixed → equal_weight or score_tiered
└── Catalyst-driven   → concentrated or rank_decay
```

---

## Pairing Examples

```json
// Conservative: quality trend + risk parity
{
  "screener": { "strategy": "quality_trend", "top_n": 5 },
  "allocation": { "strategy": "risk_parity" }
}

// Aggressive: breakout + concentrated
{
  "screener": { "strategy": "vcp", "top_n": 5 },
  "allocation": {
    "strategy": "concentrated",
    "conc_top1_pct": 0.45,
    "conc_top2_pct": 0.30
  }
}

// Balanced: multi-factor + score tiers
{
  "screener": { "strategy": "multi_factor", "top_n": 6 },
  "allocation": {
    "strategy": "score_tiered",
    "tier_count": 3,
    "tier_top_mult": 2.0,
    "tier_bottom_mult": 0.5
  }
}

// Defensive: high-vol environment
{
  "screener": { "strategy": "quality_trend", "top_n": 8 },
  "allocation": {
    "strategy": "min_volatility",
    "minvol_top_n": 3,
    "minvol_equal": true
  }
}

// Mathematical: calibrated Kelly
{
  "screener": { "strategy": "momentum", "top_n": 5 },
  "allocation": {
    "strategy": "kelly",
    "kelly_win_rate": 0.55,
    "kelly_avg_win": 200,
    "kelly_avg_loss": 120,
    "kelly_max_frac": 0.40,
    "kelly_fraction": 0.5
  }
}
```
