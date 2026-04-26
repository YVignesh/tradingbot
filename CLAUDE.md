# Trading Bot — Project Context for Claude Code Sessions

> **How to use this file:** At the start of every new session, Claude reads this file to get full project context. At the end of each session (or after significant changes), Claude updates this file. This avoids re-spending context tokens re-discovering the codebase.

---

## Project Overview

**Name:** AngelOne SmartAPI Trading Bot Toolkit  
**Package name:** `angelone_sdk`  
**Version:** 1.0.0  
**Language:** Python ≥ 3.10 (uses `int | float` union type hints)  
**Platform:** Windows 11, `.venv` present at `.venv/`  
**Purpose:** A modular Python SDK/toolkit for building algorithmic trading bots on top of AngelOne's SmartAPI (Indian stock market broker).

---

## File Map

| File | Role |
|------|------|
| [config.py](config.py) | All constants: API endpoints, enums (order types, exchanges, intervals), charge rates (AngelOne, April 2026), rate limits |
| [session.py](session.py) | `AngelSession` + `SessionTokens` — login (TOTP), token refresh, logout, context manager |
| [instruments.py](instruments.py) | `InstrumentMaster` — downloads scrip master JSON, symbol↔token lookup |
| [orders.py](orders.py) | Order helpers: `buy`, `sell`, `buy_limit`, `sell_limit`, SL, TP, bracket, GTT, OCO |
| [portfolio.py](portfolio.py) | Holdings, positions, open P&L, RMS margin check, position conversion |
| [market_data.py](market_data.py) | Historical candles, live quotes (LTP/OHLC/FULL), market open check |
| [websocket_feed.py](websocket_feed.py) | `MarketFeed` (live ticks) + `OrderFeed` (order updates) via SmartWebSocketV2 |
| [charges.py](charges.py) | `calculate_charges`, `breakeven_price`, `net_pnl_after_charges` — full brokerage + tax breakdown |
| [utils.py](utils.py) | `get_logger`, `RateLimiter`, paise↔rupee conversion, `AngelOneAPIError`, header builder |
| [__init__.py](__init__.py) | Package init — re-exports everything for `from angelone_sdk import ...` usage |
| [bot_example.py](bot_example.py) | Full working bot skeleton: EMA(9/21) crossover on 5-min candles, DRY_RUN mode |
| [requirements.txt](requirements.txt) | Dependencies (see below) |

---

## Key Dependencies

```
smartapi-python >= 1.3.5   # AngelOne's official SDK (SmartConnect, SmartWebSocketV2)
pyotp >= 2.9.0             # TOTP 6-digit code generation from QR secret
requests >= 2.31.0         # REST API calls
pandas >= 2.0.0            # candles_to_dataframe(), indicator logic
numpy >= 1.24.0            # Array operations
websocket-client >= 1.7.0  # Required by smartapi-python WebSocket modules
```

---

## Architecture & Design Principles

- **Session-centric:** Every helper function accepts an `AngelSession` instance (not raw credentials). Always call `session.login()` first.
- **Env-var credentials (recommended):** `AngelSession.from_env()` reads `ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_MPIN`, `ANGEL_TOTP_SECRET`. Optional: `ANGEL_PUBLIC_IP`, `ANGEL_LOCAL_IP`, `ANGEL_MAC_ADDRESS`.
- **No global state in helpers:** All order/portfolio/market functions are pure functions taking `(session, ...)` args.
- **Prices in paise from WebSocket:** `websocket_feed.py` and GTT prices use paise (integer). Always call `paise_to_rupees()` from `utils.py` before use.
- **Rate limits:** 10 order API calls/sec per exchange/segment (`RateLimits` in `config.py`). `order_rate_limiter` in `utils.py` is a pre-wired `RateLimiter` instance.
- **Sessions expire at midnight IST:** Call `session.refresh_if_needed()` (or `session.refresh()`) in a scheduler around 23:30 IST.

---

## Core Enums (from `config.py`)

```python
Variety:         NORMAL, AMO, STOPLOSS, ROBO
TransactionType: BUY, SELL
OrderType:       MARKET, LIMIT, STOPLOSS_LIMIT, STOPLOSS_MARKET
ProductType:     DELIVERY, CARRYFORWARD, MARGIN, INTRADAY, BO
Duration:        DAY, IOC
Exchange:        NSE, BSE, NFO, CDS, MCX, NCDEX
CandleInterval:  ONE_MINUTE, THREE_MINUTE, FIVE_MINUTE, TEN_MINUTE,
                 FIFTEEN_MINUTE, THIRTY_MINUTE, ONE_HOUR, ONE_DAY
ExchangeType:    NSE_CM=1, NSE_FO=2, BSE_CM=3, BSE_FO=4, MCX_FO=5, CDS_FO=13  (WebSocket codes)
WSMode:          LTP=1, QUOTE=2, SNAP_QUOTE=3
GTTStatus:       NEW, ACTIVE, CANCELLED, TRIGGERED, INVALID, ...
```

---

## API Endpoints (from `config.py`)

Base URL: `https://apiconnect.angelone.in`

Key endpoint keys in `ENDPOINTS` dict:
- Auth: `login`, `refresh_token`, `profile`, `logout`, `rms`
- Orders: `place_order`, `modify_order`, `cancel_order`, `order_book`, `trade_book`, `order_status`, `ltp`, `convert_position`, `positions`
- Market: `market_data`, `candle_data`
- Portfolio: `holdings`, `all_holdings`
- GTT: `gtt_create`, `gtt_modify`, `gtt_cancel`, `gtt_details`, `gtt_list`

---

## Charge Rates Summary (AngelOne, April 2026)

| Charge | Rate |
|--------|------|
| Brokerage (Delivery) | ₹0 (zero brokerage) |
| Brokerage (Intraday/F&O) | min(₹20, 0.1%) — min ₹5 |
| STT Equity Delivery | 0.1% both sides |
| STT Equity Intraday | 0.025% sell side only |
| GST | 18% on brokerage + exchange fees + SEBI |
| Stamp Duty (Delivery) | 0.015% on buy |
| Stamp Duty (Intraday) | 0.003% on buy |
| DP Charge | ₹20 + GST per scrip, sell side, delivery only |

---

## bot_example.py — Strategy & Structure

- **Strategy:** EMA(9) / EMA(21) crossover on 5-minute candles  
  - EMA(9) crosses above EMA(21) → BUY  
  - EMA(9) crosses below EMA(21) → SELL/exit  
- **Symbol:** `SBIN-EQ` on NSE (configurable)  
- **Product:** `INTRADAY` (MIS, auto square-off 3:20 PM)  
- **DRY_RUN = True** by default — no real orders until explicitly set to `False`  
- **Risk:** 1% of capital per trade, SL = ₹5, TP = ₹10  
- **State dict** tracks `in_position`, `entry_price`, `entry_qty`, order IDs, tick buffer

---

## Session History

### Session 1 — 2026-04-26
- Initial project exploration.
- Verified full read/write access to all files.
- Created this CLAUDE.md for persistent context across sessions.
- No code changes made.

### Session 2 — 2026-04-26
- Full review of all 11 scripts. Codebase built by a previous Claude session via web-scraping of AngelOne API docs.
- **3 bugs fixed:**
  1. `instruments.py:36` — `DEFAULT_CACHE_PATH` used `/tmp/` which doesn't exist on Windows. Fixed to `Path(tempfile.gettempdir()) / "angelone_instruments.json"` so cache works cross-platform.
  2. `orders.py:62` — `_get()` had no `session.tokens` guard before accessing `.headers`; would crash with `AttributeError` on unauthenticated calls. Added the same guard that already existed in `portfolio.py`'s `_get()`.
  3. `orders.py:911` — `cancel_gtt()` accepted a `symbol` parameter but never included it in the API payload; AngelOne's cancel-GTT endpoint requires `tradingsymbol`. Added to payload.
- Everything else reviewed and confirmed correct: charge rates, EMA crossover logic, WebSocket wiring, paise/rupee conversions, rate limiter, session lifecycle, GTT create/modify, all portfolio helpers.

---

## Open Tasks / Known Issues

- **No `.env` file present** — credentials must be set as environment variables before running (`ANGEL_API_KEY`, `ANGEL_CLIENT_CODE`, `ANGEL_MPIN`, `ANGEL_TOTP_SECRET`, `ANGEL_PUBLIC_IP`).
- **Static IP whitelisting required** — AngelOne SEBI mandate (Apr 2026): order endpoints reject requests unless the public IP matches the IP whitelisted in the SmartAPI dashboard. Set `ANGEL_PUBLIC_IP` env var.
- **GTT works for CNC/NRML only** — not for intraday (MIS). Use regular SL orders for intraday protection.
- **`create_gtt_oco` is pseudo-OCO** — AngelOne has no native OCO. Two independent GTT rules are created; your bot must cancel the other when one fires (poll via `list_gtt(status=["TRIGGERED"])`).
- **Holiday calendar not implemented** — `is_market_open()` checks weekdays only; does not account for NSE exchange holidays.

---

## Notes for Claude

- Always check `DRY_RUN` flag before touching any order logic — never suggest setting it to `False` without explicit user confirmation.
- The `.venv` folder is at the project root — use it for dependency checks.
- `requirements.txt` is UTF-16 encoded (appears spaced when read) — parse carefully.
- When updating this file: add a new bullet under **Session History** with the date and a 2–3 line summary of what was done.
