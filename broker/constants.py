"""
config.py — AngelOne SmartAPI · Central Configuration
======================================================
All constants, enums, API endpoints, and charge rates live here.
Edit this file to update rates when AngelOne publishes new pricing.

Last updated: April 2026 (source: angelone.in/exchange-transaction-charges)
"""

# ──────────────────────────────────────────────────────────────────────────────
# BASE URLS
# ──────────────────────────────────────────────────────────────────────────────

BASE_URL = "https://apiconnect.angelone.in"
LEGACY_URL = "https://apiconnect.angelbroking.com"   # still active as fallback
INSTRUMENTS_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)
WEBSOCKET_URL = "wss://smartapisocket.angelone.in/smart-stream"

# ──────────────────────────────────────────────────────────────────────────────
# REST ENDPOINT PATHS  (appended to BASE_URL)
# ──────────────────────────────────────────────────────────────────────────────

ENDPOINTS = {
    # Auth
    "login":            "/rest/auth/angelbroking/user/v1/loginByPassword",
    "refresh_token":    "/rest/auth/angelbroking/jwt/v1/generateTokens",
    "profile":          "/rest/secure/angelbroking/user/v1/getProfile",
    "logout":           "/rest/secure/angelbroking/user/v1/logout",
    "rms":              "/rest/secure/angelbroking/user/v1/getRMS",

    # Orders
    "place_order":      "/rest/secure/angelbroking/order/v1/placeOrder",
    "modify_order":     "/rest/secure/angelbroking/order/v1/modifyOrder",
    "cancel_order":     "/rest/secure/angelbroking/order/v1/cancelOrder",
    "order_book":       "/rest/secure/angelbroking/order/v1/getOrderBook",
    "trade_book":       "/rest/secure/angelbroking/order/v1/getTradeBook",
    "order_status":     "/rest/secure/angelbroking/order/v1/details",   # + /{uniqueOrderId}
    "ltp":              "/rest/secure/angelbroking/order/v1/getLtpData",
    "convert_position": "/rest/secure/angelbroking/order/v1/convertPosition",
    "positions":        "/rest/secure/angelbroking/order/v1/getPosition",

    # Market Data
    "market_data":      "/rest/secure/angelbroking/market/v1/quote/",

    # Portfolio
    "holdings":         "/rest/secure/angelbroking/portfolio/v1/getHolding",
    "all_holdings":     "/rest/secure/angelbroking/portfolio/v1/getAllHolding",

    # Historical
    "candle_data":      "/rest/secure/angelbroking/historical/v1/getCandleData",

    # GTT
    "gtt_create":       "/rest/secure/angelbroking/gtt/v1/createRule",
    "gtt_modify":       "/rest/secure/angelbroking/gtt/v1/modifyRule",
    "gtt_cancel":       "/rest/secure/angelbroking/gtt/v1/cancelRule",
    "gtt_details":      "/rest/secure/angelbroking/gtt/v1/ruleDetails",
    "gtt_list":         "/rest/secure/angelbroking/gtt/v1/ruleList",
}

# ──────────────────────────────────────────────────────────────────────────────
# ORDER ENUMS
# ──────────────────────────────────────────────────────────────────────────────

class Variety:
    NORMAL   = "NORMAL"     # Regular order
    AMO      = "AMO"        # After-market order
    STOPLOSS = "STOPLOSS"   # Stop-loss order
    ROBO     = "ROBO"       # Bracket (ROBO) order


class TransactionType:
    BUY  = "BUY"
    SELL = "SELL"


class OrderType:
    MARKET          = "MARKET"          # Market order (MKT)
    LIMIT           = "LIMIT"           # Limit order (L)
    STOPLOSS_LIMIT  = "STOPLOSS_LIMIT"  # Stop-loss limit (SL)
    STOPLOSS_MARKET = "STOPLOSS_MARKET" # Stop-loss market (SL-M)


class ProductType:
    DELIVERY      = "DELIVERY"      # Cash & Carry / CNC
    CARRYFORWARD  = "CARRYFORWARD"  # Normal futures/options (NRML)
    MARGIN        = "MARGIN"        # Margin delivery
    INTRADAY      = "INTRADAY"      # Margin Intraday Squareoff (MIS)
    BO            = "BO"            # Bracket order (only with ROBO)


class Duration:
    DAY = "DAY"   # Valid for the day
    IOC = "IOC"   # Immediate or Cancel


class Exchange:
    NSE   = "NSE"    # NSE Equity
    BSE   = "BSE"    # BSE Equity
    NFO   = "NFO"    # NSE Futures & Options
    CDS   = "CDS"    # NSE Currency Derivatives
    MCX   = "MCX"    # MCX Commodity
    NCDEX = "NCDEX"  # NCDEX Commodity


class MarketDataMode:
    LTP  = "LTP"   # Last traded price only
    OHLC = "OHLC"  # Open, High, Low, Close + LTP
    FULL = "FULL"  # OHLC + market depth + volume + circuit limits


class CandleInterval:
    ONE_MINUTE     = "ONE_MINUTE"
    THREE_MINUTE   = "THREE_MINUTE"
    FIVE_MINUTE    = "FIVE_MINUTE"
    TEN_MINUTE     = "TEN_MINUTE"
    FIFTEEN_MINUTE = "FIFTEEN_MINUTE"
    THIRTY_MINUTE  = "THIRTY_MINUTE"
    ONE_HOUR       = "ONE_HOUR"
    ONE_DAY        = "ONE_DAY"


class ExchangeType:
    """WebSocket V2 exchange type codes."""
    NSE_CM  = 1   # NSE Cash Market
    NSE_FO  = 2   # NSE Futures & Options
    BSE_CM  = 3   # BSE Cash Market
    BSE_FO  = 4   # BSE Futures & Options
    MCX_FO  = 5   # MCX Commodity F&O
    NCX_FO  = 7   # NCX
    CDS_FO  = 13  # NSE Currency Derivatives


class WSMode:
    """WebSocket subscription modes."""
    LTP        = 1   # Last Traded Price only
    QUOTE      = 2   # Quote data
    SNAP_QUOTE = 3   # Full market depth + OHLCV


class GTTStatus:
    FORALL           = "FORALL"
    NEW              = "NEW"
    CANCELLED        = "CANCELLED"
    ACTIVE           = "ACTIVE"
    SENTTOEXCHANGE   = "SENTTOEXCHANGE"
    FORDELETE        = "FORDELETE"
    INVALID          = "INVALID"
    TRIGGERED        = "TRIGGERED"


# ──────────────────────────────────────────────────────────────────────────────
# BROKERAGE & TAX RATES  (AngelOne, April 2026)
# Source: https://www.angelone.in/exchange-transaction-charges
# ──────────────────────────────────────────────────────────────────────────────

class ChargeRates:
    """
    All rates sourced from AngelOne's official pricing page.
    Update these values if AngelOne changes their fee structure.
    AngelOne provides advance notice of 30 days for any rate changes.
    """

    # ── Brokerage ────────────────────────────────────────────────────────────
    # Equity cash segments: lower of ₹20 flat OR 0.1% of trade value (min ₹5)
    BROKERAGE_CASH_FLAT_MAX         = 20.0    # ₹20 per executed order
    BROKERAGE_CASH_PERCENT          = 0.001   # 0.1% of turnover
    BROKERAGE_CASH_MIN              = 5.0     # ₹5 minimum brokerage

    # Derivatives / currency / commodity: flat ₹20 per executed order
    BROKERAGE_DERIVATIVES_FLAT      = 20.0

    # ── Securities Transaction Tax (STT) ─────────────────────────────────────
    # Equity delivery: 0.1% on BOTH buy and sell sides
    STT_EQUITY_DELIVERY             = 0.001   # both sides
    # Equity intraday: 0.025% on SELL side only
    STT_EQUITY_INTRADAY_SELL        = 0.00025
    # Equity futures: 0.05% on SELL side
    STT_FUTURES_SELL                = 0.0005
    # Equity options: 0.15% on SELL side (on premium value)
    STT_OPTIONS_SELL_PREMIUM        = 0.0015

    # ── Exchange Transaction Charges (NSE) ───────────────────────────────────
    # NSE Equity Delivery & Intraday: 0.0030699% of turnover (buy + sell)
    TXN_NSE_EQUITY                  = 0.000030699   # NOTE: applied on total turnover
    # NSE Futures: 0.0018299% of turnover
    TXN_NSE_FUTURES                 = 0.000018299
    # NSE Options: 0.03552% of premium turnover
    TXN_NSE_OPTIONS                 = 0.0003552
    # NSE Currency Futures: 0.00035% of turnover
    TXN_NSE_CURRENCY_FUTURES        = 0.0000035
    # NSE Currency Options: 0.0311% of premium turnover
    TXN_NSE_CURRENCY_OPTIONS        = 0.000311
    # MCX commodity futures: 0.00210% of turnover
    TXN_MCX_COMMODITY_FUTURES       = 0.000021
    # MCX commodity options: 0.0418% of premium turnover
    TXN_MCX_COMMODITY_OPTIONS       = 0.000418

    # BSE Equity: varies by scrip group; use NSE rate as approximation for NSE stocks
    TXN_BSE_EQUITY                  = 0.0000375      # 0.00375% approximation

    # ── SEBI Turnover Fees ───────────────────────────────────────────────────
    # ₹10 per crore on both buy and sell (= 0.000010% of turnover)
    SEBI_TURNOVER_RATE              = 0.0000001      # per rupee of turnover

    # ── GST ──────────────────────────────────────────────────────────────────
    # 18% on (brokerage + exchange transaction charges + SEBI fees + IPFT)
    GST_RATE                        = 0.18

    # ── Stamp Duty ───────────────────────────────────────────────────────────
    # Charged on BUY side only, varies by segment
    # Equity Delivery: 0.015% of buy turnover
    STAMP_DUTY_EQUITY_DELIVERY      = 0.00015
    # Equity Intraday: 0.003% of buy turnover
    STAMP_DUTY_EQUITY_INTRADAY      = 0.00003
    # Futures: 0.002% of buy turnover
    STAMP_DUTY_FUTURES              = 0.00002
    # Options: 0.003% of buy premium turnover
    STAMP_DUTY_OPTIONS              = 0.00003
    # Currency futures: 0.0001% of buy turnover
    STAMP_DUTY_CURRENCY_FUTURES     = 0.000001
    # Currency options: 0.003% of buy premium turnover
    STAMP_DUTY_CURRENCY_OPTIONS     = 0.00003

    # Investor Protection Fund Trust (IPFT)
    IPFT_EQUITY_RATE                = 0.000000001   # 0.0000001%
    IPFT_FUTURES_OPTIONS_RATE       = 0.000000001   # 0.0000001%
    IPFT_CURRENCY_FUTURES_RATE      = 0.0000005     # 0.00005%
    IPFT_CURRENCY_OPTIONS_RATE      = 0.00002       # 0.002%
    IPFT_COMMODITY_RATE             = 0.0

    # ── DP (Depository Participant) Charges ──────────────────────────────────
    # ₹20 + GST per scrip per transaction, on SELL side of Equity Delivery only
    # No DP charges on intraday, F&O
    DP_CHARGE_PER_SCRIP             = 20.0           # ₹20 flat
    DP_CHARGE_WITH_GST              = DP_CHARGE_PER_SCRIP * (1 + GST_RATE)   # ₹23.60


# ──────────────────────────────────────────────────────────────────────────────
# RATE LIMITS  (SEBI mandate, Apr 2026)
# ──────────────────────────────────────────────────────────────────────────────

class RateLimits:
    ORDERS_PER_SECOND   = 10   # max 10 order API calls per second per exchange/segment
    ORDER_STATUS_PER_SEC = 10  # individual order status endpoint: 10 req/sec


# ──────────────────────────────────────────────────────────────────────────────
# MISC
# ──────────────────────────────────────────────────────────────────────────────

PAISE_PER_RUPEE = 100   # all WebSocket prices & GTT prices are in paise
REQUEST_TIMEOUT = 10    # seconds; default HTTP request timeout
