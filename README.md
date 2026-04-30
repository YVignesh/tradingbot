# AngelOne SmartAPI Trading Bot

Modular algo trading bot for the Indian stock market via AngelOne SmartAPI.

## Quick Start

### 1. Prerequisites

- Python >= 3.10
- Linux (tested on Ubuntu)
- AngelOne trading account with SmartAPI enabled
- Static IP whitelisted on AngelOne (SEBI mandate)

### 2. Setup

```bash
# Clone and enter
cd tradingbot

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure

```bash
# Copy env template and fill in credentials
cp .env.example .env
# Edit .env with your AngelOne API key, client code, MPIN, TOTP secret
```

Edit `config.json` for strategy, risk, screener settings. Key sections:

| Section | Purpose |
|---------|---------|
| `bot.dry_run` | `true` = paper trading (default), `false` = live |
| `strategy.name` | Strategy to run (see strategies below) |
| `risk.*` | Capital, SL/TP, position sizing, daily limits |
| `screener.*` | Auto stock selection (optional) |
| `allocation.*` | Capital allocation across symbols |
| `regime_filter.*` | Market regime gating (optional) |
| `ai.*` | AI-assisted parameter tuning (optional) |

### 4. Run

```bash
# Dry run (default — no real orders)
python bot_runtime.py

# Backtest
python backtest.py --from 2026-01-01 --to 2026-03-31

# With custom config
python bot_runtime.py --config config.local.json
```

## Architecture

- **Session-centric:** All API calls take `AngelSession` as first argument
- **No global state:** Pure functions for orders, portfolio, market data
- **Thread-safe:** Token lock on session, subscription lock on WebSocket
- **Rate limited:** 9 orders/sec, 10 status/sec, 3 candles/sec

## Strategies (13)

| Strategy | Signal Logic |
|----------|-------------|
| `ema_crossover` | EMA 9/21 crossover + volume confirmation |
| `macd_rsi_trend` | Trend EMA + MACD + RSI confirmation |
| `supertrend` | Supertrend + RSI filter + volume |
| `vwap_pullback` | VWAP reclaim/rejection entries |
| `bollinger_breakout` | Squeeze breakout + volume |
| `rsi_reversal` | RSI oversold/overbought reversal |
| `stochastic_crossover` | Stochastic %K/%D crossover |
| `three_ema_trend` | Triple EMA trend alignment |
| `orb` | Opening Range Breakout |
| `pivot_bounce` | Daily pivot point bounce |
| `inside_bar` | Inside bar breakout + NR detection |
| `macd_divergence` | MACD histogram divergence |
| `gap_and_go` | Morning gap continuation/fill |

## Screeners (12)

`momentum`, `mean_reversion`, `breakout`, `vcp`, `gap_momentum`, `high_rvol`,
`multi_factor`, `price_acceleration`, `quality_trend`, `range_position`,
`relative_strength`, `institutional`

## Allocators (10)

`equal_weight`, `momentum_weighted`, `atr_based`, `kelly`, `rank_decay`,
`score_tiered`, `concentrated`, `risk_parity`, `min_volatility`, `volatility_targeting`

## Risk Management

- Daily loss limit — halt trading on breach
- Max trades per day — prevent overtrading
- Consecutive loss guard — cool-down after N losses
- Max drawdown kill switch — halt on cumulative loss threshold
- Trailing stop loss — points/pct/ATR modes
- ATR-based dynamic SL/TP

## AI Integration (Optional)

3-window architecture with guardrails:
1. **Pre-Market (08:50):** Strategy selection, parameter tuning
2. **Mid-Day (12:30):** Review + adjust
3. **Post-Market (15:30):** Lessons + rule extraction

Supports Gemini, OpenAI, Anthropic. All outputs pass through hard-coded guardrails
with bounds, delta caps, and audit trail.

## Project Structure

```
bot_runtime.py          — Live trading runtime
backtest.py             — Backtest CLI
config.json             — All configuration
broker/                 — Session, orders, portfolio, market data, WebSocket
strategies/             — 13 strategies + registry
indicators/             — Trend, momentum, volatility, volume
screener/               — 12 screeners + filters + universe
allocation/             — 10 capital allocators
risk/                   — Risk manager + trailing SL
journal/                — SQLite trade journal
notifications/          — Telegram alerts
ai/                     — AI integration (client, orchestrator, guardrails)
```

## Important Notes

- **Always start with `dry_run: true`** until you've validated your setup
- AngelOne requires static IP whitelisting for order endpoints
- GTT orders are CNC/NRML only (not for intraday)
- Capital of ₹50,000+ recommended for multi-symbol Nifty50 trading
- Update NSE holiday calendar in `broker/market_data.py` annually
