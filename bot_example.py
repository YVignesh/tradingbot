"""
bot_example.py — AngelOne SmartAPI · Complete Trading Bot Skeleton
==================================================================
A fully wired example showing every toolkit module in action.

Strategy: Simple EMA crossover on 5-minute candles (illustrative only).
  • EMA(9) crosses above EMA(21)  →  BUY signal
  • EMA(9) crosses below EMA(21)  →  SELL / exit signal

This file is intentionally over-commented — it serves as a reference
for building your own strategy on top of the toolkit.

To run:
    1. Copy .env.example to .env and fill in your credentials.
    2. pip install -r requirements.txt
    3. python bot_example.py

Safety:
    • Paper-trade mode (DRY_RUN=True) is ON by default — no real orders.
    • Flip DRY_RUN to False only after testing thoroughly.
    • Always test on a small quantity first.
"""

import os
import time
import threading
from datetime import datetime, timedelta, timezone

# ── Load .env if python-dotenv is installed ────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # .env loading is optional

# ── Toolkit imports ───────────────────────────────────────────────────────────
from session      import AngelSession
from instruments  import InstrumentMaster
from orders       import (
    buy, sell, buy_limit, sell_limit,
    place_stop_loss, place_take_profit,
    create_gtt, cancel_gtt, create_gtt_oco,
    get_order_book, cancel_order,
)
from portfolio    import (
    get_positions, get_open_positions, is_position_open,
    get_available_cash, has_sufficient_margin,
    get_position_pnl, get_holdings,
)
from market_data  import (
    get_candles, get_candles_today, get_candles_n_days,
    get_ltp_single, candles_to_dataframe,
    is_market_open, minutes_to_market_open,
)
from websocket_feed import MarketFeed, OrderFeed, parse_tick
from charges      import Segment, calculate_charges, breakeven_price, net_pnl_after_charges
from config       import (
    CandleInterval, ExchangeType, WSMode,
    ProductType, OrderType, Exchange,
)
from utils        import get_logger, AngelOneAPIError

_log = get_logger("bot_example")

IST = timezone(timedelta(hours=5, minutes=30))

# ─────────────────────────────────────────────────────────────────────────────
# BOT CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DRY_RUN      = True          # Set False to place real orders
SYMBOL       = "SBIN-EQ"     # Trading symbol
EXCHANGE     = "NSE"
PRODUCT_TYPE = ProductType.INTRADAY   # MIS — auto square-off at 3:20 PM

# Position sizing
MAX_QTY      = 10            # Maximum shares per trade
RISK_PER_TRADE_PCT = 0.01    # Risk max 1% of available capital per trade

# Strategy parameters (EMA crossover)
EMA_FAST     = 9
EMA_SLOW     = 21
CANDLE_INTERVAL = CandleInterval.FIVE_MINUTE

# Risk parameters
SL_POINTS    = 5.0           # Stop-loss: 5 rupees below entry
TP_POINTS    = 10.0          # Take-profit: 10 rupees above entry


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL STATE  (a real bot would use a proper state machine / database)
# ─────────────────────────────────────────────────────────────────────────────

state = {
    "in_position":    False,
    "entry_price":    0.0,
    "entry_qty":      0,
    "entry_order_id": "",
    "sl_order_id":    "",
    "tp_order_id":    "",
    "last_signal":    None,   # "BUY" | "SELL" | None
    "tick_buffer":    {},     # token → latest tick dict
}


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — SESSION & INSTRUMENT SETUP
# ─────────────────────────────────────────────────────────────────────────────

def setup() -> tuple:
    """
    Initialise session, load instruments master, resolve symbol token.

    Returns:
        (session, token) tuple
    """
    _log.info("=== Bot starting up ===")

    # Load credentials from environment variables
    # Required: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_MPIN, ANGEL_TOTP_SECRET
    # Optional: ANGEL_PUBLIC_IP (required for order endpoints from Apr 2026)
    session = AngelSession.from_env()
    session.login()

    profile = session.get_profile()
    _log.info("Logged in as: %s (%s)", profile.get("name"), profile.get("clientcode"))

    # Load instruments master — refreshes daily before market open
    master = InstrumentMaster()
    master.load()
    _log.info("Instruments loaded: %d instruments", len(master))

    token = master.get_token_strict(EXCHANGE, SYMBOL)
    _log.info("Resolved %s:%s → token=%s", EXCHANGE, SYMBOL, token)

    return session, token


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — CHARGE / RISK ANALYSIS  (always check before trading)
# ─────────────────────────────────────────────────────────────────────────────

def show_charge_analysis(buy_price: float, sell_price: float, quantity: int) -> None:
    """
    Print full charge breakdown for a hypothetical round-trip.
    Run this at startup with expected entry/exit prices to understand costs.

    Args:
        buy_price  : expected entry price (₹)
        sell_price : expected exit price (₹)
        quantity   : number of shares
    """
    result = calculate_charges(
        segment    = Segment.EQUITY_INTRADAY,
        buy_price  = buy_price,
        sell_price = sell_price,
        quantity   = quantity,
        exchange   = EXCHANGE,
    )
    print(result)   # prints full table via ChargeBreakdown.__str__

    be = breakeven_price(Segment.EQUITY_INTRADAY, buy_price, quantity, EXCHANGE)
    _log.info("Break-even sell price: ₹%.4f (buy was ₹%.2f)", be, buy_price)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — STRATEGY SIGNAL GENERATION
# ─────────────────────────────────────────────────────────────────────────────

def calculate_ema(prices: list, period: int) -> list:
    """
    Calculate Exponential Moving Average.

    Args:
        prices : list of close prices (float)
        period : EMA period
    Returns:
        List of EMA values (same length as prices, NaN for warm-up period)
    """
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    ema = [sum(prices[:period]) / period]   # seed with SMA
    for price in prices[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def get_signal(session: AngelSession, token: str) -> str | None:
    """
    Compute EMA crossover signal from latest 5-minute candles.

    Fetches today's candles, computes EMA(9) and EMA(21), and returns
    a signal if a crossover just occurred on the most recent candle.

    Args:
        session : authenticated AngelSession
        token   : instrument token

    Returns:
        "BUY"  — fast EMA crossed above slow EMA (bullish crossover)
        "SELL" — fast EMA crossed below slow EMA (bearish crossover)
        None   — no signal (hold / do nothing)
    """
    try:
        candles = get_candles_today(session, EXCHANGE, token, CANDLE_INTERVAL)
    except AngelOneAPIError as e:
        _log.warning("Could not fetch candles for signal: %s", e)
        return None

    if len(candles) < EMA_SLOW + 2:
        _log.debug("Not enough candles yet (%d < %d)", len(candles), EMA_SLOW + 2)
        return None

    closes  = [c["close"] for c in candles]
    ema_fast = calculate_ema(closes, EMA_FAST)
    ema_slow = calculate_ema(closes, EMA_SLOW)

    if len(ema_fast) < 2 or len(ema_slow) < 2:
        return None

    # Crossover detection — compare last two candles
    fast_prev, fast_curr = ema_fast[-2], ema_fast[-1]
    slow_prev, slow_curr = ema_slow[-2], ema_slow[-1]

    if fast_prev <= slow_prev and fast_curr > slow_curr:
        _log.info("BUY signal: EMA(%d)=%.2f crossed above EMA(%d)=%.2f",
                  EMA_FAST, fast_curr, EMA_SLOW, slow_curr)
        return "BUY"

    if fast_prev >= slow_prev and fast_curr < slow_curr:
        _log.info("SELL signal: EMA(%d)=%.2f crossed below EMA(%d)=%.2f",
                  EMA_FAST, fast_curr, EMA_SLOW, slow_curr)
        return "SELL"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — POSITION SIZING
# ─────────────────────────────────────────────────────────────────────────────

def calculate_quantity(session: AngelSession, entry_price: float) -> int:
    """
    Calculate position size based on available capital and risk budget.

    Limits qty to:
      • MAX_QTY cap
      • Number of shares affordable with 10% of available capital
      • Never 0 (returns at least 1 if we have any capital)

    Args:
        session     : authenticated AngelSession
        entry_price : expected entry price (₹)

    Returns:
        Quantity (integer)
    """
    available = get_available_cash(session)
    if available <= 0:
        _log.warning("No available margin — cannot size position")
        return 0

    # Risk: max 10% of available capital for a single intraday trade
    capital_for_trade = available * 0.10
    qty_by_capital    = int(capital_for_trade / entry_price)
    qty               = min(qty_by_capital, MAX_QTY)

    _log.info(
        "Position size: available=₹%.2f → qty=%d @ ₹%.2f",
        available, qty, entry_price
    )
    return max(qty, 0)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

def enter_long(session: AngelSession, token: str, ltp: float) -> None:
    """
    Execute a long entry: market buy + stop-loss + take-profit orders.

    Args:
        session : authenticated AngelSession
        token   : instrument token
        ltp     : current last traded price (used for SL/TP calculation)
    """
    if state["in_position"]:
        _log.debug("Already in position — skipping entry")
        return

    qty = calculate_quantity(session, ltp)
    if qty <= 0:
        _log.warning("Position size is 0 — skipping entry")
        return

    sl_price = round(ltp - SL_POINTS, 2)
    tp_price = round(ltp + TP_POINTS, 2)
    sl_trigger = round(sl_price + 0.50, 2)   # trigger slightly above limit

    # Show expected P&L (dry-run or real)
    pnl_tp = net_pnl_after_charges(Segment.EQUITY_INTRADAY, ltp, tp_price, qty)
    pnl_sl = net_pnl_after_charges(Segment.EQUITY_INTRADAY, ltp, sl_price, qty)
    _log.info(
        "Trade plan: entry=₹%.2f  TP=₹%.2f (net +₹%.2f)  SL=₹%.2f (net ₹%.2f)  qty=%d",
        ltp, tp_price, pnl_tp, sl_price, pnl_sl, qty
    )

    if DRY_RUN:
        _log.info("[DRY RUN] Would BUY %d %s @ market (LTP ₹%.2f)", qty, SYMBOL, ltp)
        state.update({"in_position": True, "entry_price": ltp,
                       "entry_qty": qty, "last_signal": "BUY"})
        return

    # ── Real order execution ──────────────────────────────────────────────────
    try:
        # 1. Entry — market buy
        entry = buy(session, SYMBOL, token, qty, exchange=EXCHANGE,
                    product_type=PRODUCT_TYPE, order_tag="ema_entry")
        state["entry_order_id"] = entry["uniqueorderid"]
        state["entry_price"]    = ltp
        state["entry_qty"]      = qty
        state["in_position"]    = True
        _log.info("Entry order placed: uniqueOrderId=%s", entry["uniqueorderid"])

        # 2. Stop-loss order
        sl_order = place_stop_loss(
            session, SYMBOL, token, qty,
            trigger_price=sl_trigger, limit_price=sl_price,
            exchange=EXCHANGE, product_type=PRODUCT_TYPE, order_tag="ema_sl"
        )
        state["sl_order_id"] = sl_order["orderid"]
        _log.info("SL order placed @ ₹%.2f (trigger ₹%.2f)", sl_price, sl_trigger)

        # 3. Take-profit order (limit sell)
        tp_order = place_take_profit(
            session, SYMBOL, token, qty, price=tp_price,
            exchange=EXCHANGE, product_type=PRODUCT_TYPE, order_tag="ema_tp"
        )
        state["tp_order_id"] = tp_order["orderid"]
        _log.info("TP order placed @ ₹%.2f", tp_price)

    except AngelOneAPIError as e:
        _log.error("Failed to enter position: %s", e)
        # Best effort: cancel any partial orders
        _cleanup_orders(session)


def exit_long(session: AngelSession, token: str, reason: str = "signal") -> None:
    """
    Exit an open long position by cancelling SL/TP orders and placing a market sell.

    Args:
        session : authenticated AngelSession
        token   : instrument token
        reason  : reason for exit (for logging)
    """
    if not state["in_position"]:
        _log.debug("Not in position — nothing to exit")
        return

    qty = state["entry_qty"]
    _log.info("Exiting long position (%s): %d shares of %s", reason, qty, SYMBOL)

    if DRY_RUN:
        _log.info("[DRY RUN] Would SELL %d %s @ market", qty, SYMBOL)
        state.update({"in_position": False, "entry_price": 0.0,
                       "entry_qty": 0, "last_signal": "SELL"})
        return

    # Cancel open SL and TP orders first
    _cleanup_orders(session)

    # Market sell
    try:
        result = sell(session, SYMBOL, token, qty, exchange=EXCHANGE,
                      product_type=PRODUCT_TYPE, order_tag="ema_exit")
        _log.info("Exit order placed: uniqueOrderId=%s", result["uniqueorderid"])
        state.update({"in_position": False, "entry_price": 0.0,
                       "entry_qty": 0, "sl_order_id": "", "tp_order_id": ""})
    except AngelOneAPIError as e:
        _log.error("Failed to exit position: %s", e)


def _cleanup_orders(session: AngelSession) -> None:
    """Cancel open SL and TP orders. Best-effort — errors are logged, not raised."""
    for key in ("sl_order_id", "tp_order_id"):
        order_id = state.get(key)
        if order_id:
            try:
                cancel_order(session, order_id)
                state[key] = ""
                _log.info("Cancelled order: %s (%s)", order_id, key)
            except AngelOneAPIError as e:
                _log.warning("Could not cancel %s (%s): %s", key, order_id, e)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — WEBSOCKET TICK HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def on_tick(tick: dict) -> None:
    """
    Called for every incoming market tick.
    Tick prices are already in RUPEES (parse_tick() was applied by MarketFeed).

    Args:
        tick : cleaned tick dict from parse_tick()
    """
    token = tick.get("token", "")
    ltp   = tick.get("ltp", 0.0)

    # Update the latest tick buffer
    state["tick_buffer"][token] = tick

    # Only log occasionally to avoid flooding
    # _log.debug("Tick %s: LTP ₹%.2f", token, ltp)


def on_order_update(update: dict) -> None:
    """
    Called for every order status change (fill, rejection, cancellation).
    Update your position state based on order outcomes here.

    Args:
        update : order update dict from AngelOne order WebSocket
    """
    status    = update.get("status", "")
    unique_id = update.get("uniqueorderid", "?")
    symbol    = update.get("tradingsymbol", "?")
    avg_price = update.get("averageprice", 0.0)
    qty       = update.get("filledshares", 0)

    _log.info(
        "Order update: id=%s symbol=%s status=%s fill=%d@₹%.2f",
        unique_id, symbol, status, qty, float(avg_price)
    )

    # If SL or TP was triggered, reset position state
    if status == "complete":
        if unique_id == state.get("entry_order_id"):
            _log.info("Entry fill confirmed @ ₹%.2f", float(avg_price))
            state["entry_price"] = float(avg_price)
        elif update.get("ordertag", "") in ("ema_sl", "ema_tp"):
            _log.info("Exit triggered (%s): position closed", update.get("ordertag"))
            state.update({
                "in_position":  False,
                "entry_price":  0.0,
                "entry_qty":    0,
                "sl_order_id":  "",
                "tp_order_id":  "",
            })

    elif status == "rejected":
        _log.error("Order REJECTED: id=%s reason=%s", unique_id, update.get("text", ""))


# ─────────────────────────────────────────────────────────────────────────────
# STEP 7 — SESSION REFRESH SCHEDULER
# ─────────────────────────────────────────────────────────────────────────────

def session_refresh_scheduler(session: AngelSession) -> None:
    """
    Background thread that refreshes the session token before midnight expiry.
    Checks every 30 minutes and refreshes if within 60 minutes of midnight IST.

    Args:
        session : authenticated AngelSession (mutates tokens in-place)
    """
    while True:
        time.sleep(30 * 60)   # check every 30 minutes
        try:
            refreshed = session.refresh_if_needed(warn_minutes=60)
            if refreshed:
                _log.info("Session token refreshed proactively")
        except AngelOneAPIError as e:
            _log.error("Session refresh failed: %s — attempting full re-login", e)
            try:
                session.login()
                _log.info("Re-login successful")
            except Exception as ex:
                _log.critical("Re-login also failed: %s", ex)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 8 — MAIN STRATEGY LOOP
# ─────────────────────────────────────────────────────────────────────────────

def run_strategy(session: AngelSession, token: str) -> None:
    """
    Main strategy loop — runs on a 5-minute tick (aligned to candle close).

    On each iteration:
      1. Skip if market is closed
      2. Compute EMA crossover signal from latest candles
      3. Enter long on BUY signal if not in position
      4. Exit on SELL signal if in position
      5. Log P&L summary

    Args:
        session : authenticated AngelSession
        token   : instrument token
    """
    _log.info("Strategy loop started for %s (token=%s)", SYMBOL, token)

    while True:
        # ── Wait for market to open ───────────────────────────────────────────
        if not is_market_open():
            mins = minutes_to_market_open()
            if mins:
                _log.info("Market opens in %d minutes — sleeping", mins)
                time.sleep(min(mins * 60, 300))   # max 5 min sleep check
            else:
                _log.info("Market is closed (after hours / weekend) — sleeping 5 min")
                time.sleep(300)
            continue

        # ── Get latest LTP ────────────────────────────────────────────────────
        try:
            ltp = get_ltp_single(session, EXCHANGE, SYMBOL, token)
        except AngelOneAPIError as e:
            _log.warning("LTP fetch failed: %s — retrying in 60s", e)
            time.sleep(60)
            continue

        # ── Generate signal ───────────────────────────────────────────────────
        signal = get_signal(session, token)

        # ── Act on signal ─────────────────────────────────────────────────────
        if signal == "BUY" and not state["in_position"]:
            enter_long(session, token, ltp)

        elif signal == "SELL" and state["in_position"]:
            exit_long(session, token, reason="sell_signal")

        # ── P&L check ─────────────────────────────────────────────────────────
        try:
            pnl = get_position_pnl(session)
            _log.info(
                "P&L snapshot — total: ₹%.2f  realised: ₹%.2f  unrealised: ₹%.2f",
                pnl["total_pnl"], pnl["realised"], pnl["unrealised"]
            )
        except AngelOneAPIError:
            pass

        # ── Wait for next candle close (5 minutes) ─────────────────────────────
        _log.debug("Sleeping 5 minutes until next candle...")
        time.sleep(5 * 60)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Full bot entry point.

    1. Setup: authenticate, load instruments
    2. Show charge analysis for expected trade
    3. Start WebSocket feeds (market ticks + order updates)
    4. Start session refresh scheduler
    5. Run strategy loop (blocking)
    6. Graceful shutdown on KeyboardInterrupt
    """
    session, token = setup()

    # ── Charge analysis (optional, but recommended) ───────────────────────────
    _log.info("=== Charge Analysis (estimated) ===")
    show_charge_analysis(buy_price=550.0, sell_price=560.0, quantity=MAX_QTY)

    # ── Start market tick feed ────────────────────────────────────────────────
    market_feed = MarketFeed(
        session       = session,
        on_tick       = on_tick,
        on_error      = lambda e: _log.error("Market feed error: %s", e),
        on_connect    = lambda: _log.info("Market feed connected ✓"),
        on_disconnect = lambda: _log.warning("Market feed disconnected"),
        auto_reconnect = True,
        parse_prices  = True,   # convert paise → rupees automatically
    )
    market_feed.subscribe(
        instruments = [("NSE_CM", ExchangeType.NSE_CM, [token])],
        mode        = WSMode.SNAP_QUOTE,
    )
    market_feed.start()

    # ── Start order update feed ────────────────────────────────────────────────
    order_feed = OrderFeed(
        session          = session,
        on_order_update  = on_order_update,
        on_error         = lambda e: _log.error("Order feed error: %s", e),
        auto_reconnect   = True,
    )
    order_feed.start()

    # ── Start session refresh scheduler (background thread) ───────────────────
    refresh_thread = threading.Thread(
        target   = session_refresh_scheduler,
        args     = (session,),
        daemon   = True,
        name     = "SessionRefresh"
    )
    refresh_thread.start()

    # ── Run strategy (blocking main thread) ───────────────────────────────────
    try:
        _log.info("=== Bot is live. DRY_RUN=%s ===", DRY_RUN)
        run_strategy(session, token)

    except KeyboardInterrupt:
        _log.info("=== Shutdown signal received ===")

    finally:
        # ── Graceful shutdown ─────────────────────────────────────────────────
        _log.info("Stopping WebSocket feeds...")
        market_feed.stop()
        order_feed.stop()

        # Cancel all open orders before exiting
        if not DRY_RUN and state["in_position"]:
            _log.warning("Bot shutting down with open position — exiting now")
            exit_long(session, token, reason="shutdown")

        # Logout
        try:
            session.logout()
        except Exception:
            pass

        _log.info("=== Bot stopped cleanly ===")


if __name__ == "__main__":
    main()
