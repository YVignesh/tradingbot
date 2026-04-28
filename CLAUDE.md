# Trading Bot — Claude Code Context

## Project Overview

**AngelOne SmartAPI Trading Bot** — modular algo trading on Indian stock market.  
Python ≥ 3.10 | Linux | `.venv/` at project root | credentials in `.env`

---

## Folder Structure

```
tradingbot/
├── main.py              # bot runner (Phase 2)
├── backtest.py          # CLI backtester — fetches candles, replays signals, prints stats
├── config.json          # all config: bot, strategy, risk, broker
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
├── indicators/          # pure TA functions: trend, momentum, volatility, volume (Phase 2)
├── strategies/
│   ├── base.py          # BaseStrategy ABC
│   └── ema_crossover.py # EMA 9/21 crossover (Phase 2)
├── risk/
│   └── manager.py       # daily loss limit, position sizing, drawdown guard (Phase 2)
└── data/cache/          # OHLCV cache for backtesting (gitignored)
```

---

## Architecture & Design Principles

- **Session-centric:** All helpers take `AngelSession` as first arg. Call `session.login()` before anything.
- **Credentials via env:** `AngelSession.from_env()` reads `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_MPIN`, `ANGEL_TOTP_SECRET`. Optional: `ANGEL_PUBLIC_IP`, `ANGEL_LOCAL_IP`, `ANGEL_MAC_ADDRESS`.
- **No global state in helpers:** All order/portfolio/market functions are pure `(session, ...)`.
- **WebSocket prices in paise:** Always call `paise_to_rupees()` from `utils` before use.
- **Rate limits:** 10 order API calls/sec per exchange/segment — `order_rate_limiter` in `utils` is pre-wired.
- **Sessions expire midnight IST:** Call `session.refresh_if_needed()` around 23:30 IST.

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

| Charge | Rate |
|--------|------|
| Brokerage Delivery | ₹0 |
| Brokerage Intraday/F&O | min(₹20, 0.1%) — min ₹5 |
| STT Delivery | 0.1% both sides |
| STT Intraday | 0.025% sell only |
| GST | 18% on brokerage + exchange fees + SEBI |
| Stamp Duty Delivery | 0.015% buy |
| Stamp Duty Intraday | 0.003% buy |
| DP Charge | ₹20 + GST per scrip, sell side, delivery only |

---

## Known Issues / Constraints

- **Static IP whitelisting required** — AngelOne SEBI mandate (Apr 2026): order endpoints reject unwhitelisted IPs. Set `ANGEL_PUBLIC_IP`.
- **GTT = CNC/NRML only** — not for intraday. Use regular SL orders for intraday protection.
- **`create_gtt_oco` is pseudo-OCO** — two independent GTT rules; bot must cancel the other when one fires.
- **Holiday calendar not implemented** — `is_market_open()` is weekday-only; no NSE holiday awareness.
- **OrderFeed gives 403** — `tns.angelone.in` rejects auth (account restriction or IP whitelist). Bot handles it cleanly with a single warning; order fills are not real-time.

---

## Implementation Roadmap

Features planned in this order. Update status as each completes.

| # | Feature | Status | Notes |
|---|---------|--------|-------|
| 1 | **Trailing Stop Loss** | ✅ Done | `risk/trailing_sl.py` — software-side, tick-driven |
| 2 | **Short Selling** | ✅ Done | 4-signal model: BUY/SELL/SHORT/COVER; TSL enabled for both directions |
| 3 | **Indicators** | Pending | Complete stubs: ATR (needed by TSL), RSI, volume (needed by screener) |
| 4 | **Trade Journal** | Pending | SQLite, persist every trade; needed before multi-stock analysis |
| 5 | **Stock Screener** | Pending | `screener/` folder; pre-market once daily |
| 6 | **Multi-symbol main loop** | Pending | Sequential N-strategy instances, shared RiskManager |
| 7 | **Telegram Notifications** | Pending | `notifications/telegram.py`; fills, halts, daily P&L |
| 8 | **Position Recovery** | Pending | Reconcile open positions from broker API on bot restart |

---

## Feature Design Notes

### 1. Trailing Stop Loss (`risk/trailing_sl.py`)

**Design:** Standalone stateful class, no AngelOne dependency. Software-side only (AngelOne has no native TSL).

```
TrailingSL(mode, value, activation_gap=0.0)
  mode         : "points" | "pct" | "atr"
  value        : trail distance (₹ points, % float, or ATR multiplier)
  activation_gap: min profit (₹) before TSL activates — prevents noise stop-out at entry
  .arm(entry_price, atr=None)  → call once on position open
  .update(ltp)                 → call on every tick; returns True if SL is hit
  .current_sl                  → readable property; 0.0 if not yet armed/activated
```

**Integration points:**
- `strategies/ema_crossover.py` — arms TSL in `on_fill()` after BUY fill; calls `tsl.update(ltp)` in `on_tick()` to generate emergency SELL
- `backtest.py` — simulate TSL on bar OHLC (use bar low as worst-case for longs)
- `config.json` — new `"trailing_sl"` block under `"risk"`:
  ```json
  "trailing_sl": {
    "enabled": true,
    "mode": "points",
    "value": 5.0,
    "activation_gap": 3.0
  }
  ```

**Short-sell note:** TSL logic flips for shorts — track price trough, SL trails above. `arm()` takes a `direction` arg (`"long"` / `"short"`).

**Backtesting TSL simulation:**
- On each bar: check if low ≤ current_sl (long hit) before checking TP
- Update TSL peak using bar high (long) — conservative but correct for OHLC data

**ATR mode:** ATR value is computed from the last N candles at entry time and passed to `arm()`. The TSL does not recompute ATR on every tick — it uses the entry-time ATR as a fixed trail distance. This keeps TSL predictable and backtestable.

---

### 2. Short Selling (planned)

**Signal model:** `generate_signal()` returns `"BUY" | "SELL" | "SHORT" | "COVER" | None`  
**Position state:** `direction: Literal["LONG", "SHORT", "FLAT"]` added to `BaseStrategy`  
**AngelOne:** equity shorts require `INTRADAY` product (MIS) — already set in config.  
**SL/TP flip:** for shorts, SL = entry + sl_points, TP = entry − tp_points.

---

### 3. Stock Screener (planned)

**Folder:** `screener/` with `universe.py`, `filters.py`, `ranker.py`, `scheduler.py`  
**Universe:** configurable `watchlist` in `config.json`; optional `"nifty50"` shorthand loads bundled CSV  
**Timing:** pre-market once (9:00–9:10 AM IST); symbols locked for the day  
**Filters:** min/max price, min avg volume (20-day), min/max ATR, circuit-breaker check  
**Ranking:** momentum (5-day return) + volume spike → top-N symbols  
**Capital:** `max_concurrent_positions` + `max_total_exposure_pct` added to `config.json risk`

---

## Notes for Claude

- **Never suggest setting `DRY_RUN = False`** without explicit user confirmation.
- Enums/endpoints are all in `broker/constants.py` — read it rather than relying on this file.
- When updating this file: keep it short. Session history belongs in git log, not here.

---

## Session Log

| Date | Summary |
|------|---------|
| 2026-04-26 | Initial exploration, created CLAUDE.md. |
| 2026-04-26 | Reviewed all scripts; fixed 3 bugs (instruments cache path, orders auth guard, cancel_gtt payload). |
| 2026-04-28 | Fixed login 400, websocket package conflict, OrderFeed 403 retry loop. Bot running end-to-end. |
| 2026-04-28 | Restructured flat files → `broker/`, `utils/`, `indicators/`, `strategies/`, `risk/`. Added config.json, .gitignore, BaseStrategy ABC, stubs for Phase 2 files. |
| 2026-04-28 | Implemented `backtest.py` — 60-day chunk fetching, EMA crossover replay, SL/TP simulation, real charges, stats output. |
| 2026-04-28 | Designed roadmap: TSL, short sell, indicators, trade journal, screener, multi-symbol, notifications, position recovery. TSL implementation starting next. |
| 2026-04-28 | Implemented Short Selling: 4-signal model (BUY/SELL/SHORT/COVER), bidirectional TSL (short trails above entry), reversed SL/TP/PnL for shorts in backtest, L/S column in trade table. |
| 2026-04-28 | Implemented Trailing Stop Loss: `risk/trailing_sl.py` (points/pct/atr modes, activation_gap, simulate_bar for backtest). Integrated into `ema_crossover.py` (arm on fill, check in on_tick, priority in generate_signal) and `backtest.py` (bar simulation, --no-tsl flag). All unit tests pass. |
