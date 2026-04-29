"""
charges.py - Angel One brokerage and statutory charge calculator.
"""

from dataclasses import asdict, dataclass

from broker.constants import ChargeRates
from utils import format_price, get_logger

_log = get_logger(__name__)


class Segment:
    """Trading segment identifiers."""

    EQUITY_DELIVERY = "equity_delivery"
    EQUITY_INTRADAY = "equity_intraday"
    EQUITY_FUTURES = "equity_futures"
    EQUITY_OPTIONS = "equity_options"
    CURRENCY_FUTURES = "currency_futures"
    CURRENCY_OPTIONS = "currency_options"
    COMMODITY_FUTURES = "commodity_futures"
    COMMODITY_OPTIONS = "commodity_options"
    COMMODITY = COMMODITY_FUTURES


@dataclass
class ChargeBreakdown:
    buy_value: float = 0.0
    sell_value: float = 0.0
    brokerage_buy: float = 0.0
    brokerage_sell: float = 0.0
    stt: float = 0.0
    exchange_charges: float = 0.0
    sebi_charges: float = 0.0
    ipft_charges: float = 0.0
    gst: float = 0.0
    stamp_duty: float = 0.0
    dp_charges: float = 0.0
    total_charges: float = 0.0
    net_pnl: float = 0.0
    breakeven_move: float = 0.0
    breakeven_price: float = 0.0
    effective_buy_price: float = 0.0
    effective_sell_price: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def __str__(self) -> str:
        lines = [
            "-" * 45,
            "  Angel One Trade Charge Breakdown",
            "-" * 45,
            f"  Buy value          : {format_price(self.buy_value)}",
            f"  Sell value         : {format_price(self.sell_value)}",
            "  -------------------------------------",
            f"  Brokerage (buy)    : {format_price(self.brokerage_buy)}",
            f"  Brokerage (sell)   : {format_price(self.brokerage_sell)}",
            f"  STT                : {format_price(self.stt)}",
            f"  Exchange charges   : {format_price(self.exchange_charges)}",
            f"  SEBI turnover fee  : {format_price(self.sebi_charges)}",
            f"  IPFT charges       : {format_price(self.ipft_charges)}",
            f"  GST (18%)          : {format_price(self.gst)}",
            f"  Stamp duty         : {format_price(self.stamp_duty)}",
            f"  DP charges         : {format_price(self.dp_charges)}",
            "  -------------------------------------",
            f"  TOTAL CHARGES      : {format_price(self.total_charges)}",
            f"  NET P&L            : {format_price(self.net_pnl)}",
            f"  Breakeven move     : {format_price(self.breakeven_move)} / share",
            f"  Breakeven price    : {format_price(self.breakeven_price)}",
            "-" * 45,
        ]
        return "\n".join(lines)


def _calc_brokerage(turnover: float, segment: str) -> float:
    """Calculate brokerage for one executed leg."""
    if segment in (Segment.EQUITY_DELIVERY, Segment.EQUITY_INTRADAY):
        flat_fee = ChargeRates.BROKERAGE_CASH_FLAT_MAX
        pct_fee = turnover * ChargeRates.BROKERAGE_CASH_PERCENT
        return max(min(flat_fee, pct_fee), ChargeRates.BROKERAGE_CASH_MIN)
    return ChargeRates.BROKERAGE_DERIVATIVES_FLAT


def _calc_stt(
    buy_turnover: float,
    sell_turnover: float,
    segment: str,
    premium_value: float = 0.0,
) -> float:
    """Calculate STT for supported cash/F&O segments."""
    r = ChargeRates
    stt = 0.0

    if segment == Segment.EQUITY_DELIVERY:
        stt = (buy_turnover + sell_turnover) * r.STT_EQUITY_DELIVERY
    elif segment == Segment.EQUITY_INTRADAY:
        stt = sell_turnover * r.STT_EQUITY_INTRADAY_SELL
    elif segment == Segment.EQUITY_FUTURES:
        stt = sell_turnover * r.STT_FUTURES_SELL
    elif segment == Segment.EQUITY_OPTIONS:
        stt = premium_value * r.STT_OPTIONS_SELL_PREMIUM

    return round(stt, 2)


def _calc_exchange_charges(
    total_turnover: float,
    segment: str,
    exchange: str = "NSE",
) -> float:
    """Calculate exchange transaction charges."""
    r = ChargeRates
    exchange = exchange.upper()

    if segment in (Segment.EQUITY_DELIVERY, Segment.EQUITY_INTRADAY):
        rate = r.TXN_NSE_EQUITY if exchange == "NSE" else r.TXN_BSE_EQUITY
    elif segment == Segment.EQUITY_FUTURES:
        rate = r.TXN_NSE_FUTURES if exchange == "NFO" else r.TXN_NSE_FUTURES
    elif segment == Segment.EQUITY_OPTIONS:
        rate = r.TXN_NSE_OPTIONS
    elif segment == Segment.CURRENCY_FUTURES:
        rate = r.TXN_NSE_CURRENCY_FUTURES
    elif segment == Segment.CURRENCY_OPTIONS:
        rate = r.TXN_NSE_CURRENCY_OPTIONS
    elif segment == Segment.COMMODITY_OPTIONS:
        rate = r.TXN_MCX_COMMODITY_OPTIONS
    elif segment == Segment.COMMODITY_FUTURES:
        rate = r.TXN_MCX_COMMODITY_FUTURES
    else:
        rate = r.TXN_NSE_EQUITY

    return round(total_turnover * rate, 2)


def _calc_sebi_charges(total_turnover: float) -> float:
    return round(total_turnover * ChargeRates.SEBI_TURNOVER_RATE, 2)


def _calc_ipft_charges(total_turnover: float, segment: str) -> float:
    r = ChargeRates
    rates = {
        Segment.EQUITY_DELIVERY: r.IPFT_EQUITY_RATE,
        Segment.EQUITY_INTRADAY: r.IPFT_EQUITY_RATE,
        Segment.EQUITY_FUTURES: r.IPFT_FUTURES_OPTIONS_RATE,
        Segment.EQUITY_OPTIONS: r.IPFT_FUTURES_OPTIONS_RATE,
        Segment.CURRENCY_FUTURES: r.IPFT_CURRENCY_FUTURES_RATE,
        Segment.CURRENCY_OPTIONS: r.IPFT_CURRENCY_OPTIONS_RATE,
        Segment.COMMODITY_FUTURES: r.IPFT_COMMODITY_RATE,
        Segment.COMMODITY_OPTIONS: r.IPFT_COMMODITY_RATE,
    }
    return round(total_turnover * rates.get(segment, 0.0), 2)


def _calc_stamp_duty(buy_turnover: float, segment: str) -> float:
    r = ChargeRates
    rates = {
        Segment.EQUITY_DELIVERY: r.STAMP_DUTY_EQUITY_DELIVERY,
        Segment.EQUITY_INTRADAY: r.STAMP_DUTY_EQUITY_INTRADAY,
        Segment.EQUITY_FUTURES: r.STAMP_DUTY_FUTURES,
        Segment.EQUITY_OPTIONS: r.STAMP_DUTY_OPTIONS,
        Segment.CURRENCY_FUTURES: r.STAMP_DUTY_CURRENCY_FUTURES,
        Segment.CURRENCY_OPTIONS: r.STAMP_DUTY_CURRENCY_OPTIONS,
        Segment.COMMODITY_FUTURES: r.STAMP_DUTY_FUTURES,
        Segment.COMMODITY_OPTIONS: r.STAMP_DUTY_OPTIONS,
    }
    return round(buy_turnover * rates.get(segment, r.STAMP_DUTY_EQUITY_INTRADAY), 2)


def calculate_charges(
    segment: str,
    buy_price: float,
    sell_price: float,
    quantity: int,
    exchange: str = "NSE",
    is_sell: bool = True,
    premium_value: float = 0.0,
) -> ChargeBreakdown:
    """Calculate round-trip trade charges."""
    r = ChargeRates
    buy_turnover = buy_price * quantity
    sell_turnover = sell_price * quantity
    total_turnover = buy_turnover + sell_turnover

    premium_segments = {
        Segment.EQUITY_OPTIONS,
        Segment.CURRENCY_OPTIONS,
        Segment.COMMODITY_OPTIONS,
    }
    if segment in premium_segments and premium_value == 0:
        premium_value = sell_turnover

    brokerage_buy = _calc_brokerage(buy_turnover, segment)
    brokerage_sell = _calc_brokerage(sell_turnover, segment)
    stt = _calc_stt(buy_turnover, sell_turnover, segment, premium_value)

    exchange_base = premium_value if segment in premium_segments else total_turnover
    exchange_charges = _calc_exchange_charges(exchange_base, segment, exchange)
    sebi_charges = _calc_sebi_charges(total_turnover)
    ipft_charges = _calc_ipft_charges(exchange_base, segment)
    gst = round(
        (brokerage_buy + brokerage_sell + exchange_charges + sebi_charges + ipft_charges)
        * r.GST_RATE,
        2,
    )
    stamp_duty = _calc_stamp_duty(buy_turnover, segment)

    dp_charges = 0.0
    if segment == Segment.EQUITY_DELIVERY and is_sell:
        dp_charges = round(r.DP_CHARGE_WITH_GST, 2)

    total_charges = round(
        brokerage_buy
        + brokerage_sell
        + stt
        + exchange_charges
        + sebi_charges
        + ipft_charges
        + gst
        + stamp_duty
        + dp_charges,
        2,
    )

    gross_pnl = sell_turnover - buy_turnover
    net_pnl = round(gross_pnl - total_charges, 2)
    breakeven_move = round(total_charges / quantity, 4) if quantity else 0.0
    breakeven_price = round(buy_price + breakeven_move, 2)

    buy_side_cost = brokerage_buy + stamp_duty
    sell_side_cost = brokerage_sell + dp_charges
    if quantity:
        effective_buy_price = round(buy_price + (buy_side_cost / quantity), 4)
        effective_sell_price = round(sell_price - (sell_side_cost / quantity), 4)
    else:
        effective_buy_price = buy_price
        effective_sell_price = sell_price

    result = ChargeBreakdown(
        buy_value=round(buy_turnover, 2),
        sell_value=round(sell_turnover, 2),
        brokerage_buy=round(brokerage_buy, 2),
        brokerage_sell=round(brokerage_sell, 2),
        stt=stt,
        exchange_charges=exchange_charges,
        sebi_charges=sebi_charges,
        ipft_charges=ipft_charges,
        gst=gst,
        stamp_duty=stamp_duty,
        dp_charges=dp_charges,
        total_charges=total_charges,
        net_pnl=net_pnl,
        breakeven_move=breakeven_move,
        breakeven_price=breakeven_price,
        effective_buy_price=effective_buy_price,
        effective_sell_price=effective_sell_price,
    )

    _log.debug(
        "Charges calculated [%s] qty=%d buy=Rs%.2f sell=Rs%.2f -> total=Rs%.2f net_pnl=Rs%.2f",
        segment,
        quantity,
        buy_price,
        sell_price,
        total_charges,
        net_pnl,
    )
    return result


def estimate_charges_buy_only(
    segment: str,
    buy_price: float,
    quantity: int,
    exchange: str = "NSE",
) -> float:
    """Estimate buy-side charges before an exit price is known."""
    buy_turnover = buy_price * quantity
    brokerage = _calc_brokerage(buy_turnover, segment)
    stt = buy_turnover * ChargeRates.STT_EQUITY_DELIVERY if segment == Segment.EQUITY_DELIVERY else 0.0
    exchange_charges = _calc_exchange_charges(buy_turnover, segment, exchange)
    sebi_charges = _calc_sebi_charges(buy_turnover)
    ipft_charges = _calc_ipft_charges(buy_turnover, segment)
    gst = (
        brokerage + exchange_charges + sebi_charges + ipft_charges
    ) * ChargeRates.GST_RATE
    stamp_duty = _calc_stamp_duty(buy_turnover, segment)
    return round(
        brokerage + stt + exchange_charges + sebi_charges + ipft_charges + gst + stamp_duty,
        2,
    )


def net_pnl_after_charges(
    segment: str,
    buy_price: float,
    sell_price: float,
    quantity: int,
    exchange: str = "NSE",
) -> float:
    return calculate_charges(segment, buy_price, sell_price, quantity, exchange).net_pnl


def breakeven_price(
    segment: str,
    buy_price: float,
    quantity: int,
    exchange: str = "NSE",
) -> float:
    return calculate_charges(segment, buy_price, buy_price, quantity, exchange).breakeven_price