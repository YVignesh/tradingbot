"""
angelone_sdk — AngelOne SmartAPI Trading Bot Toolkit
=====================================================
A modular, well-documented Python toolkit for building trading bots
on top of AngelOne's SmartAPI.

Quick start:
    from angelone_sdk import AngelSession, buy, sell, get_candles, MarketFeed

    session = AngelSession.from_env()
    session.login()

    result = buy(session, "SBIN-EQ", "3045", quantity=10)

Module map:
    session.py        — Authentication, token management, session lifecycle
    instruments.py    — Instruments master download, symbol↔token lookup
    orders.py         — Buy/sell/cancel/SL/TP/GTT/bracket order helpers
    portfolio.py      — Holdings, positions, margin (RMS), P&L
    market_data.py    — Historical candles, live quotes, market status
    websocket_feed.py — Real-time tick stream (MarketFeed) + order updates (OrderFeed)
    charges.py        — Brokerage + tax calculator, breakeven analysis
    utils.py          — Logging, rate limiter, paise↔rupee conversion
    config.py         — All constants, enums, endpoints, charge rates
"""

# ── Session ───────────────────────────────────────────────────────────────────
from session import AngelSession, SessionTokens

# ── Instruments ───────────────────────────────────────────────────────────────
from instruments import InstrumentMaster

# ── Orders ────────────────────────────────────────────────────────────────────
from orders import (
    place_order,
    buy,
    sell,
    buy_limit,
    sell_limit,
    place_stop_loss,
    place_stop_loss_market,
    place_take_profit,
    place_bracket_order,
    modify_order,
    cancel_order,
    get_order_book,
    get_trade_book,
    get_order_status,
    get_ltp,
    create_gtt,
    create_gtt_oco,
    modify_gtt,
    cancel_gtt,
    list_gtt,
    get_gtt_details,
)

# ── Portfolio ─────────────────────────────────────────────────────────────────
from portfolio import (
    get_holdings,
    get_all_holdings,
    get_holding_summary,
    get_positions,
    get_open_positions,
    is_position_open,
    get_position_pnl,
    get_rms,
    get_available_cash,
    has_sufficient_margin,
    convert_position,
)

# ── Market Data ───────────────────────────────────────────────────────────────
from market_data import (
    get_candles,
    get_candles_today,
    get_candles_n_days,
    candles_to_dataframe,
    get_quote,
    get_ltp_single,
    get_ltp_bulk,
    get_ohlc,
    is_market_open,
    minutes_to_market_open,
)

# ── WebSocket Feeds ───────────────────────────────────────────────────────────
from websocket_feed import MarketFeed, OrderFeed, parse_tick

# ── Charges ───────────────────────────────────────────────────────────────────
from charges import (
    Segment,
    ChargeBreakdown,
    calculate_charges,
    estimate_charges_buy_only,
    net_pnl_after_charges,
    breakeven_price,
)

# ── Config / Enums ────────────────────────────────────────────────────────────
from config import (
    Variety,
    TransactionType,
    OrderType,
    ProductType,
    Duration,
    Exchange,
    MarketDataMode,
    CandleInterval,
    ExchangeType,
    WSMode,
    GTTStatus,
    ChargeRates,
    RateLimits,
    PAISE_PER_RUPEE,
)

# ── Utils ─────────────────────────────────────────────────────────────────────
from utils import (
    paise_to_rupees,
    rupees_to_paise,
    format_price,
    get_logger,
    AngelOneAPIError,
    RateLimiter,
    order_rate_limiter,
    now_ist_str,
    today_ist_str,
)

__version__ = "1.0.0"
__author__  = "AngelOne SmartAPI Bot Toolkit"

__all__ = [
    # Session
    "AngelSession", "SessionTokens",
    # Instruments
    "InstrumentMaster",
    # Orders
    "place_order", "buy", "sell", "buy_limit", "sell_limit",
    "place_stop_loss", "place_stop_loss_market", "place_take_profit",
    "place_bracket_order", "modify_order", "cancel_order",
    "get_order_book", "get_trade_book", "get_order_status", "get_ltp",
    "create_gtt", "create_gtt_oco", "modify_gtt", "cancel_gtt",
    "list_gtt", "get_gtt_details",
    # Portfolio
    "get_holdings", "get_all_holdings", "get_holding_summary",
    "get_positions", "get_open_positions", "is_position_open",
    "get_position_pnl", "get_rms", "get_available_cash",
    "has_sufficient_margin", "convert_position",
    # Market Data
    "get_candles", "get_candles_today", "get_candles_n_days",
    "candles_to_dataframe", "get_quote", "get_ltp_single",
    "get_ltp_bulk", "get_ohlc", "is_market_open", "minutes_to_market_open",
    # WebSocket
    "MarketFeed", "OrderFeed", "parse_tick",
    # Charges
    "Segment", "ChargeBreakdown", "calculate_charges",
    "estimate_charges_buy_only", "net_pnl_after_charges", "breakeven_price",
    # Enums
    "Variety", "TransactionType", "OrderType", "ProductType",
    "Duration", "Exchange", "MarketDataMode", "CandleInterval",
    "ExchangeType", "WSMode", "GTTStatus", "ChargeRates", "RateLimits",
    "PAISE_PER_RUPEE",
    # Utils
    "paise_to_rupees", "rupees_to_paise", "format_price",
    "get_logger", "AngelOneAPIError", "RateLimiter",
    "order_rate_limiter", "now_ist_str", "today_ist_str",
]
