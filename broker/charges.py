"""
charges.py — AngelOne SmartAPI · Brokerage & Tax Calculator
============================================================
Calculate the complete cost of any trade before placing it:
brokerage, STT, exchange transaction charges, SEBI turnover fee,
GST, stamp duty, and DP charges.

Use this to:
  • Know your true break-even price before entering a trade
  • Log net P&L (after all charges) on closed positions
  • Build a position-sizer that accounts for round-trip costs

All rates sourced from AngelOne's official pricing page (April 2026).
Update config.ChargeRates when AngelOne revises their fee schedule.

Usage:
    from charges import calculate_charges, Segment

    result = calculate_charges(
        segment    = Segment.EQUITY_INTRADAY,
        buy_price  = 550.0,
        sell_price = 560.0,
        quantity   = 100,
        exchange   = "NSE",
    )
    print(result)

Dependencies:
    No external dependencies — uses only config.ChargeRates.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional

from broker.constants import ChargeRates
from utils import get_logger, format_price

_log = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# SEGMENT ENUM
# ──────────────────────────────────────────────────────────────────────────────

class Segment:
    """
    Trading segment identifiers — used to select the correct charge rates.
    Match the segment to the producttype and instrument you are trading.
    """
    EQUITY_DELIVERY  = "equity_delivery"    # CNC — multi-day cash equity
    EQUITY_INTRADAY  = "equity_intraday"    # MIS — same-day cash equity
    EQUITY_FUTURES   = "equity_futures"     # NFO futures (NRML / MIS)
    EQUITY_OPTIONS   = "equity_options"     # NFO options (NRML / MIS)
    CURRENCY_FUTURES = "currency_futures"   # CDS futures
    CURRENCY_OPTIONS = "currency_options"   # CDS options
    COMMODITY        = "commodity"          # MCX / NCDEX futures


# ──────────────────────────────────────────────────────────────────────────────
# CHARGE BREAKDOWN DATA CLASS
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ChargeBreakdown:
    """
    Full breakdown of all charges for a trade.

    All monetary values are in rupees (₹), rounded to 2 decimal places.

    Attributes:
        buy_value           : gross cost of buying (price × qty)
        sell_value          : gross proceeds of selling (price × qty)
        brokerage_buy       : brokerage on buy leg
        brokerage_sell      : brokerage on sell leg
        stt                 : securities transaction tax (total)
        exchange_charges    : exchange + clearing house transaction charges
        sebi_charges        : SEBI turnover fee
        gst                 : GST on (brokerage + exchange_charges + sebi_charges)
        stamp_duty          : stamp duty on buy side
        dp_charges          : CDSL DP charge (delivery sell only)
        total_charges       : sum of all charges
        net_pnl             : (sell_value - buy_value) - total_charges
        breakeven_move      : minimum price move needed to cover charges (₹ per share)
        breakeven_price     : sell price needed to break even from entry buy_price
        effective_buy_price : buy_price + per-share cost of buy-side charges
        effective_sell_price: sell_price - per-share cost of sell-side charges
    """
    # Turnover
    buy_value:            float = 0.0
    sell_value:           float = 0.0
    # Charges
    brokerage_buy:        float = 0.0
    brokerage_sell:       float = 0.0
    stt:                  float = 0.0
    exchange_charges:     float = 0.0
    sebi_charges:         float = 0.0
    gst:                  float = 0.0
    stamp_duty:           float = 0.0
    dp_charges:           float = 0.0
    # Totals
    total_charges:        float = 0.0
    net_pnl:              float = 0.0
    breakeven_move:       float = 0.0   # ₹ per share
    breakeven_price:      float = 0.0   # absolute sell price to break even
    effective_buy_price:  float = 0.0
    effective_sell_price: float = 0.0

    def to_dict(self) -> dict:
        """Return the breakdown as a plain dict (all values in ₹)."""
        return asdict(self)

    def __str__(self) -> str:
        """Pretty-print the charge breakdown."""
        lines = [
            "─" * 45,
            "  AngelOne Trade Charge Breakdown",
            "─" * 45,
            f"  Buy  value         : {format_price(self.buy_value)}",
            f"  Sell value         : {format_price(self.sell_value)}",
            "  ─────────────────────────────────────",
            f"  Brokerage (buy)    : {format_price(self.brokerage_buy)}",
            f"  Brokerage (sell)   : {format_price(self.brokerage_sell)}",
            f"  STT                : {format_price(self.stt)}",
            f"  Exchange charges   : {format_price(self.exchange_charges)}",
            f"  SEBI turnover fee  : {format_price(self.sebi_charges)}",
            f"  GST (18%)          : {format_price(self.gst)}",
            f"  Stamp duty         : {format_price(self.stamp_duty)}",
            f"  DP charges         : {format_price(self.dp_charges)}",
            "  ─────────────────────────────────────",
            f"  TOTAL CHARGES      : {format_price(self.total_charges)}",
            f"  NET P&L            : {format_price(self.net_pnl)}",
            f"  Breakeven move     : {format_price(self.breakeven_move)} / share",
            f"  Breakeven price    : {format_price(self.breakeven_price)}",
            "─" * 45,
        ]
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# BROKERAGE CALCULATOR
# ──────────────────────────────────────────────────────────────────────────────

def _calc_brokerage(turnover: float, segment: str) -> float:
    """
    Calculate brokerage for a single leg (buy or sell).

    Equity Delivery     : ₹0 (zero brokerage)
    All other segments  : lower of ₹20 flat OR 0.1% of turnover (min ₹5)

    Args:
        turnover : price × quantity (₹)
        segment  : Segment constant

    Returns:
        Brokerage amount in ₹
    """
    if segment == Segment.EQUITY_DELIVERY:
        return ChargeRates.BROKERAGE_EQUITY_DELIVERY   # ₹0

    flat_fee = ChargeRates.BROKERAGE_FLAT_MAX                        # ₹20
    pct_fee  = turnover * ChargeRates.BROKERAGE_PERCENT              # 0.1%
    brokerage = min(flat_fee, pct_fee)
    return max(brokerage, ChargeRates.BROKERAGE_MIN)                 # at least ₹5


def _calc_stt(
    buy_turnover:  float,
    sell_turnover: float,
    segment:       str,
    premium_value: float = 0.0,
) -> float:
    """
    Calculate Securities Transaction Tax (STT).

    Args:
        buy_turnover  : buy price × qty (₹)
        sell_turnover : sell price × qty (₹)
        segment       : Segment constant
        premium_value : options premium turnover (for options STT, ₹)

    Returns:
        Total STT amount in ₹
    """
    r = ChargeRates
    stt = 0.0

    if segment == Segment.EQUITY_DELIVERY:
        # 0.1% on BOTH buy and sell
        stt = (buy_turnover + sell_turnover) * r.STT_EQUITY_DELIVERY

    elif segment == Segment.EQUITY_INTRADAY:
        # 0.025% on sell side only
        stt = sell_turnover * r.STT_EQUITY_INTRADAY_SELL

    elif segment == Segment.EQUITY_FUTURES:
        # 0.02% on sell side
        stt = sell_turnover * r.STT_FUTURES_SELL

    elif segment == Segment.EQUITY_OPTIONS:
        # 0.1% on sell premium value
        stt = premium_value * r.STT_OPTIONS_SELL_PREMIUM

    # Currency and commodity: no STT (CTT for commodity is separate, minimal)
    return round(stt, 2)


def _calc_exchange_charges(
    total_turnover: float,
    segment:        str,
    exchange:       str = "NSE",
) -> float:
    """
    Calculate exchange + clearing house transaction charges.

    Args:
        total_turnover : buy + sell turnover (₹) — or premium for options
        segment        : Segment constant
        exchange       : "NSE" or "BSE"

    Returns:
        Exchange charges in ₹
    """
    r = ChargeRates

    if segment in (Segment.EQUITY_DELIVERY, Segment.EQUITY_INTRADAY):
        rate = r.TXN_NSE_EQUITY if exchange.upper() == "NSE" else r.TXN_BSE_EQUITY

    elif segment == Segment.EQUITY_FUTURES:
        rate = r.TXN_NSE_FUTURES

    elif segment == Segment.EQUITY_OPTIONS:
        rate = r.TXN_NSE_OPTIONS

    else:
        rate = r.TXN_NSE_EQUITY   # fallback

    return round(total_turnover * rate, 2)


def _calc_sebi_charges(total_turnover: float) -> float:
    """
    SEBI turnover fee: ₹10 per crore on all segments.
    = 0.0000001 per rupee of turnover.

    Args:
        total_turnover : buy + sell turnover (₹)
    Returns:
        SEBI charge in ₹
    """
    return round(total_turnover * ChargeRates.SEBI_TURNOVER_RATE, 2)


def _calc_stamp_duty(buy_turnover: float, segment: str) -> float:
    """
    Stamp duty on BUY side only.

    Args:
        buy_turnover : buy price × qty (₹)
        segment      : Segment constant

    Returns:
        Stamp duty in ₹
    """
    r = ChargeRates
    rates = {
        Segment.EQUITY_DELIVERY:  r.STAMP_DUTY_EQUITY_DELIVERY,
        Segment.EQUITY_INTRADAY:  r.STAMP_DUTY_EQUITY_INTRADAY,
        Segment.EQUITY_FUTURES:   r.STAMP_DUTY_FUTURES,
        Segment.EQUITY_OPTIONS:   r.STAMP_DUTY_OPTIONS,
        Segment.CURRENCY_FUTURES: r.STAMP_DUTY_FUTURES,
        Segment.CURRENCY_OPTIONS: r.STAMP_DUTY_OPTIONS,
        Segment.COMMODITY:        r.STAMP_DUTY_FUTURES,
    }
    rate = rates.get(segment, r.STAMP_DUTY_EQUITY_INTRADAY)
    return round(buy_turnover * rate, 2)


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────────────────────────

def calculate_charges(
    segment:       str,
    buy_price:     float,
    sell_price:    float,
    quantity:      int,
    exchange:      str   = "NSE",
    is_sell:       bool  = True,
    premium_value: float = 0.0,
) -> ChargeBreakdown:
    """
    Calculate the complete charge breakdown for a round-trip trade.

    Handles all segments: equity delivery/intraday, futures, options, currency, commodity.

    Args:
        segment       : Segment constant (e.g. Segment.EQUITY_INTRADAY)
        buy_price     : entry / buy price per share (₹)
        sell_price    : exit / sell price per share (₹)
        quantity      : number of shares / lots
        exchange      : "NSE" or "BSE" (affects exchange charges)
        is_sell       : True = you are selling (DP charges apply for delivery)
        premium_value : for options — total premium paid/received (price × qty)
                        If 0, it defaults to sell_price × quantity

    Returns:
        ChargeBreakdown dataclass with full itemised charges + net P&L

    Example:
        # Intraday trade: buy 100 SBIN @ 550, sell @ 558
        result = calculate_charges(
            segment   = Segment.EQUITY_INTRADAY,
            buy_price = 550.0,
            sell_price= 558.0,
            quantity  = 100,
        )
        print(result)
        print(f"Net P&L: ₹{result.net_pnl}")

        # Options trade: buy 1 lot NIFTY 24000 CE @ premium 200, sell @ 250
        result = calculate_charges(
            segment       = Segment.EQUITY_OPTIONS,
            buy_price     = 200.0,
            sell_price    = 250.0,
            quantity      = 50,               # lot size
            premium_value = 250.0 * 50,       # sell-side premium
        )
    """
    r = ChargeRates

    buy_turnover  = buy_price  * quantity
    sell_turnover = sell_price * quantity
    total_turnover = buy_turnover + sell_turnover

    # For options STT, use premium value if provided
    if segment == Segment.EQUITY_OPTIONS and premium_value == 0:
        premium_value = sell_turnover

    # ── Brokerage ────────────────────────────────────────────────────────────
    brokerage_buy  = _calc_brokerage(buy_turnover,  segment)
    brokerage_sell = _calc_brokerage(sell_turnover, segment)

    # ── STT ──────────────────────────────────────────────────────────────────
    stt = _calc_stt(buy_turnover, sell_turnover, segment, premium_value)

    # ── Exchange Charges ─────────────────────────────────────────────────────
    exc_base = premium_value if segment == Segment.EQUITY_OPTIONS else total_turnover
    exchange_charges = _calc_exchange_charges(exc_base, segment, exchange)

    # ── SEBI Turnover Fee ─────────────────────────────────────────────────────
    sebi_charges = _calc_sebi_charges(total_turnover)

    # ── GST — 18% on (brokerage + exchange charges + SEBI charges) ───────────
    gst_base = brokerage_buy + brokerage_sell + exchange_charges + sebi_charges
    gst = round(gst_base * r.GST_RATE, 2)

    # ── Stamp Duty ────────────────────────────────────────────────────────────
    stamp_duty = _calc_stamp_duty(buy_turnover, segment)

    # ── DP Charges — only on delivery SELL side ───────────────────────────────
    dp_charges = 0.0
    if segment == Segment.EQUITY_DELIVERY and is_sell:
        dp_charges = round(r.DP_CHARGE_WITH_GST, 2)   # ₹23.60 per scrip

    # ── Totals ────────────────────────────────────────────────────────────────
    total_charges = round(
        brokerage_buy + brokerage_sell + stt + exchange_charges +
        sebi_charges + gst + stamp_duty + dp_charges,
        2
    )

    gross_pnl = sell_turnover - buy_turnover
    net_pnl   = round(gross_pnl - total_charges, 2)

    # ── Breakeven analysis ────────────────────────────────────────────────────
    # How many rupees per share must the price move to cover all charges?
    breakeven_move  = round(total_charges / quantity, 4) if quantity else 0.0
    breakeven_price = round(buy_price + breakeven_move, 2)

    # Effective prices (buy slightly higher, sell slightly lower due to charges)
    effective_buy_price  = round(buy_price  + (brokerage_buy  + stamp_duty + stt * (buy_turnover  / total_turnover if total_turnover else 0.5)) / quantity, 4) if quantity else buy_price
    effective_sell_price = round(sell_price - (brokerage_sell + dp_charges + stt * (sell_turnover / total_turnover if total_turnover else 0.5)) / quantity, 4) if quantity else sell_price

    result = ChargeBreakdown(
        buy_value            = round(buy_turnover,    2),
        sell_value           = round(sell_turnover,   2),
        brokerage_buy        = round(brokerage_buy,   2),
        brokerage_sell       = round(brokerage_sell,  2),
        stt                  = stt,
        exchange_charges     = exchange_charges,
        sebi_charges         = sebi_charges,
        gst                  = gst,
        stamp_duty           = stamp_duty,
        dp_charges           = dp_charges,
        total_charges        = total_charges,
        net_pnl              = net_pnl,
        breakeven_move       = breakeven_move,
        breakeven_price      = breakeven_price,
        effective_buy_price  = effective_buy_price,
        effective_sell_price = effective_sell_price,
    )

    _log.debug(
        "Charges calculated [%s] qty=%d buy=₹%.2f sell=₹%.2f → total=₹%.2f net_pnl=₹%.2f",
        segment, quantity, buy_price, sell_price, total_charges, net_pnl
    )
    return result


def estimate_charges_buy_only(
    segment:   str,
    buy_price: float,
    quantity:  int,
    exchange:  str = "NSE",
) -> float:
    """
    Estimate charges for the BUY leg only (before you know the exit price).
    Useful for position sizing — know your entry cost upfront.

    Args:
        segment   : Segment constant
        buy_price : entry price per share (₹)
        quantity  : number of shares
        exchange  : "NSE" or "BSE"

    Returns:
        Estimated buy-side charges in ₹

    Example:
        entry_cost = estimate_charges_buy_only(
            Segment.EQUITY_DELIVERY, buy_price=550.0, quantity=100
        )
        print(f"Buying 100 SBIN at ₹550 costs ₹{entry_cost:.2f} in charges")
    """
    buy_turnover = buy_price * quantity
    brokerage    = _calc_brokerage(buy_turnover, segment)

    # STT: delivery = 0.1% on buy, intraday = 0 on buy
    stt = buy_turnover * ChargeRates.STT_EQUITY_DELIVERY if segment == Segment.EQUITY_DELIVERY else 0.0

    exc = _calc_exchange_charges(buy_turnover, segment, exchange)
    sebi = _calc_sebi_charges(buy_turnover)
    gst  = (brokerage + exc + sebi) * ChargeRates.GST_RATE
    stamp = _calc_stamp_duty(buy_turnover, segment)

    return round(brokerage + stt + exc + sebi + gst + stamp, 2)


def net_pnl_after_charges(
    segment:    str,
    buy_price:  float,
    sell_price: float,
    quantity:   int,
    exchange:   str = "NSE",
) -> float:
    """
    Quick shorthand — returns only the net P&L after all charges.

    Args:
        segment    : Segment constant
        buy_price  : entry price per share (₹)
        sell_price : exit price per share (₹)
        quantity   : number of shares
        exchange   : "NSE" or "BSE"

    Returns:
        Net P&L in ₹ (negative = loss after charges)

    Example:
        pnl = net_pnl_after_charges(Segment.EQUITY_INTRADAY, 550, 558, 100)
        print(f"Net P&L: ₹{pnl}")
    """
    result = calculate_charges(segment, buy_price, sell_price, quantity, exchange)
    return result.net_pnl


def breakeven_price(
    segment:   str,
    buy_price: float,
    quantity:  int,
    exchange:  str = "NSE",
) -> float:
    """
    Calculate the minimum sell price needed to break even (cover all charges).

    Args:
        segment   : Segment constant
        buy_price : entry price per share (₹)
        quantity  : number of shares
        exchange  : "NSE" or "BSE"

    Returns:
        Break-even sell price per share (₹)

    Example:
        be = breakeven_price(Segment.EQUITY_INTRADAY, buy_price=550.0, quantity=100)
        print(f"Need to sell above ₹{be:.2f} to profit")
    """
    # Use buy price as estimate for sell price (conservative — actual will be slightly higher)
    result = calculate_charges(segment, buy_price, buy_price, quantity, exchange)
    return result.breakeven_price
