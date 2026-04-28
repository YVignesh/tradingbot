"""
market_data.py — AngelOne SmartAPI · Market Data & Historical Candles
======================================================================
Fetch live quotes (LTP / OHLC / full market depth) and historical
OHLCV candle data for any instrument across all exchanges.

All price values are returned in RUPEES (float). The WebSocket feed
for real-time streaming is handled separately in websocket_feed.py.

Usage:
    from market_data import get_candles, get_quote, get_ltp_bulk

    # Historical candles
    candles = get_candles(session, "NSE", "3045",
                          CandleInterval.FIVE_MINUTE,
                          "2024-01-15 09:15", "2024-01-15 15:30")

    # Live quote for multiple symbols
    quotes = get_quote(session, mode=MarketDataMode.FULL,
                       tokens={"NSE": ["3045", "2885"]})

Dependencies:
    pip install requests pandas  (pandas optional — see to_dataframe())
"""

import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from broker.constants import BASE_URL, ENDPOINTS, REQUEST_TIMEOUT, CandleInterval, MarketDataMode
from broker.session import AngelSession
from utils import get_logger, validate_response, AngelOneAPIError, paise_to_rupees

_log = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


# ──────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _post(session: AngelSession, endpoint_key: str, payload: dict) -> dict:
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
# CANDLE / HISTORICAL DATA
# ──────────────────────────────────────────────────────────────────────────────

def get_candles(
    session:    AngelSession,
    exchange:   str,
    token:      str,
    interval:   str,
    from_date:  str,
    to_date:    str,
) -> List[dict]:
    """
    Fetch historical OHLCV candle data for any instrument.

    Args:
        session   : authenticated AngelSession
        exchange  : exchange code e.g. "NSE", "NFO", "MCX"
        token     : instrument token e.g. "3045"
        interval  : CandleInterval constant e.g. CandleInterval.FIVE_MINUTE
        from_date : start datetime string  "YYYY-MM-DD HH:MM"
        to_date   : end datetime string    "YYYY-MM-DD HH:MM"

    Returns:
        List of dicts, each representing one candle:
            {
                "timestamp" : ISO datetime string (IST)
                "open"      : float (₹)
                "high"      : float (₹)
                "low"       : float (₹)
                "close"     : float (₹)
                "volume"    : int
            }
        Oldest candle first.

    Raises:
        AngelOneAPIError on API failure (e.g. AB9019 = no data for date range)

    Example:
        candles = get_candles(
            session, "NSE", "3045",
            CandleInterval.FIVE_MINUTE,
            "2024-01-15 09:15",
            "2024-01-15 15:30",
        )
        latest = candles[-1]
        print(f"Latest close: ₹{latest['close']}")
    """
    payload = {
        "exchange":    exchange,
        "symboltoken": str(token),
        "interval":    interval,
        "fromdate":    from_date,
        "todate":      to_date,
    }

    _log.debug(
        "Fetching candles: exchange=%s token=%s interval=%s from=%s to=%s",
        exchange, token, interval, from_date, to_date,
    )

    raw  = _post(session, "candle_data", payload)
    data = validate_response(raw, context="get_candles")

    # data is a list of [timestamp, open, high, low, close, volume]
    raw_candles = data if isinstance(data, list) else []

    candles = []
    for row in raw_candles:
        if len(row) < 6:
            continue
        candles.append({
            "timestamp": row[0],
            "open":      float(row[1]),
            "high":      float(row[2]),
            "low":       float(row[3]),
            "close":     float(row[4]),
            "volume":    int(row[5]),
        })

    _log.debug("Received %d candles for token %s", len(candles), token)
    return candles


def get_candles_today(
    session:  AngelSession,
    exchange: str,
    token:    str,
    interval: str = CandleInterval.FIVE_MINUTE,
) -> List[dict]:
    """
    Shorthand — fetch today's candles from market open (09:15 IST) to now.

    Args:
        session  : authenticated AngelSession
        exchange : exchange code
        token    : instrument token
        interval : candle interval (default 5-minute)

    Returns:
        List of candle dicts (see get_candles for schema)

    Example:
        today = get_candles_today(session, "NSE", "3045")
    """
    now = datetime.now(IST)
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    from_date = market_open.strftime("%Y-%m-%d %H:%M")
    to_date   = now.strftime("%Y-%m-%d %H:%M")
    return get_candles(session, exchange, token, interval, from_date, to_date)


def get_candles_n_days(
    session:  AngelSession,
    exchange: str,
    token:    str,
    days:     int,
    interval: str = CandleInterval.ONE_DAY,
) -> List[dict]:
    """
    Fetch candles for the last N calendar days.

    Args:
        session  : authenticated AngelSession
        exchange : exchange code
        token    : instrument token
        days     : number of calendar days to look back
        interval : candle interval (default daily)

    Returns:
        List of candle dicts

    Example:
        # Last 30 days of daily data
        daily = get_candles_n_days(session, "NSE", "3045", days=30)

        # Last 5 days of 15-minute data
        intraday = get_candles_n_days(
            session, "NSE", "3045", days=5,
            interval=CandleInterval.FIFTEEN_MINUTE
        )
    """
    now       = datetime.now(IST)
    from_date = (now - timedelta(days=days)).strftime("%Y-%m-%d 09:15")
    to_date   = now.strftime("%Y-%m-%d %H:%M")
    return get_candles(session, exchange, token, interval, from_date, to_date)


def candles_to_dataframe(candles: List[dict]):
    """
    Convert a list of candle dicts to a pandas DataFrame.

    Sets 'timestamp' as the index (as timezone-aware datetime).
    Columns: open, high, low, close, volume.

    Requires: pip install pandas

    Args:
        candles : list of candle dicts from get_candles()

    Returns:
        pandas DataFrame indexed by datetime

    Raises:
        ImportError if pandas is not installed

    Example:
        df = candles_to_dataframe(get_candles(...))
        df['sma_20'] = df['close'].rolling(20).mean()
    """
    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError("pandas is required for candles_to_dataframe: pip install pandas") from e

    if not candles:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(candles)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()
    return df


# ──────────────────────────────────────────────────────────────────────────────
# LIVE MARKET QUOTES
# ──────────────────────────────────────────────────────────────────────────────

def get_quote(
    session:  AngelSession,
    tokens:   Dict[str, List[str]],
    mode:     str = MarketDataMode.FULL,
) -> List[dict]:
    """
    Fetch live market data for one or more instruments.

    Supports three data modes:
      • LTP  — last traded price only (fastest)
      • OHLC — OHLC + LTP
      • FULL — OHLC + market depth (5 buy/sell) + volume + circuit limits

    Args:
        session : authenticated AngelSession
        tokens  : dict mapping exchange → list of token strings
                  e.g. {"NSE": ["3045", "2885"], "BSE": ["500325"]}
        mode    : MarketDataMode constant (default FULL)

    Returns:
        List of quote dicts. Each dict contains:
            exchange, tradingsymbol, symboltoken, ltp, open, high, low, close
            (FULL also includes: totalbuyqty, totalsellqty, volume,
             52weeklow, 52weekhigh, uppercircuit, lowercircuit,
             best5buy, best5sell)
        All prices are in rupees (₹) as floats.

    Example:
        quotes = get_quote(session, {"NSE": ["3045", "2885"]})
        for q in quotes:
            print(f"{q['tradingsymbol']}: ₹{q['ltp']}")
    """
    payload = {"mode": mode, "exchangeTokens": tokens}
    raw  = _post(session, "market_data", payload)
    data = validate_response(raw, context="get_quote")

    # AngelOne returns { "fetched": [...], "unfetched": [...] }
    fetched = data.get("fetched", []) if isinstance(data, dict) else data

    _log.debug("get_quote: fetched %d instruments (mode=%s)", len(fetched), mode)
    return fetched


def get_ltp_single(
    session:  AngelSession,
    exchange: str,
    symbol:   str,
    token:    str,
) -> float:
    """
    Get the last traded price for a single symbol.

    Uses the dedicated LTP endpoint (faster than get_quote for single symbols).

    Args:
        session  : authenticated AngelSession
        exchange : e.g. "NSE"
        symbol   : trading symbol e.g. "SBIN-EQ"
        token    : instrument token e.g. "3045"

    Returns:
        Last traded price as float (₹)

    Raises:
        AngelOneAPIError if symbol not found or session invalid

    Example:
        ltp = get_ltp_single(session, "NSE", "SBIN-EQ", "3045")
        print(f"SBIN LTP: ₹{ltp}")
    """
    payload = {"exchange": exchange, "tradingsymbol": symbol, "symboltoken": str(token)}
    raw  = _post(session, "ltp", payload)
    data = validate_response(raw, context="get_ltp_single")
    return float(data.get("ltp", 0.0))


def get_ltp_bulk(
    session:  AngelSession,
    tokens:   Dict[str, List[str]],
) -> Dict[str, float]:
    """
    Get LTP for multiple symbols in one API call.

    Args:
        session : authenticated AngelSession
        tokens  : {"NSE": ["3045", "2885"], "BSE": ["500325"]}

    Returns:
        Dict mapping "EXCHANGE:TOKEN" → ltp (₹)
        e.g. {"NSE:3045": 550.75, "NSE:2885": 2410.50}

    Example:
        prices = get_ltp_bulk(session, {"NSE": ["3045", "2885"]})
        sbin_ltp = prices["NSE:3045"]
    """
    quotes = get_quote(session, tokens, mode=MarketDataMode.LTP)
    result = {}
    for q in quotes:
        exc   = q.get("exchange", "")
        tok   = q.get("symboltoken", "")
        price = float(q.get("ltp", 0.0))
        if exc and tok:
            result[f"{exc}:{tok}"] = price
    return result


def get_ohlc(
    session:  AngelSession,
    tokens:   Dict[str, List[str]],
) -> Dict[str, dict]:
    """
    Get OHLC + LTP for multiple symbols in one call.

    Args:
        session : authenticated AngelSession
        tokens  : {"NSE": ["3045", "2885"]}

    Returns:
        Dict mapping "EXCHANGE:TOKEN" → {open, high, low, close, ltp}

    Example:
        ohlc = get_ohlc(session, {"NSE": ["3045"]})
        print(ohlc["NSE:3045"])
    """
    quotes = get_quote(session, tokens, mode=MarketDataMode.OHLC)
    result = {}
    for q in quotes:
        exc = q.get("exchange", "")
        tok = q.get("symboltoken", "")
        if exc and tok:
            result[f"{exc}:{tok}"] = {
                "open":  float(q.get("open",  0.0)),
                "high":  float(q.get("high",  0.0)),
                "low":   float(q.get("low",   0.0)),
                "close": float(q.get("close", 0.0)),
                "ltp":   float(q.get("ltp",   0.0)),
            }
    return result


def is_market_open() -> bool:
    """
    Check if the Indian equity market is currently open.

    Market hours: Monday–Friday, 09:15 to 15:30 IST.
    Does NOT account for exchange holidays — add holiday calendar as needed.

    Returns:
        True if current IST time is within regular market hours
        False on weekends or outside 09:15–15:30 IST

    Example:
        if is_market_open():
            buy(session, ...)
        else:
            print("Market is closed")
    """
    now = datetime.now(IST)
    if now.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    market_start = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    market_end   = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_start <= now <= market_end


def minutes_to_market_open() -> Optional[int]:
    """
    Return minutes until market opens, or None if market is open/already closed today.

    Useful for scheduling pre-market tasks.

    Returns:
        Integer minutes until 09:15 IST, or None if market is open or past close

    Example:
        mins = minutes_to_market_open()
        if mins:
            print(f"Market opens in {mins} minutes")
            time.sleep(mins * 60)
        get_candles(...)   # now safe to fetch
    """
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return None
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    if now >= market_open:
        return None
    return int((market_open - now).total_seconds() / 60)
