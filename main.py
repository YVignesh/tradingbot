"""
main.py — Bot Runner
=====================
Loads strategy from config.json, wires up broker feeds,
and runs the strategy loop until stopped.

Usage:
    python main.py
    python main.py --config config.json
"""

import signal
import json
import time
import logging
import argparse
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from broker.session import AngelSession
from broker.market_data import get_ltp_single, is_market_open, minutes_to_market_open
from broker.websocket_feed import MarketFeed, OrderFeed
from broker.orders import (
    buy, sell,
    place_stop_loss_market,
    cancel_order,
    get_order_status,
)
from broker.constants import ExchangeType, TransactionType
from strategies.base import BaseStrategy
from strategies.ema_crossover import EmaCrossoverStrategy
from risk.manager import RiskManager
from utils import get_logger, AngelOneAPIError

IST = timezone(timedelta(hours=5, minutes=30))

# ── Strategy registry ─────────────────────────────────────────────────────────
# Add new strategies here as they are implemented.

STRATEGIES: dict[str, type[BaseStrategy]] = {
    "ema_crossover": EmaCrossoverStrategy,
}


# ── Logging setup ─────────────────────────────────────────────────────────────

def setup_logging(log_level: str = "INFO") -> None:
    """Add a daily rotating file handler alongside the console handler."""
    today   = datetime.now(IST).strftime("%Y-%m-%d")
    log_dir = Path("logs") / today
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bot.log"

    fmt = logging.Formatter(
        "%(asctime)s  [%(levelname)-8s]  %(name)s  — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root.addHandler(fh)

    logging.getLogger(__name__).info("Logging to %s", log_file)


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: str = "config.json") -> dict:
    with open(path) as f:
        config = json.load(f)
    # Validate required top-level keys
    for section in ("bot", "strategy", "risk", "broker"):
        if section not in config:
            raise KeyError(f"Missing required section '{section}' in {path}")
    return config


def load_strategy(config: dict) -> BaseStrategy:
    name = config["strategy"]["name"]
    cls  = STRATEGIES.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown strategy {name!r}. Available: {sorted(STRATEGIES)}"
        )
    return cls(config)


# ── Session refresh ───────────────────────────────────────────────────────────

def _session_refresh_loop(
    session:    AngelSession,
    stop_event: threading.Event,
) -> None:
    log = get_logger("session_refresh")
    while not stop_event.wait(timeout=1800):   # check every 30 minutes
        try:
            if session.refresh_if_needed(warn_minutes=60):
                log.info("Session token refreshed proactively")
        except Exception as e:
            log.error("Token refresh failed: %s", e)


# ── Fill polling ──────────────────────────────────────────────────────────────

def wait_for_fill(
    session:         AngelSession,
    unique_order_id: str,
    timeout_sec:     int = 60,
    poll_interval:   int = 2,
) -> Optional[dict]:
    """
    Poll order status until filled or rejected.
    Used as a fallback when OrderFeed (WebSocket) is unavailable (403).

    Returns the filled order dict on success, None on rejection or timeout.
    """
    log      = get_logger("wait_for_fill")
    deadline = time.monotonic() + timeout_sec

    while time.monotonic() < deadline:
        try:
            status = get_order_status(session, unique_order_id)
            state  = str(status.get("status", "")).lower()
            if state == "complete":
                return status
            if state in ("rejected", "cancelled"):
                log.warning(
                    "Order %s %s — %s",
                    unique_order_id, state, status.get("text", "no reason given"),
                )
                return None
        except AngelOneAPIError as e:
            log.warning("Status poll error: %s", e)
        time.sleep(poll_interval)

    log.error("Order %s timed out waiting for fill (%ds)", unique_order_id, timeout_sec)
    return None


# ── SL management ─────────────────────────────────────────────────────────────

def _place_sl(
    session:      AngelSession,
    strategy:     EmaCrossoverStrategy,
    entry_price:  float,
    qty:          int,
) -> Optional[str]:
    """Place a hard-floor SELL stop-loss below entry price (for long positions)."""
    log        = get_logger("sl_manager")
    sl_trigger = round(entry_price - strategy.sl_points, 2)
    try:
        result   = place_stop_loss_market(
            session, strategy.symbol, strategy.token,
            quantity         = qty,
            trigger_price    = sl_trigger,
            transaction_type = TransactionType.SELL,
            product_type     = strategy.product,
            order_tag        = "sl_long",
        )
        order_id = result.get("orderid", "")
        log.info("Long SL placed — trigger=₹%.2f  orderid=%s", sl_trigger, order_id)
        return order_id
    except AngelOneAPIError as e:
        log.error("SL order failed (no stop-loss active): %s", e)
        return None


def _place_sl_short(
    session:      AngelSession,
    strategy:     EmaCrossoverStrategy,
    entry_price:  float,
    qty:          int,
) -> Optional[str]:
    """Place a hard-floor BUY stop-loss above entry price (for short positions)."""
    log        = get_logger("sl_manager")
    sl_trigger = round(entry_price + strategy.sl_points, 2)
    try:
        result   = place_stop_loss_market(
            session, strategy.symbol, strategy.token,
            quantity         = qty,
            trigger_price    = sl_trigger,
            transaction_type = TransactionType.BUY,
            product_type     = strategy.product,
            order_tag        = "sl_short",
        )
        order_id = result.get("orderid", "")
        log.info("Short SL placed — trigger=₹%.2f  orderid=%s", sl_trigger, order_id)
        return order_id
    except AngelOneAPIError as e:
        log.error("Short SL order failed (no stop-loss active): %s", e)
        return None


def _cancel_sl(session: AngelSession, sl_order_id: str) -> None:
    log = get_logger("sl_manager")
    try:
        cancel_order(session, sl_order_id)
        log.info("SL order %s cancelled", sl_order_id)
    except AngelOneAPIError as e:
        log.warning("Could not cancel SL order %s: %s", sl_order_id, e)


# ── Order execution ───────────────────────────────────────────────────────────

def execute_buy(
    session:  AngelSession,
    strategy: EmaCrossoverStrategy,
    risk_mgr: RiskManager,
    ltp:      float,
    dry_run:  bool,
) -> Optional[str]:
    """
    Place a market buy order (or simulate in DRY_RUN).
    Returns the SL orderid if one was placed (LIVE only), else None.
    """
    log = get_logger("execute_buy")
    qty = risk_mgr.position_size(ltp)

    if dry_run:
        log.info("[DRY_RUN] BUY %s  qty=%d  ltp=₹%.2f", strategy.symbol, qty, ltp)
        strategy.on_fill({
            "status":          "complete",
            "transactiontype": "BUY",
            "averageprice":    str(ltp),
            "filledshares":    str(qty),
            "uniqueorderid":   "DRY-BUY",
        })
        return None  # no real SL order in dry run

    try:
        result    = buy(
            session, strategy.symbol, strategy.token,
            quantity     = qty,
            product_type = strategy.product,
            order_tag    = "entry",
        )
        unique_id = result.get("uniqueorderid", "")
        log.info("BUY order placed — uniqueorderid=%s", unique_id)

        fill = wait_for_fill(session, unique_id)
        if not fill:
            log.error("BUY order did not fill — no position opened")
            return None

        strategy.on_fill(fill)
        entry_price = float(fill.get("averageprice", ltp))
        sl_order_id = _place_sl(session, strategy, entry_price, qty)
        return sl_order_id

    except AngelOneAPIError as e:
        log.error("BUY order failed: %s", e)
        return None


def execute_sell(
    session:     AngelSession,
    strategy:    EmaCrossoverStrategy,
    risk_mgr:    RiskManager,
    ltp:         float,
    dry_run:     bool,
    sl_order_id: Optional[str] = None,
) -> None:
    """Cancel any outstanding SL and place a market sell (or simulate in DRY_RUN)."""
    log = get_logger("execute_sell")

    # Capture before on_fill resets position state
    entry_price = strategy.entry_price
    entry_qty   = strategy.entry_qty

    if dry_run:
        pnl = (ltp - entry_price) * entry_qty
        log.info("[DRY_RUN] SELL %s  qty=%d  ltp=₹%.2f  P&L=₹%.2f",
                 strategy.symbol, entry_qty, ltp, pnl)
        risk_mgr.record_trade(pnl)
        strategy.on_fill({
            "status":          "complete",
            "transactiontype": "SELL",
            "averageprice":    str(ltp),
            "filledshares":    str(entry_qty),
            "uniqueorderid":   "DRY-SELL",
        })
        return

    if sl_order_id:
        _cancel_sl(session, sl_order_id)

    try:
        result    = sell(
            session, strategy.symbol, strategy.token,
            quantity     = entry_qty,
            product_type = strategy.product,
            order_tag    = "exit",
        )
        unique_id = result.get("uniqueorderid", "")
        log.info("SELL order placed — uniqueorderid=%s", unique_id)

        fill = wait_for_fill(session, unique_id)
        if fill:
            exit_price = float(fill.get("averageprice", ltp))
            pnl        = (exit_price - entry_price) * entry_qty
            risk_mgr.record_trade(pnl)
            strategy.on_fill(fill)
        else:
            log.error("SELL order did not fill — position may still be open!")

    except AngelOneAPIError as e:
        log.error("SELL order failed: %s", e)


def execute_short(
    session:  AngelSession,
    strategy: EmaCrossoverStrategy,
    risk_mgr: RiskManager,
    ltp:      float,
    dry_run:  bool,
) -> Optional[str]:
    """
    Place a market SELL to open a short position (or simulate in DRY_RUN).
    Returns the hard-floor SL orderid (LIVE only), else None.
    """
    log = get_logger("execute_short")
    qty = risk_mgr.position_size(ltp)

    if dry_run:
        log.info("[DRY_RUN] SHORT %s  qty=%d  ltp=₹%.2f", strategy.symbol, qty, ltp)
        strategy.on_fill({
            "status":          "complete",
            "transactiontype": "SELL",
            "averageprice":    str(ltp),
            "filledshares":    str(qty),
            "uniqueorderid":   "DRY-SHORT",
        })
        return None

    try:
        result    = sell(
            session, strategy.symbol, strategy.token,
            quantity     = qty,
            product_type = strategy.product,
            order_tag    = "short_entry",
        )
        unique_id = result.get("uniqueorderid", "")
        log.info("SHORT order placed — uniqueorderid=%s", unique_id)

        fill = wait_for_fill(session, unique_id)
        if not fill:
            log.error("SHORT order did not fill — no position opened")
            return None

        strategy.on_fill(fill)
        entry_price = float(fill.get("averageprice", ltp))
        sl_order_id = _place_sl_short(session, strategy, entry_price, qty)
        return sl_order_id

    except AngelOneAPIError as e:
        log.error("SHORT order failed: %s", e)
        return None


def execute_cover(
    session:     AngelSession,
    strategy:    EmaCrossoverStrategy,
    risk_mgr:    RiskManager,
    ltp:         float,
    dry_run:     bool,
    sl_order_id: Optional[str] = None,
) -> None:
    """Cancel any outstanding SL and place a market BUY to cover the short (or simulate)."""
    log = get_logger("execute_cover")

    # Capture before on_fill resets position state
    entry_price = strategy.entry_price
    entry_qty   = strategy.entry_qty

    if dry_run:
        pnl = (entry_price - ltp) * entry_qty   # profit when price falls
        log.info("[DRY_RUN] COVER %s  qty=%d  ltp=₹%.2f  P&L=₹%.2f",
                 strategy.symbol, entry_qty, ltp, pnl)
        risk_mgr.record_trade(pnl)
        strategy.on_fill({
            "status":          "complete",
            "transactiontype": "BUY",
            "averageprice":    str(ltp),
            "filledshares":    str(entry_qty),
            "uniqueorderid":   "DRY-COVER",
        })
        return

    if sl_order_id:
        _cancel_sl(session, sl_order_id)

    try:
        result    = buy(
            session, strategy.symbol, strategy.token,
            quantity     = entry_qty,
            product_type = strategy.product,
            order_tag    = "short_exit",
        )
        unique_id = result.get("uniqueorderid", "")
        log.info("COVER order placed — uniqueorderid=%s", unique_id)

        fill = wait_for_fill(session, unique_id)
        if fill:
            exit_price = float(fill.get("averageprice", ltp))
            pnl        = (entry_price - exit_price) * entry_qty
            risk_mgr.record_trade(pnl)
            strategy.on_fill(fill)
        else:
            log.error("COVER order did not fill — short position may still be open!")

    except AngelOneAPIError as e:
        log.error("COVER order failed: %s", e)


# ── Strategy loop ─────────────────────────────────────────────────────────────

def run_strategy_loop(
    session:    AngelSession,
    strategy:   EmaCrossoverStrategy,
    risk_mgr:   RiskManager,
    config:     dict,
    stop_event: threading.Event,
) -> None:
    log           = get_logger("strategy_loop")
    dry_run       = config["bot"]["dry_run"]
    loop_interval = int(config["bot"].get("loop_interval_sec", 300))
    strat_cfg     = config["strategy"]
    sl_order_id: Optional[str] = None

    log.info(
        "Strategy loop started — interval=%ds  dry_run=%s",
        loop_interval, dry_run,
    )

    while not stop_event.is_set():
        try:
            # ── Wait for market open ──────────────────────────────────────────
            if not is_market_open():
                mins = minutes_to_market_open()
                if mins:
                    log.info("Market opens in %d min — sleeping", mins)
                    stop_event.wait(timeout=min(mins * 60, 300))
                else:
                    log.info("Market closed — sleeping 5 min")
                    stop_event.wait(timeout=300)
                continue

            # ── Fetch current LTP ────────────────────────────────────────────
            try:
                ltp = get_ltp_single(
                    session,
                    strat_cfg["exchange"],
                    strat_cfg["symbol"],
                    strategy.token,
                )
            except AngelOneAPIError as e:
                log.warning("LTP fetch failed: %s — skipping iteration", e)
                stop_event.wait(timeout=30)
                continue

            # ── Generate signal ──────────────────────────────────────────────
            signal = strategy.generate_signal(session)

            if signal == "BUY":
                can_trade, reason = risk_mgr.check_can_trade()
                if can_trade:
                    sl_order_id = execute_buy(session, strategy, risk_mgr, ltp, dry_run)
                else:
                    log.warning("BUY blocked by risk manager: %s", reason)

            elif signal == "SELL":
                execute_sell(session, strategy, risk_mgr, ltp, dry_run, sl_order_id)
                sl_order_id = None

            elif signal == "SHORT":
                can_trade, reason = risk_mgr.check_can_trade()
                if can_trade:
                    sl_order_id = execute_short(session, strategy, risk_mgr, ltp, dry_run)
                else:
                    log.warning("SHORT blocked by risk manager: %s", reason)

            elif signal == "COVER":
                execute_cover(session, strategy, risk_mgr, ltp, dry_run, sl_order_id)
                sl_order_id = None

            # ── Log position + risk state ─────────────────────────────────────
            pos = strategy.get_state()
            if pos["in_position"]:
                tsl_info = (
                    f"  tsl=₹{pos['tsl_sl']:.2f}{'(active)' if pos['tsl_activated'] else '(pending)'}"
                    if pos["tsl_sl"] > 0 else ""
                )
                log.info(
                    "OPEN %s — %s  qty=%d  entry=₹%.2f  ltp=₹%.2f  unrealised=₹%.2f%s",
                    pos["direction"],
                    strat_cfg["symbol"],
                    pos["entry_qty"], pos["entry_price"],
                    pos["ltp"] or ltp, pos["unrealised_pnl"],
                    tsl_info,
                )

            risk = risk_mgr.status()
            log.info(
                "Risk — day_pnl=₹%.2f  trades=%d/%d  consec_losses=%d  "
                "limit_used=%.1f%%%s",
                risk["daily_pnl"],
                risk["trades_today"], risk_mgr.max_trades_per_day,
                risk["consecutive_losses"],
                risk["loss_limit_used_pct"],
                "  *** HALTED ***" if risk["halted"] else "",
            )

        except AngelOneAPIError as e:
            log.error("API error in strategy loop: %s", e)
        except Exception as e:
            log.exception("Unexpected error in strategy loop: %s", e)

        stop_event.wait(timeout=loop_interval)


# ── Exchange type helper ──────────────────────────────────────────────────────

def _exchange_type(exchange: str) -> int:
    return {
        "NSE": ExchangeType.NSE_CM,
        "BSE": ExchangeType.BSE_CM,
        "NFO": ExchangeType.NSE_FO,
        "MCX": ExchangeType.MCX_FO,
        "CDS": ExchangeType.CDS_FO,
    }.get(exchange.upper(), ExchangeType.NSE_CM)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="AngelOne Trading Bot")
    parser.add_argument("--config", default="config.json",
                        help="Path to config.json (default: config.json)")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config["bot"].get("log_level", "INFO"))
    log = get_logger("main")

    log.info(
        "=== Bot starting — strategy=%s  dry_run=%s ===",
        config["strategy"]["name"],
        config["bot"]["dry_run"],
    )

    session    = AngelSession.from_env()
    strategy   = load_strategy(config)
    stop_event = threading.Event()

    # ── Graceful shutdown on Ctrl+C / SIGTERM ─────────────────────────────────
    def _shutdown(sig, frame):
        log.info("Shutdown signal received — stopping gracefully...")
        stop_event.set()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        # ── Login ─────────────────────────────────────────────────────────────
        session.login()
        log.info("Logged in: %s", session.tokens.client_code)

        # ── Risk manager ──────────────────────────────────────────────────────
        risk_mgr = RiskManager(config)
        risk_mgr.sync_from_portfolio(session)   # seed today's P&L on restart

        # ── Strategy setup ────────────────────────────────────────────────────
        strategy.on_start(session)

        # ── Market feed — live ticks → strategy.on_tick ───────────────────────
        market_feed = MarketFeed(
            session       = session,
            on_tick       = strategy.on_tick,
            on_error      = lambda e: log.error("MarketFeed error: %s", e),
            on_connect    = lambda: log.info("MarketFeed connected"),
            on_disconnect = lambda: log.warning("MarketFeed disconnected"),
        )
        market_feed.subscribe(
            instruments = [(
                config["strategy"]["exchange"],
                _exchange_type(config["strategy"]["exchange"]),
                [strategy.token],
            )],
            mode = 1,   # LTP — sufficient for displaying live price
        )
        market_feed.start()

        # ── Order feed — fills → strategy.on_fill ─────────────────────────────
        # Degrades gracefully if server returns 403 (account restriction).
        order_feed = OrderFeed(
            session         = session,
            on_order_update = strategy.on_fill,
            on_error        = lambda e: log.warning("OrderFeed: %s", e),
        )
        order_feed.start()

        # ── Session refresh thread ────────────────────────────────────────────
        refresh_stop   = threading.Event()
        refresh_thread = threading.Thread(
            target  = _session_refresh_loop,
            args    = (session, refresh_stop),
            daemon  = True,
            name    = "SessionRefresh",
        )
        refresh_thread.start()

        log.info("=== All feeds started — running strategy loop ===")

        # ── Strategy loop — blocks until stop_event is set ───────────────────
        run_strategy_loop(session, strategy, risk_mgr, config, stop_event)

    except KeyboardInterrupt:
        stop_event.set()

    finally:
        log.info("=== Shutting down ===")

        strategy.on_stop()

        try:
            market_feed.stop()
        except Exception:
            pass
        try:
            order_feed.stop()
        except Exception:
            pass

        try:
            refresh_stop.set()
        except Exception:
            pass

        try:
            session.logout()
        except Exception:
            pass

        log.info("=== Bot stopped cleanly ===")


if __name__ == "__main__":
    main()
