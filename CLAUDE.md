# Trading Bot — Claude Code Context

## Project Overview

**AngelOne SmartAPI Trading Bot** — modular algo trading on Indian stock market.  
Python ≥ 3.10 | Linux | `.venv/` at project root | credentials in `.env`

---

## Folder Structure

```
tradingbot/
├── main.py              # bot runner — strategy loop, ExecutionManager, feeds
├── bot_runtime.py       # upgraded live runner — multi-symbol, screener, recovery, journal
├── backtest.py          # CLI backtester entrypoint
├── backtest_runtime.py  # generic backtester for all registered strategies
├── config.json          # all config: bot, strategy, risk, broker, screener, notifications, regime_filter
├── broker/
│   ├── constants.py     # enums, endpoints, charge rates, rate limits
│   ├── session.py       # AngelSession + SessionTokens (login/refresh/logout, thread-safe token lock)
│   ├── instruments.py   # InstrumentMaster — symbol↔token lookup (cache in ~/.tradingbot/cache/)
│   ├── orders.py        # buy/sell/limit/SL/TP/bracket/GTT helpers (9 req/sec + order_status limiter)
│   ├── portfolio.py     # holdings, positions, P&L, margin
│   ├── market_data.py   # candles, live quotes, market open check, NSE holiday calendar
│   ├── websocket_feed.py# MarketFeed + OrderFeed via SmartWebSocketV2 (thread-safe _sub_lock)
│   └── charges.py       # calculate_charges, breakeven_price, net_pnl_after_charges
├── utils/
│   ├── __init__.py      # get_logger, paise↔rupee, rate limiters, AngelOneAPIError
│   ├── market_regime.py # MarketRegimeFilter — ADX + ATR% trend/chop classification
│   ├── converters.py    # (stub) future currency/unit converters
│   ├── errors.py        # (stub) future error hierarchy
│   └── logger.py        # (stub) future logger refactor
├── indicators/
│   ├── trend.py         # EMA, SMA, DEMA, TEMA, crossover, crossunder, ADX (+DI/-DI)
│   ├── momentum.py      # RSI, Stochastic, MACD, ROC
│   ├── volatility.py    # Bollinger Bands, ATR, Supertrend
│   ├── volume.py        # VWAP, OBV, volume spike
│   ├── patterns.py      # Candlestick patterns: inside bar, engulfing, hammer, doji, NR7
│   ├── divergence.py    # Bullish/bearish divergence detection (price vs oscillator)
│   └── mtf.py           # Multi-timeframe: resample OHLCV, higher-TF indicators
├── strategies/
│   ├── base.py          # BaseStrategy ABC
│   ├── directional.py   # shared long/short state machine, fills, TSL, recovery, MAE/MFE, ATR-based SL/TP
│   ├── ema_crossover.py # EMA 9/21 crossover — bidirectional with volume confirmation
│   ├── macd_rsi_trend.py# trend EMA + MACD + RSI confirmation
│   ├── vwap_pullback.py # intraday VWAP reclaim / rejection entries
│   ├── bollinger_breakout.py # squeeze breakout with volume confirmation
│   ├── supertrend.py    # Supertrend — with RSI filter + volume confirmation
│   ├── rsi_reversal.py  # RSI oversold/overbought reversal — with volume confirmation
│   ├── stochastic_crossover.py # Stochastic %K/%D crossover
│   ├── three_ema_trend.py # Triple EMA trend alignment
│   ├── orb.py           # Opening Range Breakout (requires DatetimeIndex)
│   ├── pivot_bounce.py  # Daily pivot point bounce entries (requires DatetimeIndex)
│   ├── inside_bar.py    # Inside bar breakout — NR detection + volume + RSI filter
│   ├── macd_divergence.py # MACD histogram divergence — bullish/bearish with trend filter
│   ├── gap_and_go.py    # Morning gap continuation/fill — gap% + hold bars + volume
│   └── registry.py      # shared live/backtest strategy registry (13 strategies)
├── risk/
│   ├── manager.py       # RiskManager — daily loss, trade count, consecutive-loss guard
│   └── trailing_sl.py   # TrailingSL — software-side TSL (points/pct/atr)
├── journal/
│   └── trade_journal.py # SQLite fills + completed trades (with MAE/MFE columns)
├── notifications/
│   └── telegram.py      # optional Telegram alerts
├── screener/
│   ├── base.py          # BaseScreener ABC
│   ├── momentum.py      # MomentumScreener — mom5d×0.6 + vol_spike×25 − gap×0.5
│   ├── mean_reversion.py# MeanReversionScreener — oversold RSI + below SMA20 + near lower BB
│   ├── breakout.py      # BreakoutScreener — near 20d high with volume expansion
│   ├── vcp.py           # VCP (Volatility Contraction Pattern) screener
│   ├── gap_momentum.py  # Gap + momentum screener
│   ├── high_rvol.py     # High relative volume screener
│   ├── multi_factor.py  # Multi-factor composite screener
│   ├── price_acceleration.py # Price acceleration screener
│   ├── quality_trend.py # Quality trend screener
│   ├── range_position.py# Range position screener
│   ├── relative_strength.py # Relative strength vs Nifty 50 screener
│   ├── institutional.py # Institutional activity (delivery %, volume expansion) screener
│   ├── registry.py      # SCREENERS dict + get_screener(cfg) — 12 screeners
│   ├── universe.py      # watchlist + nifty50 universe loading
│   ├── filters.py       # base liquidity / ATR / gap filters
│   ├── ranker.py        # (legacy) standalone rank_candidates
│   └── scheduler.py     # once-daily symbol locking; uses screener from registry
├── allocation/
│   ├── base.py          # BaseAllocator ABC — allocate(pool, picks) → {symbol: capital}
│   ├── equal_weight.py  # pool / n — equal split
│   ├── momentum_weighted.py # proportional to screener score
│   ├── atr_based.py     # inverse-ATR weighting (low-vol stocks get more)
│   ├── kelly.py         # Kelly criterion — deploys f* × pool across symbols
│   ├── rank_decay.py    # exponential decay by screener rank
│   ├── score_tiered.py  # tiered allocation by score bands
│   ├── concentrated.py  # concentrated allocation to top picks
│   ├── risk_parity.py   # risk parity (inverse volatility)
│   ├── min_volatility.py# minimum volatility portfolio
│   ├── volatility_targeting.py # target a fixed volatility budget
│   └── registry.py      # ALLOCATORS dict + get_allocator(cfg)
├── data/cache/          # OHLCV cache + screener selections (gitignored)
├── data/journal/        # backtest text journals — one file per run (gitignored)
├── data/ai/             # AI state: lessons, rules, day plans, audit logs (gitignored)
│   ├── lessons/         # daily lesson JSON files (YYYY-MM-DD.json)
│   ├── day_plans/       # daily AI plan JSON files
│   ├── rules.json       # promoted permanent rules (from 3+ repeated lessons)
│   └── audit/           # guardrail audit trail (YYYY-MM-DD.json)
└── ai/
    ├── __init__.py
    ├── client.py        # AIClient — multi-provider (Gemini/OpenAI/Anthropic), retry, JSON mode, thread-safe
    ├── orchestrator.py  # AIOrchestrator — 3-window coordinator (pre-market/mid-day/post-market)
    ├── prompts.py       # System prompts + dynamic prompt builders (news/lessons/rules injection)
    ├── lessons.py       # LessonStore — daily lessons persistence + rule extraction
    ├── news.py          # MarketNewsCollector — RSS feeds, economic calendar, special days
    └── guardrails.py    # GuardRail — hard limits, validation, clamping, audit logging
```

---

## Architecture & Design Principles

- **Session-centric:** All helpers take `AngelSession` as first arg. Call `session.login()` before anything.
- **Credentials via env:** `AngelSession.from_env()` reads `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_MPIN`, `ANGEL_TOTP_SECRET`. Optional: `ANGEL_PUBLIC_IP`, `ANGEL_LOCAL_IP`, `ANGEL_MAC_ADDRESS`.
- **No global state in helpers:** All order/portfolio/market functions are pure `(session, ...)`.
- **WebSocket prices in paise:** Always call `paise_to_rupees()` from `utils` before use.
- **Rate limits (from API docs):** Orders (place+modify+cancel) = **9 req/sec cumulative**; order status = 10/sec; candle data = 3/sec. All enforced by `RateLimiter` singletons in `utils/__init__.py`.
- **Thread-safe tokens:** `AngelSession._token_lock` guards all `self.tokens` reads/writes. `refresh()` does atomic swap (build new `SessionTokens` → assign under lock). `logout()` calls `_clear_credentials()` to zero-out MPIN/TOTP in memory.
- **Thread-safe WebSocket:** `MarketFeed._sub_lock` guards `_subscriptions` and `_ws` reference across subscribe/stop/connect threads.
- **Sessions expire midnight IST:** `_session_refresh_loop` in `main.py` proactively refreshes every 30 min.
- **ExecutionManager** (`bot_runtime.py`) owns all order lifecycle: placement, tracking, retry, stale-order cancellation, and circuit-breaking. Never call broker order functions directly from the strategy loop.
- **Error code classification:** `ExecutionManager._is_retryable_error()` uses AngelOne error codes (AG8001, AB1004, etc.) not just text matching.
- **Telegram bidirectional control:** `TelegramCommandHandler` (daemon thread) polls `getUpdates` for remote commands. Auth-gated to configured `chat_id`. Destructive commands (`/squareoff`, `/kill`) require `/confirm` within 60s. `/pause` blocks new entries; exits always pass.

---

## Key Dependencies

```
smartapi-python >= 1.3.5   # AngelOne SDK (SmartConnect, SmartWebSocketV2)
pyotp >= 2.9.0             # TOTP generation
requests >= 2.31.0
pandas >= 2.0.0
numpy >= 1.24.0
websocket-client >= 1.7.0  # required by smartapi-python
```

> `requirements.txt` is UTF-16 encoded — appears double-spaced when read raw.

---

## Charge Rates (AngelOne, April 2026)

Source: `broker/constants.py` → `ChargeRates`. Update that file when rates change.

| Charge | Segment | Rate |
|--------|---------|------|
| Brokerage | Equity Delivery & Intraday | min(₹20, 0.1%) per order — min ₹5 |
| Brokerage | Derivatives / Currency / Commodity | ₹20 flat per order |
| STT | Equity Delivery | 0.1% both sides |
| STT | Equity Intraday | 0.025% sell only |
| STT | Equity Futures | 0.05% sell only |
| STT | Equity Options | 0.15% on sell premium |
| Exchange txn | NSE Equity | 0.0030699% of turnover |
| Exchange txn | NSE Futures | 0.0018299% of turnover |
| Exchange txn | NSE Options | 0.03552% of premium turnover |
| SEBI fee | All | ₹10/crore (0.000010%) both sides |
| IPFT | Equity | ₹1/crore (negligible) |
| IPFT | Currency Futures | 0.00005% |
| IPFT | Currency Options | 0.002% |
| GST | All | 18% on (brokerage + exchange + SEBI + IPFT) |
| Stamp Duty | Equity Delivery | 0.015% buy side |
| Stamp Duty | Equity Intraday | 0.003% buy side |
| DP Charge | Equity Delivery sell | ₹20 + GST per scrip |

> **Note:** Equity delivery brokerage is min(₹20, 0.1%) min ₹5 — NOT zero as some sources say. AngelOne revised this effective November 2025.

---

## Known Issues / Constraints

- **Static IP whitelisting required** — AngelOne SEBI mandate (Apr 2026): order endpoints reject unwhitelisted IPs. Set `ANGEL_PUBLIC_IP`.
- **GTT = CNC/NRML only** — not for intraday. Use regular SL orders for intraday protection.
- **NSE holiday calendar** — `is_market_open()` now includes 2026 NSE holidays. Update `_NSE_HOLIDAYS` in `broker/market_data.py` annually.
- **OrderFeed gives 403** — `tns.angelone.in` rejects auth (account restriction or IP whitelist). Bot handles it cleanly with a single warning; order fills are not real-time.
- **Historical data rate limit** — AngelOne allows ~3 req/sec for candle data. Backtester adds `time.sleep(0.35)` between each symbol fetch. If 403s persist on specific symbols, those symbols may require a higher data subscription tier.
- **Capital requirements for Nifty50** — AngelOne's minimum brokerage is ₹20/order. For intraday, a round trip costs ≥₹40 regardless of position size. With 5 symbols and ₹10,000 total, each symbol gets ₹2,000 which is too small for high-priced stocks (EICHERMOT ₹5000+, MARUTI ₹12000+). Use ₹50,000–₹100,000+ for realistic multi-symbol Nifty50 backtest.

---

## Implementation Roadmap

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| 1 | **Trailing Stop Loss** | ✅ Done | `risk/trailing_sl.py` — points/pct/atr modes, activation_gap |
| 2 | **Short Selling** | ✅ Done | 4-signal model: BUY/SELL/SHORT/COVER; bidirectional TSL |
| 3 | **Charge Calculator** | ✅ Done | Full IPFT, all segments; auto segment detection in backtest |
| 4 | **Risk Limits** | ✅ Done | daily_loss_limit, max_trades_per_day, max_consecutive_losses in both live and backtest |
| 5 | **Execution Manager** | ✅ Done | `bot_runtime.py` — order tracking, partial fills, retry, circuit breaker, slippage log |
| 6 | **Partial Fill Handling** | ✅ Done | `strategies/directional.py` — scale-in (_increase_long/short), partial exit (_reduce_long/short) |
| 7 | **Trade Journal** | ✅ Done | `journal/trade_journal.py` — SQLite fills + completed trades |
| 8 | **Stock Screener** | ✅ Done | `screener/` folder; once-daily cache with watchlist / nifty50 support |
| 9 | **Multi-symbol main loop** | ✅ Done | `bot_runtime.py` — sequential runtimes, shared RiskManager, shared feeds |
| 10 | **Telegram Notifications** | ✅ Done | `notifications/telegram.py`; fills, halts, closed trades, daily summary |
| 11 | **Position Recovery** | ✅ Done | Reconciles broker open positions into strategy state on restart |
| 12 | **Day-by-day backtest** | ✅ Done | `backtest_runtime.py` — chronological multi-symbol simulation, shared pool, shared risk limits |
| 13 | **Warmup pre-fetch** | ✅ Done | Auto-fetches extra days before `--from` date so indicators are warmed up at actual start |
| 14 | **Backtest journal** | ✅ Done | `BacktestJournal` writes `data/journal/backtest_STRATEGY_FROM_TO.txt` — screener picks, bar signals, trade events, day P&L |
| 15 | **Pluggable screener strategies** | ✅ Done | `screener/` registry — `momentum`, `mean_reversion`, `breakout`; set via `config.json screener.strategy` |
| 16 | **Pluggable capital allocation** | ✅ Done | `allocation/` registry — `equal_weight`, `momentum_weighted`, `atr_based`, `kelly`, `rank_decay`, `score_tiered`, `concentrated`, `risk_parity`, `min_volatility`, `volatility_targeting` |
| 17 | **MAE/MFE tracking** | ✅ Done | Intra-trade high/low extremes tracked in `directional.py` (live: on_tick, backtest: bar loop). Stored in SQLite journal + backtest exit logs |
| 18 | **Market regime filter** | ✅ Done | `utils/market_regime.py` — ADX + ATR% classifies TRENDING/CHOPPY. Gates BUY/SHORT in both live and backtest. Config: `regime_filter` section |
| 19 | **Thread-safe session** | ✅ Done | `_token_lock` for atomic token swap; credential clearing on logout |
| 20 | **Thread-safe WebSocket** | ✅ Done | `_sub_lock` guards `_subscriptions` and `_ws` across threads |
| 21 | **Backtest slippage model** | ✅ Done | `risk.slippage_pct` (default 0.05%) — adverse adjustment on entry and exit prices |
| 22 | **ADX indicator** | ✅ Done | `indicators/trend.adx()` — Wilder-smoothed ADX with +DI/-DI |
| 23 | **NSE holiday calendar** | ✅ Done | 2026 holidays in `broker/market_data.py`; `is_nse_holiday()` + `is_market_open()` aware |
| 24 | **ATR-based dynamic SL/TP** | ✅ Done | `sl_atr_multiplier`/`tp_atr_multiplier` in risk config; `effective_sl_points()`/`effective_tp_points()` in directional.py |
| 25 | **Volume confirmation** | ✅ Done | Pluggable `volume_spike`/`volume_period` in ema_crossover, supertrend, rsi_reversal; 0 = disabled |
| 26 | **Candlestick patterns** | ✅ Done | `indicators/patterns.py` — inside bar, engulfing, hammer, doji, NR7, shooting star |
| 27 | **Divergence detection** | ✅ Done | `indicators/divergence.py` — bullish/bearish divergence (price vs oscillator swing points) |
| 28 | **Multi-timeframe indicators** | ✅ Done | `indicators/mtf.py` — resample OHLCV, higher-TF trend/RSI/EMA, forward-fill |
| 29 | **Inside Bar strategy** | ✅ Done | `strategies/inside_bar.py` — mother bar breakout + NR detection + volume + RSI filter |
| 30 | **MACD Divergence strategy** | ✅ Done | `strategies/macd_divergence.py` — histogram divergence + trend EMA filter |
| 31 | **Gap & Go strategy** | ✅ Done | `strategies/gap_and_go.py` — morning gap continuation/fill with hold bars + volume |
| 32 | **Relative Strength screener** | ✅ Done | `screener/relative_strength.py` — stock vs Nifty 50 multi-period RS |
| 33 | **Institutional screener** | ✅ Done | `screener/institutional.py` — delivery % proxy + volume expansion detection |
| 34 | **AI integration framework** | ✅ Done | `ai/` — multi-provider client, 3-window orchestrator, guardrails, news, lessons, self-improving prompts |
| 35 | **Telegram remote control** | ✅ Done | `TelegramCommandHandler` — /status, /positions, /trades, /risk, /pause, /resume, /squareoff, /kill with /confirm |

---

## Feature Design Notes

### ExecutionManager (`bot_runtime.py`)

Owns the full order lifecycle. Key behaviours:
- **Intent constants:** `ENTRY_LONG`, `EXIT_LONG`, `ENTRY_SHORT`, `EXIT_SHORT`, `STOP_LONG`, `STOP_SHORT`
- **`register_order()`** — tracks every placed order as a `TrackedOrder` dataclass
- **`process_order_update()`** — handles fills delta (partial fills), routes to `strategy.on_fill()` and `risk_mgr.record_realized_pnl()`
- **`wait_for_terminal()`** — polls order status until complete/rejected/cancelled or timeout
- **`monitor_orders()`** — called every loop tick; detects and cancels stale orders; opens circuit if exit stale
- **Circuit breaker** — opens after `max_consecutive_api_failures` (default 5); blocks new entries for `broker_circuit_cooldown_sec` (default 300s)
- **Retry** — `call_with_retry()` retries network/5xx/AB1004/AB2001 errors with backoff; does NOT retry rejections or auth errors (AG8001, AB1009, etc.)
- **LRU cleanup** — `_last_terminal_status` caps at 200 entries with FIFO eviction
- **Polling interval** — `wait_for_terminal()` uses `config.status_poll_interval_sec` (not hardcoded)
- Configurable via `config.json bot.execution` block

### Partial Fill Handling (`strategies/ema_crossover.py`)

`on_fill()` now routes 6 cases instead of 4:

| TXN | Direction | Action |
|-----|-----------|--------|
| BUY | FLAT | `_open_long` |
| BUY | LONG | `_increase_long` (weighted avg entry) |
| BUY | SHORT | `_reduce_short` (partial cover) |
| SELL | LONG | `_reduce_long` (partial exit) |
| SELL | FLAT | `_open_short` |
| SELL | SHORT | `_increase_short` (weighted avg entry) |

Position only resets to FLAT when `remaining_qty <= 0`.

### MAE/MFE Tracking (`strategies/directional.py` + `journal/trade_journal.py`)

**MAE** (Maximum Adverse Excursion): how far price moved against the position before close.  
**MFE** (Maximum Favorable Excursion): how far price moved in favour before close.

- `_active_trade` dict carries `high_since_entry` and `low_since_entry`
- **Live:** updated on every tick in `on_tick()` — captures real-time intra-trade extremes
- **Backtest:** updated on every bar's high/low before SL/TP/TSL checks
- At trade close: MAE/MFE computed from direction (LONG MAE = entry − low, SHORT MAE = high − entry)
- Stored in completed trade dict → SQLite `trades` table (`mae`/`mfe` columns, auto-migrated)
- BacktestJournal shows MAE/MFE per exit line and avg MAE/MFE in aggregate summary

**Use cases:** If avg MAE > SL distance → stops too tight. If avg MFE >> actual profit → exits too early.

### Market Regime Filter (`utils/market_regime.py`)

Classifies the broad market (Nifty 50 by default) as **TRENDING** or **CHOPPY** using:
- **ADX** < threshold (default 20) → no trend
- **ATR%** < minimum (default 0.5%) → insufficient volatility

Both conditions must be met for TRENDING. If either fails → CHOPPY → entries blocked.

```python
regime = MarketRegimeFilter(config["regime_filter"])
allowed, reason = regime.allows_entry()  # (True, "") or (False, "market regime CHOPPY ...")
```

**Live:** `update(session)` fetches index candles periodically (every `update_interval_sec`).  
**Backtest:** `update_from_df(index_df)` recomputes from prebuilt DataFrame slice up to current date.  
**Exit signals (SELL/COVER) always pass** — never block exits in a choppy market.

Config keys (`config.json regime_filter`):
```json
{
  "enabled": false,
  "index_symbol": "NIFTY", "index_exchange": "NSE", "index_token": "99926000",
  "adx_period": 14, "adx_threshold": 20.0,
  "atr_period": 14, "atr_range_min": 0.5,
  "lookback_bars": 50, "interval": "FIFTEEN_MINUTE",
  "update_interval_sec": 300
}
```

### Trailing Stop Loss (`risk/trailing_sl.py`)

```
TrailingSL(mode, value, activation_gap=0.0)
  mode          : "points" | "pct" | "atr"
  activation_gap: min profit (₹) before TSL activates
  .arm(entry_price, direction, atr=0.0)  → call once on position open
  .update(ltp)                           → returns True if SL hit (WebSocket thread)
  .simulate_bar(high, low)              → returns (hit, exit_price) for backtesting
```

ATR mode: ATR computed once at entry from last N candles and passed to `arm()` as a fixed trail distance.

### Backtest Signal Execution (`backtest.py`)

Uses `pending_entry` / `pending_exit` flags — signal fires at bar close, execution happens at next bar **open** price. More realistic than entering at the signal bar close.

Segment auto-detection via `_resolve_trade_segment()`:
- NSE/BSE + INTRADAY → `EQUITY_INTRADAY`
- NSE/BSE + DELIVERY → `EQUITY_DELIVERY`
- NFO + CE/PE symbol → `EQUITY_OPTIONS`, else `EQUITY_FUTURES`
- CDS → `CURRENCY_OPTIONS` or `CURRENCY_FUTURES`
- MCX/NCDEX → `COMMODITY_OPTIONS` or `COMMODITY_FUTURES`

Override with `charge_segment` key in `config.json strategy` or `broker` section.

### Risk Manager (`risk/manager.py`)

Thread-safe (uses `threading.Lock`). Daily state resets automatically on IST date change.

- `check_can_trade()` → `(bool, reason_str)` — call before every entry order
- `record_realized_pnl(pnl, close_round_trip)` — partial fills book P&L without consuming a trade slot; `close_round_trip=True` increments trade count and consecutive-loss streak
- `sync_from_portfolio(session)` — seeds today's realised P&L on bot restart (prevents limit reset)
- `status()` → dict snapshot for logging

### Pluggable Screener Strategies (`screener/`)

**Universe:** configurable `watchlist` in `config.json`; optional `"nifty50"` shorthand  
**Timing:** once daily with cache; pre-market window (default 09:00–09:10 IST) with startup fallback  
**Base filters** (`filters.py`): min/max price, min avg volume, min/max ATR, gap% — applied before screener strategy  
**Screener strategy** selected via `config.json screener.strategy`:

| Strategy | Filter | Score |
|----------|--------|-------|
| `momentum` | none (all pass) | `mom5d×0.6 + vol_spike×25 − gap×0.5` |
| `mean_reversion` | RSI < `rsi_threshold` (def 40), optionally below SMA20 | `(threshold−RSI)×1.5 + (−pct_from_sma20)×2 + (0.5−bb_pct_b)×20` |
| `breakout` | within `pct_near_high`% of 20d high AND vol expansion > `vol_expansion_min` | `−pct_from_high×2 + vol_expansion×15 − gap×0.5` |
| `vcp` | Volatility Contraction Pattern | contraction ratio + volume decline |
| `gap_momentum` | Gap + intraday momentum | gap% × momentum |
| `high_rvol` | High relative volume | RVOL spike detection |
| `multi_factor` | Composite multi-factor | weighted blend of trend/momentum/volume |
| `price_acceleration` | Accelerating price change | rate of change acceleration |
| `quality_trend` | Quality trend characteristics | trend strength + consistency |
| `range_position` | Position within range | proximity to support/resistance |

**`BaseScreener` ABC** (`screener/base.py`):
```python
extra_metrics(hist: pd.DataFrame) -> dict   # add RSI, BB, etc. on top of base metrics
passes_filter(metrics: dict) -> bool         # screener-specific accept/reject
score(metrics: dict) -> float                # ranking score; higher = better
rank(candidates, top_n) -> list[dict]        # default: score → sort → top-N + assign rank
```

**Live path:** `ScreenerScheduler` creates screener via `get_screener(cfg)`, passes it to `evaluate_symbol()`.  
**Backtest path:** `_compute_screener_selection_per_day` accepts screener instance; uses `extra_metrics`, `passes_filter`, `rank` for walk-forward selection.

### Daily Symbol Re-Selection (`bot_runtime.py`)

`run_strategy_loop` detects IST date changes. When `screener.enabled=true`, the `reselect_fn` callback fires once per day at `screener.run_window_start` (default 09:00 IST). The `_reselect` closure in `main()`:
- Calls `build_strategy_configs(force_screener=True)` — bypasses today's cache for a fresh run.
- Reuses existing `StrategyRuntime` for symbols still selected (preserves position state).
- Creates new `ExecutionManager` + strategy for newly selected symbols; calls `on_stop()` for removed ones.
- Updates `token_to_runtime` in-place.
- **Atomic feed swap:** starts new `MarketFeed` first; only stops old after new connects. Rolls back on failure.

### Pluggable Capital Allocation (`allocation/`)

Selected via `config.json allocation.strategy`. Called once per trading day with `(pool, picks_today)`.

| Strategy | Logic | Config keys |
|----------|-------|-------------|
| `equal_weight` | `pool / n` — same to all | — |
| `momentum_weighted` | proportional to screener score (score-shifted so all weights > 0) | — |
| `atr_based` | inverse-ATR weighting — lower volatility → more capital | — |
| `kelly` | Kelly fraction × pool, split equally across symbols | `kelly_win_rate`, `kelly_avg_win`, `kelly_avg_loss`, `kelly_max_frac`, `kelly_fraction` |
| `rank_decay` | exponential decay by screener rank | — |
| `score_tiered` | tiered allocation by score bands | — |
| `concentrated` | concentrated allocation to top picks | — |
| `risk_parity` | risk parity (inverse volatility) | — |
| `min_volatility` | minimum volatility portfolio | — |
| `volatility_targeting` | target a fixed volatility budget | — |

**Kelly math:** `f* = (b·p − q) / b` where `b = avg_win/avg_loss`. Deploy `f* × kelly_fraction × pool` in total; split equally per symbol. Cap at `kelly_max_frac`. Falls back to 10% if edge is negative.

> **Important:** Kelly with 50% win rate and avg_win/avg_loss ≈ 1.25 produces f*≈10%. Half-Kelly (kelly_fraction=0.5) deploys only 5% of the pool — Rs5,000 on Rs1L — intentionally conservative. Use `equal_weight` for full-capital deployment.

**`BaseAllocator` ABC** (`allocation/base.py`):
```python
allocate(pool: float, picks: list[dict]) -> dict[str, float]
```
Returns `{symbol: capital}`. Sum may be less than pool (Kelly under-deploys intentionally).

**Backtest integration:** `alloc_map = allocator.allocate(pool, picks_today)` per day. Per-symbol budget = `alloc_map.get(symbol, pool/n_active)`. Shown in journal screener table under Alloc column.

### Day-by-Day Backtest + Walk-Forward Screener (`backtest_runtime.py`)

**CLI — only dates are required; all settings come from `config.json`:**
```
python backtest.py --from 2026-01-01 --to 2026-03-31
```
Optional overrides: `--strategy`, `--symbols`, `--interval`, `--capital`, `--no-tsl`, `--config`

**Warmup pre-fetch:** Before fetching intraday candles, the backtester queries `strategy.required_history_bars()`, converts that to calendar days via `_BARS_PER_DAY[interval]` (with 1.5× buffer for weekends), and extends the fetch window back that many days. Bars before `actual_start` warm up indicators but never generate trades.

**Day-by-day simulation (`_run_all_day_by_day`):**
- All symbols' bars are sorted into `bars_by_day: dict[date, list]` and processed chronologically.
- On each trading day: screener selects `selected_today`; daily risk counters (loss limit, max trades, consecutive losses) are **shared** across all active symbols.
- Capital: shared `pool` (starts at `config.risk.capital`). Per-symbol budget comes from the pluggable allocator: `alloc_map = allocator.allocate(pool, picks_today)`.
- All P&L flows to/from `pool`. `sym_capital[symbol]` tracks per-symbol contribution for reporting.
- Positions are force-closed at end of each day; no intraday carry-over.
- **Slippage:** `risk.slippage_pct` (default 0.05%) applied adversely to both entry and exit prices.
- **Regime filter:** if `regime_filter.enabled`, fetches index data and blocks entries on CHOPPY days.

**Walk-forward screener:**
- `_compute_screener_selection_per_day(daily_dfs, screener_cfg, screener, ...)` returns `(selected, daily_picks)`.
- Uses the pluggable screener's `extra_metrics`, `passes_filter`, and `rank` — no hardcoded scoring.
- `daily_picks` includes score, close, ATR, screener-specific extra metrics per symbol per day.
- Entries blocked before `actual_start` even if bars exist (warmup period).

**BacktestJournal:**
- Written to `data/journal/backtest_STRATEGY_FROM_TO.txt`.
- Per day: screener table (rank, score, metrics, **per-symbol allocation**), bar-by-bar indicator snapshots (file only), trade entry/exit events (file + console), end-of-day P&L summary.
- `_indicator_snapshot(prepared, i)` reads all non-OHLCV columns — strategy-agnostic.
- Exit lines include MAE/MFE per trade; aggregate summary shows avg MAE and avg MFE.
- Aggregate summary appended at the end of the file.

### AI Integration Framework (`ai/`)

**3-Window Architecture** — 3 AI calls/day instead of per-tick:
1. **Pre-Market (08:50 IST):** Select strategy, prefer/avoid symbols, tune risk params
2. **Mid-Day (12:30 IST):** Review morning trades, adjust SL/TP multipliers, drop underperforming symbols
3. **Post-Market (15:30 IST):** Extract lessons, propose rules, assess strategy performance

**AIClient** (`ai/client.py`):
- Multi-provider: Gemini (`google-genai`), OpenAI, Anthropic — thread-safe lazy init
- `generate(prompt, system, temperature)` → str; `generate_json()` → dict (native JSON mode)
- 3-attempt exponential backoff retry for 429/503/timeout
- `sanitize_external_text()` strips prompt injection patterns from external data
- `usage_stats()` tracks call_count, input/output tokens, avg latency

**AIOrchestrator** (`ai/orchestrator.py`):
- `pre_market(screener_picks, regime_state)` → day plan dict
- `mid_day(trades_so_far, active_symbols, regime_state)` → adjustments dict
- `post_market(all_trades, regime_state)` → lessons dict (saved to `data/ai/lessons/`)
- `apply_day_plan(config, plan)` / `apply_mid_day_adjustments(config, adj)` — in-memory config mutation
- All outputs validated through `GuardRail` before application

**GuardRail** (`ai/guardrails.py`):
- Hard bounds: SL ATR (0.5–3.0), TP ATR (1.0–5.0), risk% (0.5–3.0%), trades (1–20)
- Max daily delta: SL ±0.5, TP ±1.0, risk% ±0.5 — prevents wild swings
- Forbidden fields: `dry_run`, `capital`, `api_key`, broker credentials
- Full audit trail: `data/ai/audit/YYYY-MM-DD.json`

**MarketNewsCollector** (`ai/news.py`):
- RSS feeds: ET Markets, MoneyControl via `xml.etree.ElementTree`
- Economic calendar: ForexFactory JSON (USD/INR, high/medium impact)
- Special days: RBI policy dates, F&O expiry, budget day detection
- All text sanitized via `sanitize_external_text()`

**LessonStore** (`ai/lessons.py`):
- Daily lessons: `data/ai/lessons/YYYY-MM-DD.json`
- Day plans: `data/ai/day_plans/YYYY-MM-DD.json`
- Rule extraction: patterns seen 3+ times in 30 days → `data/ai/rules.json`
- `format_recent_for_prompt()` injects last 7 days of lessons into AI prompts

**Prompts** (`ai/prompts.py`):
- `PRE_MARKET_SYSTEM` / `MID_DAY_SYSTEM` / `POST_MARKET_SYSTEM` — base system prompts
- Dynamic builders inject: news, lessons, rules, yesterday stats, screener picks, regime state
- Strict JSON output format with guardrail-compatible field names

### ATR-Based Dynamic SL/TP (`strategies/directional.py`)

- `sl_atr_multiplier` (default 0) and `tp_atr_multiplier` (default 0) in `config.json risk`
- When > 0, overrides fixed `sl_points`/`tp_points` with `ATR × multiplier`
- `effective_sl_points()` / `effective_tp_points()` check `_last_atr` computed on each bar
- ATR is **always** cached via `_cache_atr()` in `generate_signal()`, regardless of TSL mode
- `compute_qty()` uses `effective_sl_points()` for risk-based sizing

### Volume Confirmation (ema_crossover, supertrend, rsi_reversal)

- `volume_spike` (default 0 = disabled) and `volume_period` (default 20) per strategy
- Entry signals require `volume >= volume_spike × avg_volume` when enabled
- Exit signals (SELL/COVER) are **never** gated by volume — ensures positions close cleanly
- Backward compatible: existing configs with no volume keys behave identically

---

## Notes for Claude

- **Never suggest setting `dry_run: false`** without explicit user confirmation.
- All charge rates and enums live in `broker/constants.py` — read it, don't guess.
- `TrailingSL` uses `import logging` directly (not `utils.get_logger`) to avoid circular import via `utils → broker.constants → broker.session`.
- **ORB and PivotBounce** require `DatetimeIndex` — they raise `ValueError` if given integer index (no silent fallback).
- `instruments.py` cache lives in `~/.tradingbot/cache/` (not `/tmp/`).
- `get_logger()` uses `_logger_lock` to prevent duplicate handler attachment under concurrent access.
- `supertrend()` in `indicators/volatility.py` uses `math.isnan()` for NaN checks (not `a != a`).
- When updating this file: keep it short. Session history belongs in git log, not here.

---

## Session Log

| Date | Summary |
|------|---------|
| 2026-04-26 | Initial exploration, created CLAUDE.md. |
| 2026-04-26 | Reviewed all scripts; fixed 3 bugs (instruments cache path, orders auth guard, cancel_gtt payload). |
| 2026-04-28 | Fixed login 400, websocket package conflict, OrderFeed 403 retry loop. Bot running end-to-end. |
| 2026-04-28 | Restructured flat files → `broker/`, `utils/`, `indicators/`, `strategies/`, `risk/`. |
| 2026-04-28 | Implemented backtest, TSL, short selling, risk limits in backtest. |
| 2026-04-28 | User updated: charges (IPFT, corrected rates, all segments), ExecutionManager (order tracking, partial fills, retry, circuit breaker), partial fill routing in ema_crossover, pending_entry/exit in backtest, segment auto-detection, RiskManager sync_from_portfolio + status(). |
| 2026-04-28 | Finished roadmap: multi-symbol live runtime, stock screener, SQLite journal, Telegram hooks, restart recovery, and added `macd_rsi_trend`, `vwap_pullback`, `bollinger_breakout`. |
| 2026-04-28 | Updated main.py + backtest.py to clean entry points. Added daily symbol re-selection (pre-market 09:00 IST) to `bot_runtime.py` via `_reselect` closure + `reselect_fn` param. Added `--symbols`, multi-symbol run, walk-forward screener gate, and aggregate report to `backtest_runtime.py`. |
| 2026-04-29 | Rewrote backtest to day-by-day chronological simulation (shared pool, shared risk limits). Added automatic warmup pre-fetch. Added `BacktestJournal` (screener picks + scores, per-bar indicators, trade events, day P&L to `data/journal/`). Fixed HTTP 403 rate limiting with `time.sleep(0.35)` between symbol fetches. Dynamic capital sizing: `pool / n_active` (not `pool / top_n`). |
| 2026-04-29 | Added pluggable screener strategies (`screener/base.py`, `momentum`, `mean_reversion`, `breakout`, `registry`). Added pluggable capital allocation (`allocation/` — `equal_weight`, `momentum_weighted`, `atr_based`, `kelly`, `registry`). Simplified backtest CLI: only `--from`/`--to` required, all config from `config.json`. |
| 2026-04-29 | Deep code review + AngelOne API docs study. P0 fixes: thread-safe session tokens (atomic swap + `_token_lock`), thread-safe WebSocket (`_sub_lock`), rate-limit `get_order_status` (10/sec), fixed order rate to 9/sec (API docs), atomic feed swap with rollback in `_reselect`. P1: credential clearing on logout, LRU eviction for `_last_terminal_status`, configurable poll interval, `DatetimeIndex` validation in ORB/Pivot (raise instead of silent fallback), backtest slippage model (`slippage_pct`). P2: AngelOne error code classification in `_is_retryable_error`, NSE 2026 holiday calendar, instrument cache in `~/.tradingbot/cache/`, thread-safe `get_logger`, `math.isnan` in supertrend. |
| 2026-04-29 | MAE/MFE tracking: `high_since_entry`/`low_since_entry` in `directional.py` (live via `on_tick`, backtest via bar loop), computed at close, stored in SQLite (`mae`/`mfe` columns with auto-migration), shown in backtest exit logs + aggregate summary. Market regime filter: ADX indicator in `indicators/trend.py`, `utils/market_regime.py` (`MarketRegimeFilter`), integrated in `bot_runtime.py` (gates BUY/SHORT) and `backtest_runtime.py` (fetches index data, checks regime per day). Config section `regime_filter` (disabled by default). |
| 2026-04-29 | Professional review → full implementation: ATR-based dynamic SL/TP (`sl_atr_multiplier`/`tp_atr_multiplier`), volume confirmation in 3 strategies, 3 new indicators (`patterns.py`, `divergence.py`, `mtf.py`), 3 new strategies (`inside_bar`, `macd_divergence`, `gap_and_go`), 2 new screeners (`relative_strength`, `institutional`), AI integration framework (6 modules: `base`, `sentiment`, `strategy_selector`, `optimizer`, `exit_advisor`, `trade_reviewer`). Registries updated to 13 strategies + 12 screeners. Config: reduced `max_risk_pct` to 2%, enabled `regime_filter`, added `ai` section. |
| 2026-04-29 | Rewrote AI module: 6 per-tick modules → 3-window architecture (pre-market/mid-day/post-market). New: `ai/client.py` (retry, JSON mode, thread-safe), `ai/orchestrator.py` (3-window coordinator), `ai/prompts.py` (dynamic prompt builders with lesson/rule injection), `ai/lessons.py` (daily lessons + rule extraction from 3+ patterns), `ai/news.py` (RSS + economic calendar + special days), `ai/guardrails.py` (hard bounds + delta caps + audit trail). Deleted 6 old modules. Wired into `bot_runtime.py` (pre-market after screener, mid-day at 12:30, post-market in finally block). Updated `config.json` AI section. |
