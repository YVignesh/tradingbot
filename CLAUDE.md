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
├── config.json          # all config: bot, strategy, risk, broker, screener, notifications
├── broker/
│   ├── constants.py     # enums, endpoints, charge rates, rate limits
│   ├── session.py       # AngelSession + SessionTokens (login/refresh/logout)
│   ├── instruments.py   # InstrumentMaster — symbol↔token lookup
│   ├── orders.py        # buy/sell/limit/SL/TP/bracket/GTT helpers
│   ├── portfolio.py     # holdings, positions, P&L, margin
│   ├── market_data.py   # candles, live quotes, market open check
│   ├── websocket_feed.py# MarketFeed + OrderFeed via SmartWebSocketV2
│   └── charges.py       # calculate_charges, breakeven_price, net_pnl_after_charges
├── utils/
│   └── __init__.py      # get_logger, paise↔rupee, date helpers, AngelOneAPIError
├── indicators/          # pure TA functions: trend, momentum, volatility, volume
├── strategies/
│   ├── base.py          # BaseStrategy ABC
│   ├── directional.py   # shared long/short state machine, fills, TSL, recovery
│   ├── ema_crossover.py # EMA 9/21 crossover — bidirectional with partial fill support
│   ├── macd_rsi_trend.py# trend EMA + MACD + RSI confirmation
│   ├── vwap_pullback.py # intraday VWAP reclaim / rejection entries
│   ├── bollinger_breakout.py # squeeze breakout with volume confirmation
│   └── registry.py      # shared live/backtest strategy registry
├── risk/
│   ├── manager.py       # RiskManager — daily loss, trade count, consecutive-loss guard
│   └── trailing_sl.py   # TrailingSL — software-side TSL (points/pct/atr)
├── journal/
│   └── trade_journal.py # SQLite fills + round-trip trade persistence
├── notifications/
│   └── telegram.py      # optional Telegram alerts
├── screener/
│   ├── base.py          # BaseScreener ABC
│   ├── momentum.py      # MomentumScreener — mom5d×0.6 + vol_spike×25 − gap×0.5
│   ├── mean_reversion.py# MeanReversionScreener — oversold RSI + below SMA20 + near lower BB
│   ├── breakout.py      # BreakoutScreener — near 20d high with volume expansion
│   ├── registry.py      # SCREENERS dict + get_screener(cfg)
│   ├── universe.py      # watchlist + nifty50 universe loading
│   ├── filters.py       # base liquidity / ATR / gap filters; delegates extra_metrics/passes_filter to screener
│   ├── ranker.py        # (legacy) standalone rank_candidates — superseded by BaseScreener.rank()
│   └── scheduler.py     # once-daily symbol locking; uses screener from registry
├── allocation/
│   ├── base.py          # BaseAllocator ABC — allocate(pool, picks) → {symbol: capital}
│   ├── equal_weight.py  # pool / n — equal split
│   ├── momentum_weighted.py # proportional to screener score
│   ├── atr_based.py     # inverse-ATR weighting (low-vol stocks get more)
│   ├── kelly.py         # Kelly criterion — deploys f* × pool across symbols
│   └── registry.py      # ALLOCATORS dict + get_allocator(cfg)
├── data/cache/          # OHLCV cache + screener selections (gitignored)
└── data/journal/        # backtest text journals — one file per run (gitignored)
```

---

## Architecture & Design Principles

- **Session-centric:** All helpers take `AngelSession` as first arg. Call `session.login()` before anything.
- **Credentials via env:** `AngelSession.from_env()` reads `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_MPIN`, `ANGEL_TOTP_SECRET`. Optional: `ANGEL_PUBLIC_IP`, `ANGEL_LOCAL_IP`, `ANGEL_MAC_ADDRESS`.
- **No global state in helpers:** All order/portfolio/market functions are pure `(session, ...)`.
- **WebSocket prices in paise:** Always call `paise_to_rupees()` from `utils` before use.
- **Rate limits:** 10 order API calls/sec per exchange/segment.
- **Sessions expire midnight IST:** `_session_refresh_loop` in `main.py` proactively refreshes every 30 min.
- **ExecutionManager** (`main.py`) owns all order lifecycle: placement, tracking, retry, stale-order cancellation, and circuit-breaking. Never call broker order functions directly from the strategy loop.

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
- **Holiday calendar not implemented** — `is_market_open()` is weekday-only; no NSE holiday awareness.
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
| 16 | **Pluggable capital allocation** | ✅ Done | `allocation/` registry — `equal_weight`, `momentum_weighted`, `atr_based`, `kelly`; set via `config.json allocation.strategy` |

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
- **Retry** — `call_with_retry()` retries network/5xx errors with backoff; does NOT retry rejections
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
- Updates `token_to_runtime` in-place and restarts `MarketFeed` with new subscriptions.

### Pluggable Capital Allocation (`allocation/`)

Selected via `config.json allocation.strategy`. Called once per trading day with `(pool, picks_today)`.

| Strategy | Logic | Config keys |
|----------|-------|-------------|
| `equal_weight` | `pool / n` — same to all | — |
| `momentum_weighted` | proportional to screener score (score-shifted so all weights > 0) | — |
| `atr_based` | inverse-ATR weighting — lower volatility → more capital | — |
| `kelly` | Kelly fraction × pool, split equally across symbols | `kelly_win_rate`, `kelly_avg_win`, `kelly_avg_loss`, `kelly_max_frac`, `kelly_fraction` |

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

**Walk-forward screener:**
- `_compute_screener_selection_per_day(daily_dfs, screener_cfg, screener, ...)` returns `(selected, daily_picks)`.
- Uses the pluggable screener's `extra_metrics`, `passes_filter`, and `rank` — no hardcoded scoring.
- `daily_picks` includes score, close, ATR, screener-specific extra metrics per symbol per day.
- Entries blocked before `actual_start` even if bars exist (warmup period).

**BacktestJournal:**
- Written to `data/journal/backtest_STRATEGY_FROM_TO.txt`.
- Per day: screener table (rank, score, metrics, **per-symbol allocation**), bar-by-bar indicator snapshots (file only), trade entry/exit events (file + console), end-of-day P&L summary.
- `_indicator_snapshot(prepared, i)` reads all non-OHLCV columns — strategy-agnostic.
- Aggregate summary appended at the end of the file.

---

## Notes for Claude

- **Never suggest setting `dry_run: false`** without explicit user confirmation.
- All charge rates and enums live in `broker/constants.py` — read it, don't guess.
- `TrailingSL` uses `import logging` directly (not `utils.get_logger`) to avoid circular import via `utils → broker.constants → broker.session`.
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
