"""
portfolio.py — AngelOne SmartAPI · Portfolio & Margin
=====================================================
Fetch holdings, open positions, available margin (RMS),
and convert positions between product types.

All functions accept a session: AngelSession and return
clean Python dicts / lists with prices already in rupees.

Dependencies:
    pip install requests
"""

import requests
from typing import List, Optional

from config import BASE_URL, ENDPOINTS, REQUEST_TIMEOUT, Exchange, ProductType
from session import AngelSession
from utils import get_logger, validate_response, AngelOneAPIError

_log = get_logger(__name__)


def _get(session: AngelSession, endpoint_key: str, params: dict = None) -> dict:
    """Authenticated GET helper (shared pattern for portfolio reads)."""
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


def _post(session: AngelSession, endpoint_key: str, payload: dict) -> dict:
    """Authenticated POST helper for portfolio write operations."""
    if not session.tokens:
        raise AngelOneAPIError("Session not initialised — call session.login() first")
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


# ──────────────────────────────────────────────────────────────────────────────
# HOLDINGS
# ──────────────────────────────────────────────────────────────────────────────

def get_holdings(session: AngelSession) -> List[dict]:
    """
    Fetch the current Demat holdings (long-term equity portfolio).

    Holdings are settled positions — shares you own in your Demat account.
    Does NOT include today's intraday trades or unsettled T+1 positions.

    Returns a list of dicts, each with:
        tradingsymbol   : e.g. "RELIANCE-EQ"
        exchange        : e.g. "NSE"
        isin            : ISIN code
        symboltoken     : instrument token
        quantity        : settled holdings quantity
        t1quantity      : T+1 quantity (not yet settled)
        realisedquantity: realised quantity
        averageprice    : average buy price (₹)
        ltp             : last traded price (₹)
        close           : previous day close (₹)
        profitandloss   : unrealised P&L (₹)
        pnlpercentage   : P&L percentage

    Returns:
        List of holding dicts (empty list if no holdings)

    Example:
        holdings = get_holdings(session)
        for h in holdings:
            print(f"{h['tradingsymbol']}: qty={h['quantity']} P&L=₹{h['profitandloss']}")
    """
    raw  = _get(session, "holdings")
    data = validate_response(raw, context="get_holdings")
    holdings = data if isinstance(data, list) else []
    _log.info("Fetched %d holdings", len(holdings))
    return holdings


def get_all_holdings(session: AngelSession) -> dict:
    """
    Fetch holdings including T+1 (unsettled) positions and portfolio summary.

    Returns a dict with:
        "holdings"     : list of holding dicts (same as get_holdings)
        "totalholding" : summary dict with total invested, current value, P&L

    Example:
        data = get_all_holdings(session)
        summary = data.get("totalholding", {})
        print(f"Total investment : ₹{summary.get('totalholdingvalue', 0)}")
        print(f"Current value    : ₹{summary.get('totalmarketvalue', 0)}")
        print(f"Total P&L        : ₹{summary.get('totalprofitandloss', 0)}")
    """
    raw  = _get(session, "all_holdings")
    data = validate_response(raw, context="get_all_holdings")
    _log.info(
        "All holdings fetched — %d positions",
        len(data.get("holdings", []))
    )
    return data


def get_holding_summary(session: AngelSession) -> dict:
    """
    Return a simplified portfolio summary dict.

    Calculates aggregate values across all holdings.

    Returns dict with keys:
        "total_invested"        : total cost basis (₹)
        "total_current_value"   : current market value (₹)
        "total_pnl"             : unrealised P&L (₹)
        "total_pnl_pct"         : overall P&L percentage
        "num_holdings"          : number of distinct stocks held

    Example:
        summary = get_holding_summary(session)
        print(f"Portfolio value: ₹{summary['total_current_value']:,.2f}")
        print(f"Overall P&L:     ₹{summary['total_pnl']:,.2f} ({summary['total_pnl_pct']:.2f}%)")
    """
    holdings = get_holdings(session)
    total_invested = sum(
        float(h.get("averageprice", 0)) * int(h.get("quantity", 0)) for h in holdings
    )
    total_current  = sum(
        float(h.get("ltp", 0)) * int(h.get("quantity", 0)) for h in holdings
    )
    total_pnl = total_current - total_invested
    pnl_pct   = (total_pnl / total_invested * 100) if total_invested > 0 else 0.0

    return {
        "total_invested":      round(total_invested, 2),
        "total_current_value": round(total_current,  2),
        "total_pnl":           round(total_pnl,      2),
        "total_pnl_pct":       round(pnl_pct,        2),
        "num_holdings":        len(holdings),
    }


# ──────────────────────────────────────────────────────────────────────────────
# POSITIONS
# ──────────────────────────────────────────────────────────────────────────────

def get_positions(session: AngelSession) -> List[dict]:
    """
    Fetch open positions for the current trading session.

    Positions include intraday (MIS), F&O, and any unsettled carry-forward trades.

    Returns a list of dicts, each with:
        tradingsymbol   : e.g. "SBIN-EQ"
        exchange        : e.g. "NSE"
        symboltoken     : instrument token
        producttype     : INTRADAY / CARRYFORWARD / etc.
        netqty          : net open quantity (+ = long, - = short)
        buyqty          : total buy quantity today
        sellqty         : total sell quantity today
        buyamount       : total buy value (₹)
        sellamount      : total sell value (₹)
        ltp             : last traded price (₹)
        pnl             : unrealised P&L (₹)
        realised        : realised P&L (₹)
        unrealised      : unrealised P&L (₹)

    Returns:
        List of position dicts (empty list if no open positions)

    Example:
        positions = get_positions(session)
        for p in positions:
            if p["netqty"] != 0:
                print(f"{p['tradingsymbol']}: netqty={p['netqty']} P&L=₹{p['pnl']}")
    """
    raw  = _get(session, "positions")
    data = validate_response(raw, context="get_positions")
    positions = data if isinstance(data, list) else []
    _log.info("Fetched %d positions", len(positions))
    return positions


def get_open_positions(session: AngelSession) -> List[dict]:
    """
    Return only positions with a non-zero net quantity.
    Filters out fully squared-off positions from get_positions().

    Returns:
        List of position dicts where netqty != 0

    Example:
        open_pos = get_open_positions(session)
        print(f"You have {len(open_pos)} open positions")
    """
    positions = get_positions(session)
    return [p for p in positions if int(p.get("netqty", 0)) != 0]


def is_position_open(session: AngelSession, symbol: str, exchange: str = "NSE") -> bool:
    """
    Check if there is an open position in a given symbol.

    Args:
        session  : authenticated AngelSession
        symbol   : trading symbol e.g. "SBIN-EQ"
        exchange : exchange code (default NSE)

    Returns:
        True if there's a non-zero netqty position in that symbol

    Example:
        if not is_position_open(session, "SBIN-EQ"):
            buy(session, "SBIN-EQ", "3045", quantity=10)
    """
    positions = get_positions(session)
    for p in positions:
        if (p.get("tradingsymbol", "").upper() == symbol.upper() and
                p.get("exchange", "").upper() == exchange.upper()):
            return int(p.get("netqty", 0)) != 0
    return False


def get_position_pnl(session: AngelSession) -> dict:
    """
    Compute day's total P&L across all positions.

    Returns:
        dict with keys:
            "total_pnl"    : total P&L including realised + unrealised (₹)
            "realised"     : realised P&L from closed intraday trades (₹)
            "unrealised"   : unrealised P&L on open positions (₹)
            "num_positions": number of distinct symbols with any activity today

    Example:
        pnl = get_position_pnl(session)
        print(f"Today's P&L: ₹{pnl['total_pnl']:,.2f}")
    """
    positions = get_positions(session)
    realised   = sum(float(p.get("realised",   0)) for p in positions)
    unrealised = sum(float(p.get("unrealised", 0)) for p in positions)
    return {
        "total_pnl":     round(realised + unrealised, 2),
        "realised":      round(realised,    2),
        "unrealised":    round(unrealised,  2),
        "num_positions": len(positions),
    }


# ──────────────────────────────────────────────────────────────────────────────
# MARGIN / BALANCE  (Risk Management System — RMS)
# ──────────────────────────────────────────────────────────────────────────────

def get_rms(session: AngelSession) -> dict:
    """
    Fetch the Risk Management System (RMS) data — available margin and funds.

    Returns a dict with key fields:
        "net"                   : total net balance (₹)
        "availablecash"         : available cash for trading (₹)
        "availableintradaypayin": additional intraday limit (₹)
        "utiliseddebits"        : margin already utilised (₹)
        "utilisedspan"          : SPAN margin used (₹)
        "utilisedoptionpremium" : premium margin used (₹)

    Returns:
        RMS dict

    Example:
        rms = get_rms(session)
        print(f"Available cash: ₹{rms['availablecash']}")
        print(f"Net balance:    ₹{rms['net']}")
    """
    raw  = _get(session, "rms")
    data = validate_response(raw, context="get_rms")
    _log.info(
        "RMS fetched — available cash: ₹%s  net: ₹%s",
        data.get("availablecash", "?"), data.get("net", "?")
    )
    return data


def get_available_cash(session: AngelSession) -> float:
    """
    Convenience function — returns available cash balance in rupees as a float.

    Returns:
        Available cash (₹) as float; 0.0 if unavailable

    Example:
        cash = get_available_cash(session)
        if cash >= 10000:
            buy(session, "SBIN-EQ", "3045", quantity=10)
    """
    try:
        rms = get_rms(session)
        return float(rms.get("availablecash", 0.0))
    except AngelOneAPIError:
        return 0.0


def has_sufficient_margin(
    session:    AngelSession,
    required:   float,
    buffer_pct: float = 0.05,
) -> bool:
    """
    Check if available margin is sufficient for a trade, with a safety buffer.

    Args:
        session    : authenticated AngelSession
        required   : estimated margin required for the trade (₹)
        buffer_pct : extra margin buffer as a fraction (default 5%)
                     e.g. 0.05 means require 105% of the estimated margin

    Returns:
        True if available cash >= required * (1 + buffer_pct)

    Example:
        # Check before placing 10 lots of SBIN at ₹550 (intraday needs ~₹1375 margin)
        if has_sufficient_margin(session, required=1500):
            buy(session, "SBIN-EQ", "3045", quantity=10)
    """
    available = get_available_cash(session)
    needed    = required * (1 + buffer_pct)
    ok        = available >= needed
    if not ok:
        _log.warning(
            "Insufficient margin: need ₹%.2f (with %.0f%% buffer), available ₹%.2f",
            needed, buffer_pct * 100, available
        )
    return ok


# ──────────────────────────────────────────────────────────────────────────────
# POSITION CONVERSION
# ──────────────────────────────────────────────────────────────────────────────

def convert_position(
    session:         AngelSession,
    symbol:          str,
    token:           str,
    exchange:        str,
    from_product:    str,
    to_product:      str,
    quantity:        int,
    duration:        str = "DAY",
) -> dict:
    """
    Convert an open position from one product type to another.

    Common use case: convert an intraday (MIS) position to delivery (CNC)
    at the end of the day to avoid auto square-off.

    Args:
        session      : authenticated AngelSession
        symbol       : trading symbol e.g. "SBIN-EQ"
        token        : instrument token
        exchange     : exchange code
        from_product : current product type e.g. ProductType.INTRADAY
        to_product   : target product type e.g. ProductType.DELIVERY
        quantity     : quantity to convert
        duration     : "DAY" or "IOC"

    Returns:
        API response dict

    Example:
        # Convert 10 SBIN shares from intraday to delivery
        result = convert_position(
            session, "SBIN-EQ", "3045", "NSE",
            from_product=ProductType.INTRADAY,
            to_product=ProductType.DELIVERY,
            quantity=10
        )
    """
    payload = {
        "exchange":       exchange,
        "oldproducttype": from_product,
        "newproducttype": to_product,
        "tradingsymbol":  symbol,
        "symboltoken":    str(token),
        "quantity":       quantity,
        "type":           duration,
    }
    _log.info(
        "Converting position: %s qty=%d %s → %s",
        symbol, quantity, from_product, to_product
    )
    raw  = _post(session, "convert_position", payload)
    data = validate_response(raw, context="convert_position")
    _log.info("Position converted ✓")
    return data
