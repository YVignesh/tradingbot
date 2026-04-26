"""
orders.py — AngelOne SmartAPI · Order Management
=================================================
All order-related helpers: buy, sell, market/limit/SL orders,
cancel, modify, take-profit, stop-loss, GTT rules, and order status.

Every function applies the rate limiter before calling the API,
validates the response, and returns clean Python objects.

Design rules:
  • All price arguments are in RUPEES (we convert to paise for GTT internally).
  • All functions accept a `session: AngelSession` as their first argument.
  • Errors raise `AngelOneAPIError` — never return None on failure.
  • Static IP must be whitelisted for all order APIs (SEBI mandate, Apr 2026).

Dependencies:
    pip install smartapi-python requests
"""

import requests
from typing import Optional

from config import (
    BASE_URL, ENDPOINTS, REQUEST_TIMEOUT,
    Variety, TransactionType, OrderType, ProductType, Duration, Exchange,
    GTTStatus,
)
from session import AngelSession
from utils import (
    get_logger, validate_response, AngelOneAPIError,
    order_rate_limiter, rupees_to_paise,
)

_log = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# INTERNAL HTTP HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _post(session: AngelSession, endpoint_key: str, payload: dict) -> dict:
    """
    Authenticated POST with rate limiting and error handling.
    Applies the order rate limiter before each call.
    """
    order_rate_limiter.acquire()          # Respect 10 OPS SEBI limit
    url = BASE_URL + ENDPOINTS[endpoint_key]
    try:
        resp = requests.post(
            url, json=payload, headers=session.tokens.headers, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        raise AngelOneAPIError(f"HTTP {e.response.status_code} on {endpoint_key}") from e
    except requests.RequestException as e:
        raise AngelOneAPIError(f"Network error on {endpoint_key}: {e}") from e


def _get(session: AngelSession, endpoint_key: str, params: dict = None) -> dict:
    """Authenticated GET without rate limiting (read-only calls)."""
    if not session.tokens:
        raise AngelOneAPIError("Session not initialised — call session.login() first")
    url = BASE_URL + ENDPOINTS[endpoint_key]
    try:
        resp = requests.get(
            url, params=params, headers=session.tokens.headers, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        raise AngelOneAPIError(f"HTTP {e.response.status_code} on {endpoint_key}") from e
    except requests.RequestException as e:
        raise AngelOneAPIError(f"Network error on {endpoint_key}: {e}") from e


# ──────────────────────────────────────────────────────────────────────────────
# PLACE ORDER — Core Function
# ──────────────────────────────────────────────────────────────────────────────

def place_order(
    session:          AngelSession,
    symbol:           str,
    token:            str,
    transaction_type: str,
    quantity:         int,
    exchange:         str  = Exchange.NSE,
    order_type:       str  = OrderType.MARKET,
    product_type:     str  = ProductType.INTRADAY,
    variety:          str  = Variety.NORMAL,
    duration:         str  = Duration.DAY,
    price:            float = 0.0,
    trigger_price:    float = 0.0,
    squareoff:        float = 0.0,
    stoploss:         float = 0.0,
    trailing_stoploss:float = 0.0,
    disclosed_qty:    int   = 0,
    order_tag:        str   = "",
) -> dict:
    """
    Place an order on AngelOne. This is the core order function.
    All other helpers (buy, sell, buy_limit, etc.) call this internally.

    Args:
        session          : authenticated AngelSession
        symbol           : trading symbol e.g. "SBIN-EQ"
        token            : instrument token e.g. "3045"
        transaction_type : "BUY" or "SELL" (use TransactionType enum)
        quantity         : number of shares / lots
        exchange         : exchange code (default "NSE")
        order_type       : MARKET / LIMIT / STOPLOSS_LIMIT / STOPLOSS_MARKET
        product_type     : INTRADAY / DELIVERY / CARRYFORWARD / MARGIN / BO
        variety          : NORMAL / AMO / STOPLOSS / ROBO
        duration         : DAY / IOC
        price            : limit price (required for LIMIT and SL orders)
        trigger_price    : trigger price (required for SL orders)
        squareoff        : squareoff value (for ROBO bracket orders only)
        stoploss         : stoploss value (for ROBO bracket orders only)
        trailing_stoploss: trailing stop amount (for ROBO bracket orders only)
        disclosed_qty    : disclosed quantity (0 = fully disclose)
        order_tag        : optional custom tag / strategy label (max 20 chars)

    Returns:
        dict with keys:
            "orderid"      : mutable order ID (changes on modify)
            "uniqueorderid": stable order ID across lifecycle — always track this
            "script"       : trading symbol from response

    Raises:
        AngelOneAPIError on rejection or network failure

    Example:
        result = place_order(session, "SBIN-EQ", "3045", "BUY", 10,
                             order_type=OrderType.LIMIT, price=550.0)
        print(result["uniqueorderid"])
    """
    if not session.tokens:
        raise AngelOneAPIError("Session not initialised — call session.login() first")

    payload = {
        "variety":          variety,
        "tradingsymbol":    symbol,
        "symboltoken":      str(token),
        "transactiontype":  transaction_type,
        "exchange":         exchange,
        "ordertype":        order_type,
        "producttype":      product_type,
        "duration":         duration,
        "price":            str(price),
        "triggerprice":     str(trigger_price),
        "squareoff":        str(squareoff),
        "stoploss":         str(stoploss),
        "trailingStopLoss": str(trailing_stoploss),
        "quantity":         str(quantity),
        "disclosedquantity":str(disclosed_qty),
        "ordertag":         order_tag,
    }

    _log.info(
        "Placing %s %s order: %s %s @ ₹%.2f (qty=%d)",
        variety, order_type, transaction_type, symbol, price, quantity
    )
    raw  = _post(session, "place_order", payload)
    data = validate_response(raw, context="place_order")
    _log.info(
        "Order placed ✓  orderid=%s  uniqueOrderId=%s",
        data.get("orderid"), data.get("uniqueorderid")
    )
    return data


# ──────────────────────────────────────────────────────────────────────────────
# CONVENIENCE: BUY / SELL MARKET ORDERS
# ──────────────────────────────────────────────────────────────────────────────

def buy(
    session:      AngelSession,
    symbol:       str,
    token:        str,
    quantity:     int,
    exchange:     str = Exchange.NSE,
    product_type: str = ProductType.INTRADAY,
    order_tag:    str = "",
) -> dict:
    """
    Place a market BUY order.

    Args:
        session      : authenticated AngelSession
        symbol       : e.g. "SBIN-EQ"
        token        : instrument token e.g. "3045"
        quantity     : number of shares
        exchange     : default NSE
        product_type : INTRADAY / DELIVERY / CARRYFORWARD
        order_tag    : optional strategy tag
    Returns:
        Order response dict with "orderid" and "uniqueorderid"

    Example:
        result = buy(session, "SBIN-EQ", "3045", quantity=10)
    """
    return place_order(
        session, symbol, token,
        transaction_type=TransactionType.BUY,
        quantity=quantity,
        exchange=exchange,
        order_type=OrderType.MARKET,
        product_type=product_type,
        order_tag=order_tag,
    )


def sell(
    session:      AngelSession,
    symbol:       str,
    token:        str,
    quantity:     int,
    exchange:     str = Exchange.NSE,
    product_type: str = ProductType.INTRADAY,
    order_tag:    str = "",
) -> dict:
    """
    Place a market SELL order.

    Args:
        session      : authenticated AngelSession
        symbol       : e.g. "SBIN-EQ"
        token        : instrument token e.g. "3045"
        quantity     : number of shares
        exchange     : default NSE
        product_type : INTRADAY / DELIVERY / CARRYFORWARD
        order_tag    : optional strategy tag
    Returns:
        Order response dict

    Example:
        result = sell(session, "SBIN-EQ", "3045", quantity=10)
    """
    return place_order(
        session, symbol, token,
        transaction_type=TransactionType.SELL,
        quantity=quantity,
        exchange=exchange,
        order_type=OrderType.MARKET,
        product_type=product_type,
        order_tag=order_tag,
    )


def buy_limit(
    session:      AngelSession,
    symbol:       str,
    token:        str,
    quantity:     int,
    price:        float,
    exchange:     str = Exchange.NSE,
    product_type: str = ProductType.INTRADAY,
    order_tag:    str = "",
) -> dict:
    """
    Place a LIMIT BUY order at a specified price.

    Args:
        session      : authenticated AngelSession
        symbol       : e.g. "SBIN-EQ"
        token        : instrument token
        quantity     : number of shares
        price        : limit price in rupees
        exchange     : default NSE
        product_type : INTRADAY / DELIVERY / CARRYFORWARD
    Returns:
        Order response dict

    Example:
        result = buy_limit(session, "SBIN-EQ", "3045", quantity=10, price=545.50)
    """
    return place_order(
        session, symbol, token,
        transaction_type=TransactionType.BUY,
        quantity=quantity,
        exchange=exchange,
        order_type=OrderType.LIMIT,
        product_type=product_type,
        price=price,
        order_tag=order_tag,
    )


def sell_limit(
    session:      AngelSession,
    symbol:       str,
    token:        str,
    quantity:     int,
    price:        float,
    exchange:     str = Exchange.NSE,
    product_type: str = ProductType.INTRADAY,
    order_tag:    str = "",
) -> dict:
    """
    Place a LIMIT SELL order at a specified price.

    Args:
        session      : authenticated AngelSession
        symbol       : e.g. "SBIN-EQ"
        token        : instrument token
        quantity     : number of shares
        price        : limit price in rupees
    Returns:
        Order response dict

    Example:
        result = sell_limit(session, "SBIN-EQ", "3045", quantity=10, price=560.00)
    """
    return place_order(
        session, symbol, token,
        transaction_type=TransactionType.SELL,
        quantity=quantity,
        exchange=exchange,
        order_type=OrderType.LIMIT,
        product_type=product_type,
        price=price,
        order_tag=order_tag,
    )


# ──────────────────────────────────────────────────────────────────────────────
# STOP-LOSS & TAKE-PROFIT ORDERS
# ──────────────────────────────────────────────────────────────────────────────

def place_stop_loss(
    session:        AngelSession,
    symbol:         str,
    token:          str,
    quantity:       int,
    trigger_price:  float,
    limit_price:    float,
    transaction_type: str = TransactionType.SELL,
    exchange:       str   = Exchange.NSE,
    product_type:   str   = ProductType.INTRADAY,
    order_tag:      str   = "",
) -> dict:
    """
    Place a STOP-LOSS LIMIT order (SL order).

    When the trigger price is hit, the system places a limit order at
    `limit_price`. Commonly used as an exit order to cap losses.

    Args:
        session          : authenticated AngelSession
        symbol           : e.g. "SBIN-EQ"
        token            : instrument token
        quantity         : number of shares
        trigger_price    : price at which SL order activates (in ₹)
        limit_price      : price at which limit order is placed once triggered (in ₹)
                           Set slightly below trigger_price for SELL SL
        transaction_type : usually SELL for a long stop-loss; BUY for short SL
        exchange         : default NSE
        product_type     : should match the existing position's product type

    Returns:
        Order response dict

    Example:
        # Long position in SBIN at 550 — protect with SL at 540 (trigger 541)
        result = place_stop_loss(
            session, "SBIN-EQ", "3045", quantity=10,
            trigger_price=541.0, limit_price=540.0
        )
    """
    _log.info(
        "Placing STOP-LOSS: %s %s trigger=₹%.2f limit=₹%.2f qty=%d",
        transaction_type, symbol, trigger_price, limit_price, quantity
    )
    return place_order(
        session, symbol, token,
        transaction_type=transaction_type,
        quantity=quantity,
        exchange=exchange,
        order_type=OrderType.STOPLOSS_LIMIT,
        product_type=product_type,
        price=limit_price,
        trigger_price=trigger_price,
        order_tag=order_tag,
    )


def place_stop_loss_market(
    session:          AngelSession,
    symbol:           str,
    token:            str,
    quantity:         int,
    trigger_price:    float,
    transaction_type: str = TransactionType.SELL,
    exchange:         str = Exchange.NSE,
    product_type:     str = ProductType.INTRADAY,
    order_tag:        str = "",
) -> dict:
    """
    Place a STOP-LOSS MARKET order (SL-M order).

    When trigger is hit, a MARKET order is immediately placed.
    Faster fill than SL-Limit but price is not guaranteed.

    Args:
        session          : authenticated AngelSession
        symbol           : e.g. "SBIN-EQ"
        token            : instrument token
        quantity         : number of shares
        trigger_price    : price at which the market order is triggered (in ₹)
        transaction_type : SELL (for long SL) or BUY (for short SL)

    Returns:
        Order response dict

    Example:
        result = place_stop_loss_market(
            session, "SBIN-EQ", "3045", quantity=10, trigger_price=541.0
        )
    """
    return place_order(
        session, symbol, token,
        transaction_type=transaction_type,
        quantity=quantity,
        exchange=exchange,
        order_type=OrderType.STOPLOSS_MARKET,
        product_type=product_type,
        trigger_price=trigger_price,
        order_tag=order_tag,
    )


def place_take_profit(
    session:          AngelSession,
    symbol:           str,
    token:            str,
    quantity:         int,
    price:            float,
    transaction_type: str = TransactionType.SELL,
    exchange:         str = Exchange.NSE,
    product_type:     str = ProductType.INTRADAY,
    order_tag:        str = "",
) -> dict:
    """
    Place a LIMIT order as a take-profit exit.

    A take-profit is simply a limit order above the current market price
    (for a long position) that gets filled when the target is reached.

    Args:
        session          : authenticated AngelSession
        symbol           : e.g. "SBIN-EQ"
        token            : instrument token
        quantity         : number of shares to exit
        price            : target price in rupees
        transaction_type : SELL for long take-profit; BUY for short take-profit

    Returns:
        Order response dict

    Example:
        # Entered at 550 — take profit at 565
        result = place_take_profit(
            session, "SBIN-EQ", "3045", quantity=10, price=565.0
        )
    """
    _log.info(
        "Placing TAKE-PROFIT: %s %s @ ₹%.2f qty=%d",
        transaction_type, symbol, price, quantity
    )
    if transaction_type == TransactionType.SELL:
        return sell_limit(session, symbol, token, quantity, price,
                          exchange=exchange, product_type=product_type, order_tag=order_tag)
    else:
        return buy_limit(session, symbol, token, quantity, price,
                         exchange=exchange, product_type=product_type, order_tag=order_tag)


def place_bracket_order(
    session:       AngelSession,
    symbol:        str,
    token:         str,
    transaction_type: str,
    quantity:      int,
    price:         float,
    squareoff:     float,
    stoploss:      float,
    trailing_sl:   float = 0.0,
    exchange:      str   = Exchange.NSE,
    order_tag:     str   = "",
) -> dict:
    """
    Place a ROBO (Bracket) order — combines entry, take-profit, and stop-loss.

    In a bracket order, AngelOne automatically places co-leg orders for
    squareoff (take-profit) and stop-loss when the primary order fills.

    Args:
        session          : authenticated AngelSession
        symbol           : e.g. "SBIN-EQ"
        token            : instrument token
        transaction_type : BUY or SELL
        quantity         : number of shares
        price            : entry limit price in rupees
        squareoff        : points above/below entry for take-profit (NOT absolute price)
        stoploss         : points below/above entry for stop-loss (NOT absolute price)
        trailing_sl      : trailing stop-loss in points (0 = disabled)
        exchange         : default NSE

    Returns:
        Order response dict

    Example:
        # Buy at 550, TP = 550+10 = 560, SL = 550-5 = 545
        result = place_bracket_order(
            session, "SBIN-EQ", "3045", "BUY", 10,
            price=550.0, squareoff=10.0, stoploss=5.0, trailing_sl=2.0
        )
    """
    return place_order(
        session, symbol, token,
        transaction_type=transaction_type,
        quantity=quantity,
        exchange=exchange,
        order_type=OrderType.LIMIT,
        product_type=ProductType.BO,
        variety=Variety.ROBO,
        price=price,
        squareoff=squareoff,
        stoploss=stoploss,
        trailing_stoploss=trailing_sl,
        order_tag=order_tag,
    )


# ──────────────────────────────────────────────────────────────────────────────
# MODIFY & CANCEL ORDERS
# ──────────────────────────────────────────────────────────────────────────────

def modify_order(
    session:       AngelSession,
    order_id:      str,
    symbol:        str,
    token:         str,
    quantity:      int,
    price:         float,
    order_type:    str = OrderType.LIMIT,
    transaction_type: str = TransactionType.BUY,
    product_type:  str = ProductType.INTRADAY,
    exchange:      str = Exchange.NSE,
    variety:       str = Variety.NORMAL,
    duration:      str = Duration.DAY,
    trigger_price: float = 0.0,
) -> dict:
    """
    Modify an existing pending order.

    Only pending (unexecuted) orders can be modified.
    After modification, AngelOne assigns a new `orderid` — but
    `uniqueorderid` remains the same (use it for status tracking).

    Args:
        session    : authenticated AngelSession
        order_id   : the current orderid (NOT uniqueorderid) of the order
        symbol     : trading symbol
        token      : instrument token
        quantity   : new quantity
        price      : new price in rupees
        order_type : new order type
        ...

    Returns:
        Order response dict

    Example:
        result = modify_order(session, order_id="2412...", symbol="SBIN-EQ",
                              token="3045", quantity=10, price=548.0)
    """
    payload = {
        "orderid":        order_id,
        "variety":        variety,
        "tradingsymbol":  symbol,
        "symboltoken":    str(token),
        "transactiontype":transaction_type,
        "exchange":       exchange,
        "ordertype":      order_type,
        "producttype":    product_type,
        "duration":       duration,
        "price":          str(price),
        "triggerprice":   str(trigger_price),
        "quantity":       str(quantity),
    }
    _log.info("Modifying order %s → %s qty=%d price=₹%.2f", order_id, symbol, quantity, price)
    raw  = _post(session, "modify_order", payload)
    data = validate_response(raw, context="modify_order")
    _log.info("Order modified ✓ new orderid=%s", data.get("orderid"))
    return data


def cancel_order(
    session:  AngelSession,
    order_id: str,
    variety:  str = Variety.NORMAL,
) -> dict:
    """
    Cancel a pending order.

    Only orders in open/pending state can be cancelled.
    Filled or rejected orders cannot be cancelled.

    Args:
        session  : authenticated AngelSession
        order_id : the `orderid` (mutable) of the pending order
        variety  : must match the variety used when placing (default NORMAL)

    Returns:
        Response dict confirming cancellation

    Example:
        result = cancel_order(session, order_id="241201000123456")
    """
    payload = {"variety": variety, "orderid": order_id}
    _log.info("Cancelling order: %s (variety=%s)", order_id, variety)
    raw  = _post(session, "cancel_order", payload)
    data = validate_response(raw, context="cancel_order")
    _log.info("Order cancelled ✓ orderid=%s", data.get("orderid"))
    return data


# ──────────────────────────────────────────────────────────────────────────────
# ORDER BOOK & STATUS
# ──────────────────────────────────────────────────────────────────────────────

def get_order_book(session: AngelSession) -> list:
    """
    Fetch all orders placed in the current trading session.

    Returns a list of order dicts, each with:
        orderid, uniqueorderid, tradingsymbol, exchange, transactiontype,
        producttype, ordertype, quantity, filledshares, unfilledshares,
        price, averageprice, status, ordertag

    Returns:
        List of order dicts (empty list if no orders today)

    Example:
        orders = get_order_book(session)
        for o in orders:
            print(o["tradingsymbol"], o["status"])
    """
    raw  = _get(session, "order_book")
    data = validate_response(raw, context="order_book")
    return data if isinstance(data, list) else []


def get_trade_book(session: AngelSession) -> list:
    """
    Fetch all executed trades for the current trading session.

    Returns:
        List of trade dicts with fill details (symbol, qty, price, exchange, etc.)
    """
    raw  = _get(session, "trade_book")
    data = validate_response(raw, context="trade_book")
    return data if isinstance(data, list) else []


def get_order_status(session: AngelSession, unique_order_id: str) -> dict:
    """
    Fetch the status of a single order by its unique order ID.

    Use `uniqueorderid` (stable) not `orderid` (changes on modify).
    Rate limit: 10 requests/second.

    Args:
        session         : authenticated AngelSession
        unique_order_id : stable uniqueorderid from place_order response

    Returns:
        Order status dict with full lifecycle history

    Example:
        status = get_order_status(session, "abc-def-ghi-jkl")
        print(status["status"])   # "complete", "open", "rejected", ...
    """
    url = BASE_URL + ENDPOINTS["order_status"] + f"/{unique_order_id}"
    try:
        resp = requests.get(
            url, headers=session.tokens.headers, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        return validate_response(resp.json(), context="order_status")
    except requests.RequestException as e:
        raise AngelOneAPIError(f"Network error fetching order status: {e}") from e


def get_ltp(session: AngelSession, exchange: str, symbol: str, token: str) -> dict:
    """
    Get the last traded price (LTP) and OHLC for a symbol.

    Args:
        session  : authenticated AngelSession
        exchange : e.g. "NSE"
        symbol   : e.g. "SBIN-EQ"
        token    : instrument token e.g. "3045"

    Returns:
        Dict with keys: exchange, tradingsymbol, symboltoken, open, high,
                        low, close, ltp (all prices in rupees as floats)

    Example:
        ltp_data = get_ltp(session, "NSE", "SBIN-EQ", "3045")
        print(f"LTP: ₹{ltp_data['ltp']}")
    """
    payload = {"exchange": exchange, "tradingsymbol": symbol, "symboltoken": str(token)}
    raw  = _post(session, "ltp", payload)
    data = validate_response(raw, context="get_ltp")
    return data


# ──────────────────────────────────────────────────────────────────────────────
# GTT (GOOD TILL TRIGGERED) RULES
# ──────────────────────────────────────────────────────────────────────────────

def create_gtt(
    session:          AngelSession,
    symbol:           str,
    token:            str,
    exchange:         str,
    transaction_type: str,
    trigger_price:    float,
    limit_price:      float,
    quantity:         int,
    product_type:     str = ProductType.DELIVERY,
    time_period:      int = 365,
) -> int:
    """
    Create a GTT (Good Till Triggered) rule.

    GTT orders remain active for up to 365 days (default) and trigger
    an exchange order when the `trigger_price` is touched.
    Useful for: long-term target exits, long-term SL protection.

    ⚠ GTT does NOT replace an intraday SL order — it works only on CNC/NRML.
    ⚠ Prices are internally converted from rupees → paise automatically.

    Args:
        session          : authenticated AngelSession
        symbol           : trading symbol e.g. "RELIANCE-EQ"
        token            : instrument token
        exchange         : exchange code
        transaction_type : "BUY" or "SELL"
        trigger_price    : price that activates the rule (in ₹)
        limit_price      : price at which the order is placed once triggered (in ₹)
        quantity         : number of shares
        product_type     : DELIVERY (CNC) or CARRYFORWARD (NRML) — not INTRADAY
        time_period      : validity in days (default 365 = maximum)

    Returns:
        GTT rule ID (integer) — save this to modify/cancel the rule later

    Example:
        # Auto-sell RELIANCE if it drops below 2300
        rule_id = create_gtt(
            session, "RELIANCE-EQ", "2885", "NSE", "SELL",
            trigger_price=2300.0, limit_price=2290.0, quantity=5
        )
    """
    payload = {
        "tradingsymbol":   symbol,
        "symboltoken":     str(token),
        "exchange":        exchange,
        "producttype":     product_type,
        "transactiontype": transaction_type,
        "price":           rupees_to_paise(limit_price),
        "qty":             quantity,
        "disclosedqty":    quantity,
        "triggerprice":    rupees_to_paise(trigger_price),
        "timeperiod":      time_period,
    }
    _log.info(
        "Creating GTT: %s %s trigger=₹%.2f limit=₹%.2f qty=%d valid=%dd",
        transaction_type, symbol, trigger_price, limit_price, quantity, time_period
    )
    raw  = _post(session, "gtt_create", payload)
    data = validate_response(raw, context="gtt_create")
    rule_id = int(data.get("id", 0))
    _log.info("GTT created ✓ rule_id=%d", rule_id)
    return rule_id


def create_gtt_oco(
    session:         AngelSession,
    symbol:          str,
    token:           str,
    exchange:        str,
    quantity:        int,
    target_price:    float,
    target_limit:    float,
    stoploss_price:  float,
    stoploss_limit:  float,
    product_type:    str = ProductType.DELIVERY,
) -> tuple:
    """
    Create two GTT rules forming an OCO (One-Cancels-Other) pattern.

    ⚠ AngelOne does NOT natively support OCO orders.  This creates two
    independent GTT rules (take-profit SELL + stoploss SELL).  Your bot
    must cancel the other rule once one is triggered (monitor via gtt_list).

    Args:
        session        : authenticated AngelSession
        symbol         : e.g. "RELIANCE-EQ"
        token          : instrument token
        exchange       : exchange code
        quantity       : shares to exit
        target_price   : take-profit trigger price (₹)
        target_limit   : take-profit limit price (₹)
        stoploss_price : stop-loss trigger price (₹)
        stoploss_limit : stop-loss limit price (₹)
        product_type   : DELIVERY or CARRYFORWARD

    Returns:
        Tuple (target_rule_id, stoploss_rule_id)

    Example:
        # Entry at 2350 — TP at 2450 trigger, SL at 2300 trigger
        tp_id, sl_id = create_gtt_oco(
            session, "RELIANCE-EQ", "2885", "NSE", 5,
            target_price=2450.0, target_limit=2445.0,
            stoploss_price=2300.0, stoploss_limit=2295.0
        )
    """
    _log.info("Creating GTT OCO pair for %s (TP=₹%.2f SL=₹%.2f)", symbol, target_price, stoploss_price)
    tp_id = create_gtt(
        session, symbol, token, exchange,
        TransactionType.SELL, target_price, target_limit, quantity, product_type
    )
    sl_id = create_gtt(
        session, symbol, token, exchange,
        TransactionType.SELL, stoploss_price, stoploss_limit, quantity, product_type
    )
    _log.info("GTT OCO created ✓ TP rule_id=%d  SL rule_id=%d", tp_id, sl_id)
    return tp_id, sl_id


def modify_gtt(
    session:          AngelSession,
    rule_id:          int,
    symbol:           str,
    token:            str,
    exchange:         str,
    transaction_type: str,
    trigger_price:    float,
    limit_price:      float,
    quantity:         int,
    product_type:     str = ProductType.DELIVERY,
    time_period:      int = 365,
) -> dict:
    """
    Modify an existing GTT rule.

    Args:
        rule_id       : GTT rule ID returned from create_gtt()
        (all other args same as create_gtt)

    Returns:
        API response dict
    """
    payload = {
        "id":              rule_id,
        "tradingsymbol":   symbol,
        "symboltoken":     str(token),
        "exchange":        exchange,
        "producttype":     product_type,
        "transactiontype": transaction_type,
        "price":           rupees_to_paise(limit_price),
        "qty":             quantity,
        "disclosedqty":    quantity,
        "triggerprice":    rupees_to_paise(trigger_price),
        "timeperiod":      time_period,
    }
    _log.info("Modifying GTT rule_id=%d → trigger=₹%.2f limit=₹%.2f", rule_id, trigger_price, limit_price)
    raw  = _post(session, "gtt_modify", payload)
    data = validate_response(raw, context="gtt_modify")
    _log.info("GTT modified ✓")
    return data


def cancel_gtt(
    session:  AngelSession,
    rule_id:  int,
    symbol:   str,
    token:    str,
    exchange: str,
) -> dict:
    """
    Cancel an existing GTT rule.

    Args:
        session  : authenticated AngelSession
        rule_id  : GTT rule ID to cancel
        symbol   : trading symbol (required by API)
        token    : instrument token (required by API)
        exchange : exchange code (required by API)

    Returns:
        API response dict

    Example:
        cancel_gtt(session, rule_id=12345, symbol="RELIANCE-EQ",
                   token="2885", exchange="NSE")
    """
    payload = {"id": rule_id, "tradingsymbol": symbol, "symboltoken": str(token), "exchange": exchange}
    _log.info("Cancelling GTT rule_id=%d (%s)", rule_id, symbol)
    raw  = _post(session, "gtt_cancel", payload)
    data = validate_response(raw, context="gtt_cancel")
    _log.info("GTT cancelled ✓ rule_id=%d", rule_id)
    return data


def list_gtt(
    session:  AngelSession,
    status:   list = None,
    page:     int  = 1,
    count:    int  = 20,
) -> list:
    """
    List GTT rules filtered by status.

    Args:
        session : authenticated AngelSession
        status  : list of GTTStatus values (default ["FORALL"] = all)
        page    : page number (1-indexed)
        count   : results per page

    Returns:
        List of GTT rule dicts

    Example:
        active_rules = list_gtt(session, status=["ACTIVE"])
        triggered    = list_gtt(session, status=["TRIGGERED"])
    """
    payload = {"status": status or [GTTStatus.FORALL], "page": page, "count": count}
    raw  = _post(session, "gtt_list", payload)
    data = validate_response(raw, context="gtt_list")
    return data if isinstance(data, list) else []


def get_gtt_details(session: AngelSession, rule_id: int) -> dict:
    """
    Get detailed information about a single GTT rule.

    Args:
        session : authenticated AngelSession
        rule_id : GTT rule ID

    Returns:
        GTT rule detail dict

    Example:
        details = get_gtt_details(session, rule_id=12345)
        print(details["status"])
    """
    payload = {"id": rule_id}
    raw  = _post(session, "gtt_details", payload)
    return validate_response(raw, context="gtt_details")
