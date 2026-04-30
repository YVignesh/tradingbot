"""
bot_runtime.py - Multi-strategy bot runner
==========================================
Shared live runtime for multi-symbol strategies, journaling, recovery,
notifications, and pre-market screening.
"""

from __future__ import annotations

import argparse
import atexit
import copy
import json
import logging
import signal
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from broker.constants import ExchangeType, TransactionType
from broker.market_data import get_ltp_single, is_market_open, minutes_to_market_open
from broker.orders import buy, cancel_order, get_order_status, place_stop_loss_market, sell
from broker.portfolio import get_open_positions
from broker.session import AngelSession
from broker.websocket_feed import MarketFeed, OrderFeed
from journal import TradeJournal
from notifications import TelegramNotifier
from notifications.telegram import TelegramCommandHandler
from risk.manager import RiskManager
from screener import ScreenerScheduler
from ai.orchestrator import AIOrchestrator
from strategies.directional import DirectionalStrategy
from strategies.registry import STRATEGIES
from utils import AngelOneAPIError, get_logger
from utils.market_regime import MarketRegimeFilter

IST = timezone(timedelta(hours=5, minutes=30))

ENTRY_LONG = "ENTRY_LONG"
EXIT_LONG = "EXIT_LONG"
ENTRY_SHORT = "ENTRY_SHORT"
EXIT_SHORT = "EXIT_SHORT"
STOP_LONG = "STOP_LONG"
STOP_SHORT = "STOP_SHORT"

ENTRY_INTENTS = {ENTRY_LONG, ENTRY_SHORT}
EXIT_INTENTS = {EXIT_LONG, EXIT_SHORT, STOP_LONG, STOP_SHORT}
LONG_EXIT_INTENTS = {EXIT_LONG, STOP_LONG}
SHORT_EXIT_INTENTS = {EXIT_SHORT, STOP_SHORT}
TERMINAL_STATUSES = {"complete", "rejected", "cancelled"}
PARTIAL_STATUSES = {"partial", "open", "open pending", "trigger pending"}


def setup_logging(log_level: str = "INFO") -> None:
    today = datetime.now(IST).strftime("%Y-%m-%d")
    log_dir = Path("logs") / today
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bot.log"

    fmt = logging.Formatter(
        "%(asctime)s  [%(levelname)-8s]  %(name)s  - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    root.addHandler(fh)

    logging.getLogger(__name__).info("Logging to %s", log_file)


def load_config(path: str = "config.json") -> dict:
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)

    for section in ("bot", "risk", "broker"):
        if section not in config:
            raise KeyError(f"Missing required section '{section}' in {path}")
    if "strategy" not in config and "strategies" not in config:
        raise KeyError(f"Missing required section 'strategy' or 'strategies' in {path}")

    # Validate critical risk parameters (#19)
    risk = config["risk"]
    required_risk_keys = ("capital", "max_risk_pct", "sl_points", "max_qty", "daily_loss_limit")
    for key in required_risk_keys:
        if key not in risk:
            raise KeyError(f"Missing required risk parameter '{key}' in {path}")
        val = float(risk[key])
        if val <= 0:
            raise ValueError(f"risk.{key} must be positive, got {val}")

    if float(risk["max_risk_pct"]) > 5.0:
        raise ValueError(f"risk.max_risk_pct={risk['max_risk_pct']} exceeds safety limit of 5%")

    if float(risk["daily_loss_limit"]) > float(risk["capital"]) * 0.2:
        log = get_logger("config")
        log.warning(
            "daily_loss_limit (₹%.0f) is >20%% of capital (₹%.0f) — review if intentional",
            float(risk["daily_loss_limit"]), float(risk["capital"]),
        )

    # Additional validation warnings
    _cfg_log = get_logger("config")
    sl_pts = float(risk.get("sl_points", 0))
    sl_atr = float(risk.get("sl_atr_multiplier", 0))
    if sl_pts <= 0 and sl_atr <= 0:
        _cfg_log.warning("Neither sl_points nor sl_atr_multiplier is set — SL will be zero")

    loop_interval = config.get("bot", {}).get("loop_interval_sec", 5)
    if float(loop_interval) <= 0:
        raise ValueError(f"bot.loop_interval_sec must be > 0, got {loop_interval}")

    return config


def _base_strategy_template(config: dict) -> dict:
    if "strategy" in config and isinstance(config["strategy"], dict):
        return copy.deepcopy(config["strategy"])
    strategies = config.get("strategies", [])
    if strategies:
        return copy.deepcopy(strategies[0])
    raise KeyError("No strategy template found in config")


def build_strategy_configs(
    config: dict,
    session: Optional[AngelSession] = None,
    force_screener: bool = False,
) -> list[dict]:
    template = _base_strategy_template(config)

    if config.get("strategies"):
        raw_strategies = list(config["strategies"])
    elif session is not None and config.get("screener", {}).get("enabled", False):
        selected = ScreenerScheduler(config).resolve_symbols(session, force=force_screener)
        if not selected:
            get_logger("build_strategy_configs").warning(
                "Screener returned 0 symbols — bot will idle until next screener run"
            )
            return []
        raw_strategies = [
            {"symbol": item["symbol"], "exchange": item["exchange"]}
            for item in selected
        ]
    else:
        raw_strategies = [template]

    merged_configs = []
    for raw in raw_strategies:
        merged = copy.deepcopy(config)
        merged_strategy = copy.deepcopy(template)
        merged_strategy.update(raw)
        merged["strategy"] = merged_strategy
        merged_configs.append(merged)
    return merged_configs


def load_strategy(config: dict) -> DirectionalStrategy:
    name = str(config["strategy"]["name"]).strip()
    cls = STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy {name!r}. Available: {sorted(STRATEGIES)}")
    strategy = cls(config)
    if not isinstance(strategy, DirectionalStrategy):
        raise TypeError(f"Strategy {name!r} must inherit from DirectionalStrategy")
    return strategy


def _session_refresh_loop(session: AngelSession, stop_event: threading.Event) -> None:
    log = get_logger("session_refresh")
    while not stop_event.wait(timeout=1800):
        try:
            if session.refresh_if_needed(warn_minutes=60):
                log.info("Session token refreshed proactively")
        except Exception as exc:
            log.error("Token refresh failed: %s", exc)


def _normalize_status(raw_status: str, filled_qty: int) -> str:
    text = " ".join(str(raw_status or "").strip().lower().replace("_", " ").split())
    if text in TERMINAL_STATUSES:
        return text
    if "partial" in text:
        return "partial"
    if "reject" in text:
        return "rejected"
    if "cancel" in text:
        return "cancelled"
    if "complete" in text:
        return "complete"
    if "trigger" in text:
        return "trigger pending"
    if "open" in text and filled_qty > 0:
        return "partial"
    if "open" in text:
        return "open"
    return text or "unknown"


def _safe_int(value: object) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _status_message(update: dict) -> str:
    for key in ("text", "message", "reason", "remarks"):
        text = str(update.get(key, "")).strip()
        if text:
            return text
    return "no reason given"


def _extract_fill_time(update: dict) -> str:
    for key in ("filled_at", "exchorderupdatetime", "updatetime", "orderdatetime"):
        text = str(update.get(key, "")).strip()
        if text:
            return text
    return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")


def _infer_position_entry_price(position: dict, net_qty: int) -> float:
    for key in ("netprice", "avgnetprice", "averageprice", "price"):
        value = abs(_safe_float(position.get(key, 0.0)))
        if value > 0:
            return value

    buy_qty = abs(_safe_float(position.get("buyqty", 0.0)))
    sell_qty = abs(_safe_float(position.get("sellqty", 0.0)))
    buy_amount = abs(_safe_float(position.get("buyamount", 0.0)))
    sell_amount = abs(_safe_float(position.get("sellamount", 0.0)))

    if net_qty > 0 and buy_qty > 0:
        return buy_amount / buy_qty
    if net_qty < 0 and sell_qty > 0:
        return sell_amount / sell_qty
    return 0.0


@dataclass
class ExecutionProtectionConfig:
    entry_order_timeout_sec: int = 45
    exit_order_timeout_sec: int = 30
    status_poll_interval_sec: int = 5
    max_place_retries: int = 2
    retry_backoff_sec: int = 2
    max_consecutive_api_failures: int = 5
    broker_circuit_cooldown_sec: int = 300

    @classmethod
    def from_config(cls, config: dict) -> "ExecutionProtectionConfig":
        exec_cfg = config.get("bot", {}).get("execution", {})
        return cls(
            entry_order_timeout_sec=int(exec_cfg.get("entry_order_timeout_sec", 45)),
            exit_order_timeout_sec=int(exec_cfg.get("exit_order_timeout_sec", 30)),
            status_poll_interval_sec=int(exec_cfg.get("status_poll_interval_sec", 5)),
            max_place_retries=int(exec_cfg.get("max_place_retries", 2)),
            retry_backoff_sec=int(exec_cfg.get("retry_backoff_sec", 2)),
            max_consecutive_api_failures=int(exec_cfg.get("max_consecutive_api_failures", 5)),
            broker_circuit_cooldown_sec=int(exec_cfg.get("broker_circuit_cooldown_sec", 300)),
        )


@dataclass
class TrackedOrder:
    unique_order_id: str
    order_id: str
    intent: str
    symbol: str
    expected_qty: int
    expected_price: float
    created_at: float
    stale_timeout_sec: Optional[int]
    status: str = "new"
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    last_update_at: float = 0.0
    last_polled_at: float = 0.0


@dataclass
class StrategyRuntime:
    config: dict
    strategy: DirectionalStrategy
    execution: "ExecutionManager"
    sl_order_id: Optional[str] = None
    last_circuit_reason: str = ""


class ExecutionManager:
    def __init__(
        self,
        strategy: DirectionalStrategy,
        risk_mgr: RiskManager,
        config: ExecutionProtectionConfig,
        journal: Optional[TradeJournal] = None,
        notifier: Optional[TelegramNotifier] = None,
    ) -> None:
        self.strategy = strategy
        self.risk_mgr = risk_mgr
        self.config = config
        self.journal = journal
        self.notifier = notifier
        self.log = get_logger(f"execution.{strategy.symbol}.{strategy.strategy_name}")
        self._active_orders: dict[str, TrackedOrder] = {}
        self._last_terminal_status: dict[str, str] = {}
        self._terminal_status_max = 200  # LRU cap — evict oldest when exceeded
        self._lock = threading.RLock()
        self._consecutive_api_failures = 0
        self._circuit_open_until = 0.0
        self._circuit_reason = ""

    def register_order(
        self,
        result: dict,
        intent: str,
        symbol: str,
        expected_qty: int,
        expected_price: float,
        stale_timeout_sec: Optional[int],
    ) -> str:
        unique_id = str(result.get("uniqueorderid", "")).strip()
        order_id = str(result.get("orderid", "")).strip()
        if not unique_id:
            raise AngelOneAPIError("Order placement returned no uniqueorderid")
        state = TrackedOrder(
            unique_order_id=unique_id,
            order_id=order_id,
            intent=intent,
            symbol=symbol,
            expected_qty=expected_qty,
            expected_price=expected_price,
            created_at=time.monotonic(),
            stale_timeout_sec=stale_timeout_sec,
        )
        with self._lock:
            self._active_orders[unique_id] = state
        self.log.info(
            "Tracking %s order %s qty=%d expected=%.2f",
            intent, unique_id, expected_qty, expected_price,
        )
        return unique_id

    def is_circuit_open(self) -> bool:
        return time.monotonic() < self._circuit_open_until

    def circuit_reason(self) -> str:
        return self._circuit_reason

    def can_submit(self, intent: str) -> tuple[bool, str]:
        if self.is_circuit_open() and intent in ENTRY_INTENTS:
            return False, self._circuit_reason or "broker circuit breaker is open"
        return True, ""

    def process_order_update(self, update: dict, source: str) -> bool:
        unique_id = str(update.get("uniqueorderid", "")).strip()
        if not unique_id:
            return False

        with self._lock:
            state = self._active_orders.get(unique_id)

        if state is None:
            return False

        filled_qty = _safe_int(update.get("filledshares", 0))
        avg_price = _safe_float(update.get("averageprice", 0.0), state.expected_price)
        status = _normalize_status(update.get("status", ""), filled_qty)

        if status == state.status and filled_qty <= state.filled_qty:
            return status in TERMINAL_STATUSES

        state.last_update_at = time.monotonic()
        state.order_id = str(update.get("orderid", state.order_id)).strip() or state.order_id

        delta_qty = 0
        delta_price = avg_price if avg_price > 0 else state.expected_price
        if filled_qty > state.filled_qty:
            delta_qty = filled_qty - state.filled_qty
            delta_price = self._delta_fill_price(state, filled_qty, avg_price)
            state.filled_qty = filled_qty
            state.avg_fill_price = avg_price
            self._apply_fill_delta(state, update, delta_qty, delta_price, source)
            self._log_slippage(state, delta_qty, delta_price)

        state.status = status

        if status == "rejected":
            self.log.error(
                "%s rejected: %s (%s)",
                state.intent, unique_id, self._classify_rejection(update),
            )
        elif status == "cancelled":
            self.log.warning("%s cancelled: %s", state.intent, unique_id)

        if status in TERMINAL_STATUSES:
            with self._lock:
                self._active_orders.pop(unique_id, None)
                self._last_terminal_status[unique_id] = status
                # LRU eviction: trim oldest entries when cap exceeded
                if len(self._last_terminal_status) > self._terminal_status_max:
                    excess = len(self._last_terminal_status) - self._terminal_status_max
                    for key in list(self._last_terminal_status)[:excess]:
                        del self._last_terminal_status[key]
        elif status in PARTIAL_STATUSES and delta_qty > 0:
            self.log.warning(
                "%s partially filled: %s %d/%d @ %.2f",
                state.intent, unique_id, state.filled_qty, state.expected_qty, delta_price,
            )

        return status in TERMINAL_STATUSES

    def wait_for_terminal(
        self,
        session: AngelSession,
        unique_order_id: str,
        timeout_sec: int,
    ) -> Optional[str]:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            with self._lock:
                state = self._active_orders.get(unique_order_id)
                terminal_status = self._last_terminal_status.get(unique_order_id)
            if state is None:
                return terminal_status
            try:
                update = self.call_with_retry(
                    "order_status",
                    lambda: get_order_status(session, unique_order_id),
                    max_retries=1,
                )
                if update:
                    terminal = self.process_order_update(update, source="poll")
                    if terminal:
                        return _normalize_status(update.get("status", ""), _safe_int(update.get("filledshares", 0)))
            except AngelOneAPIError as exc:
                self.log.warning("Status poll error for %s: %s", unique_order_id, exc)
            time.sleep(self.config.status_poll_interval_sec)
        with self._lock:
            state = self._active_orders.get(unique_order_id)
        if state is not None:
            self._handle_stale_order(session, state)
            with self._lock:
                return self._last_terminal_status.get(unique_order_id, "cancelled")
        return None

    def monitor_orders(self, session: AngelSession) -> None:
        now = time.monotonic()
        with self._lock:
            states = list(self._active_orders.values())

        for state in states:
            if now - state.last_polled_at >= self.config.status_poll_interval_sec:
                state.last_polled_at = now
                try:
                    update = self.call_with_retry(
                        "order_status",
                        lambda oid=state.unique_order_id: get_order_status(session, oid),
                        max_retries=1,
                    )
                    if update:
                        self.process_order_update(update, source="monitor")
                except AngelOneAPIError as exc:
                    self.log.warning("Background status poll failed for %s: %s", state.unique_order_id, exc)

            if (
                state.stale_timeout_sec is not None and
                now - state.created_at >= state.stale_timeout_sec and
                state.status not in TERMINAL_STATUSES
            ):
                self._handle_stale_order(session, state)

    def call_with_retry(self, context: str, fn, max_retries: Optional[int] = None):
        retries = self.config.max_place_retries if max_retries is None else max_retries
        attempt = 0
        while True:
            try:
                result = fn()
                self._record_api_success()
                return result
            except AngelOneAPIError as exc:
                retryable = self._is_retryable_error(exc)
                self._record_api_failure(context, exc)
                if attempt >= retries or not retryable:
                    raise
                sleep_for = self.config.retry_backoff_sec * (attempt + 1)
                self.log.warning(
                    "%s failed (%s) - retrying in %ds (%d/%d)",
                    context, exc, sleep_for, attempt + 1, retries,
                )
                time.sleep(sleep_for)
                attempt += 1

    def _delta_fill_price(self, state: TrackedOrder, filled_qty: int, avg_price: float) -> float:
        delta_qty = filled_qty - state.filled_qty
        if delta_qty <= 0:
            return avg_price if avg_price > 0 else state.expected_price
        prev_value = state.avg_fill_price * state.filled_qty
        total_value = avg_price * filled_qty
        delta_value = total_value - prev_value
        if delta_value <= 0:
            return avg_price if avg_price > 0 else state.expected_price
        return delta_value / delta_qty

    def _apply_fill_delta(
        self,
        state: TrackedOrder,
        raw_update: dict,
        delta_qty: int,
        fill_price: float,
        source: str,
    ) -> None:
        txn = str(raw_update.get("transactiontype", "")).upper()
        if delta_qty <= 0 or txn not in {"BUY", "SELL"}:
            return

        if state.intent in LONG_EXIT_INTENTS and self.strategy.direction != "LONG":
            self.log.warning("Ignoring %s delta for %s because strategy is %s", state.intent, state.unique_order_id, self.strategy.direction)
            return
        if state.intent in SHORT_EXIT_INTENTS and self.strategy.direction != "SHORT":
            self.log.warning("Ignoring %s delta for %s because strategy is %s", state.intent, state.unique_order_id, self.strategy.direction)
            return

        if state.intent in LONG_EXIT_INTENTS:
            remaining_before = self.strategy.entry_qty
            closed_qty = min(delta_qty, remaining_before)
            realised = (fill_price - self.strategy.entry_price) * closed_qty
            close_round_trip = delta_qty >= remaining_before
            self.risk_mgr.record_realized_pnl(realised, close_round_trip=close_round_trip)
        elif state.intent in SHORT_EXIT_INTENTS:
            remaining_before = self.strategy.entry_qty
            closed_qty = min(delta_qty, remaining_before)
            realised = (self.strategy.entry_price - fill_price) * closed_qty
            close_round_trip = delta_qty >= remaining_before
            self.risk_mgr.record_realized_pnl(realised, close_round_trip=close_round_trip)

        before_direction = self.strategy.direction
        update = dict(raw_update)
        update["status"] = "complete"
        update["filledshares"] = str(delta_qty)
        update["averageprice"] = str(fill_price)
        update["filled_at"] = _extract_fill_time(raw_update)
        self.strategy.on_fill(update)
        after_direction = self.strategy.direction

        fill_record = {
            "recorded_at": datetime.now(IST),
            "strategy": self.strategy.strategy_name,
            "symbol": self.strategy.symbol,
            "exchange": self.strategy.exchange,
            "intent": state.intent,
            "transaction_type": txn,
            "direction_before": before_direction,
            "direction_after": after_direction,
            "order_id": state.unique_order_id,
            "fill_qty": delta_qty,
            "fill_price": round(fill_price, 2),
            "status": "complete",
            "source": source,
        }
        if self.journal is not None:
            self.journal.record_fill(fill_record)
        if self.notifier is not None:
            self.notifier.notify_fill(fill_record)

        for trade in self.strategy.pop_completed_trades():
            if self.journal is not None:
                stored_trade = self.journal.record_trade(
                    trade,
                    product=self.strategy.product,
                    charge_segment=self.strategy.charge_segment,
                )
            else:
                stored_trade = dict(trade)
                stored_trade["net_pnl"] = stored_trade.get("gross_pnl", 0.0)
            if self.notifier is not None:
                self.notifier.notify_trade(stored_trade)

    def _log_slippage(self, state: TrackedOrder, delta_qty: int, fill_price: float) -> None:
        if delta_qty <= 0 or state.expected_price <= 0:
            return
        if state.intent in {ENTRY_LONG, EXIT_SHORT, STOP_SHORT}:
            adverse = fill_price - state.expected_price
        else:
            adverse = state.expected_price - fill_price
        self.log.info(
            "Slippage %s: order=%s expected=%.2f fill=%.2f delta=%.2f qty=%d",
            state.intent, state.unique_order_id, state.expected_price, fill_price, adverse, delta_qty,
        )

    def _handle_stale_order(self, session: AngelSession, state: TrackedOrder) -> None:
        if state.intent in {STOP_LONG, STOP_SHORT}:
            return
        self.log.warning(
            "Order %s (%s) is stale after %ds",
            state.unique_order_id, state.intent, state.stale_timeout_sec,
        )
        if state.order_id:
            try:
                self.call_with_retry(
                    "cancel_order",
                    lambda oid=state.order_id: cancel_order(session, oid),
                    max_retries=1,
                )
            except AngelOneAPIError as exc:
                self.log.error("Could not cancel stale order %s: %s", state.unique_order_id, exc)
                return
        with self._lock:
            self._active_orders.pop(state.unique_order_id, None)
            self._last_terminal_status[state.unique_order_id] = "cancelled"
        if state.intent in EXIT_INTENTS and state.filled_qty < state.expected_qty:
            self._open_circuit(f"stale exit order left residual position ({state.unique_order_id})")

    # AngelOne error codes that are definitively non-retryable
    _NON_RETRYABLE_CODES = frozenset({
        "AG8001",   # Invalid Token
        "AG8003",   # Token missing
        "AB1006",   # Blocked
        "AB1008",   # Invalid Variety
        "AB1009",   # Symbol Not Found
        "AB1012",   # Invalid Product
        "AB2002",   # ROBO blocked
        "AB4008",   # ordertag length exceeded
    })

    # Codes that indicate auth needs refresh (not retryable via simple retry)
    _AUTH_REFRESH_CODES = frozenset({
        "AG8002",   # Token Expired
        "AB8050",   # Invalid Refresh Token
        "AB8051",   # Refresh Token Expired
        "AB1010",   # AMX Session Expired
        "AB1011",   # Client not login
    })

    # Codes that are explicitly retryable
    _RETRYABLE_CODES = frozenset({
        "AB1004",   # Something Went Wrong Try Later
        "AB2001",   # Internal Error
    })

    def _is_retryable_error(self, exc: AngelOneAPIError) -> bool:
        text = str(exc)
        # Check AngelOne error codes first (more reliable than text matching)
        for code in self._NON_RETRYABLE_CODES:
            if code in text:
                return False
        for code in self._AUTH_REFRESH_CODES:
            if code in text:
                return False
        for code in self._RETRYABLE_CODES:
            if code in text:
                return True
        # Fallback: text-based classification for network/HTTP errors
        text_lower = text.lower()
        retryable_markers = (
            "network error", "timed out", "timeout", "connection",
            "http 429", "http 500", "http 502", "http 503", "http 504",
        )
        return any(marker in text_lower for marker in retryable_markers)

    def _classify_rejection(self, update: dict) -> str:
        text = _status_message(update).lower()
        if "margin" in text or "fund" in text:
            return "insufficient_margin"
        if "token" in text or "session" in text or "auth" in text:
            return "session_or_auth"
        if "price" in text or "trigger" in text:
            return "invalid_price"
        if "freeze" in text or "quantity" in text:
            return "invalid_quantity"
        return "broker_rejection"

    def _record_api_success(self) -> None:
        self._consecutive_api_failures = 0
        if not self.is_circuit_open():
            self._circuit_reason = ""

    def _record_api_failure(self, context: str, exc: AngelOneAPIError) -> None:
        self._consecutive_api_failures += 1
        if self._consecutive_api_failures >= self.config.max_consecutive_api_failures:
            self._open_circuit(f"{context} failed repeatedly: {exc}")

    def _open_circuit(self, reason: str) -> None:
        self._circuit_reason = reason
        self._circuit_open_until = time.monotonic() + self.config.broker_circuit_cooldown_sec
        self.log.error("Broker circuit breaker opened: %s", reason)


def _place_sl(
    session: AngelSession,
    strategy: DirectionalStrategy,
    execution: ExecutionManager,
    entry_price: float,
    qty: int,
) -> Optional[str]:
    log = get_logger(f"sl_manager.{strategy.symbol}")
    sl_distance = strategy.effective_sl_points()
    sl_trigger = round(entry_price - sl_distance, 2)
    try:
        result = execution.call_with_retry(
            "place_stop_loss_long",
            lambda: place_stop_loss_market(
                session,
                strategy.symbol,
                strategy.token,
                quantity=qty,
                trigger_price=sl_trigger,
                transaction_type=TransactionType.SELL,
                product_type=strategy.product,
                order_tag="sl_long",
            ),
        )
        execution.register_order(
            result=result,
            intent=STOP_LONG,
            symbol=strategy.symbol,
            expected_qty=qty,
            expected_price=sl_trigger,
            stale_timeout_sec=None,
        )
        order_id = result.get("orderid", "")
        log.info("Long SL placed trigger=%.2f orderid=%s", sl_trigger, order_id)
        return order_id
    except AngelOneAPIError as exc:
        log.error("SL order failed (no stop-loss active) — flattening position: %s", exc)
        return None


def _place_sl_short(
    session: AngelSession,
    strategy: DirectionalStrategy,
    execution: ExecutionManager,
    entry_price: float,
    qty: int,
) -> Optional[str]:
    log = get_logger(f"sl_manager.{strategy.symbol}")
    sl_distance = strategy.effective_sl_points()
    sl_trigger = round(entry_price + sl_distance, 2)
    try:
        result = execution.call_with_retry(
            "place_stop_loss_short",
            lambda: place_stop_loss_market(
                session,
                strategy.symbol,
                strategy.token,
                quantity=qty,
                trigger_price=sl_trigger,
                transaction_type=TransactionType.BUY,
                product_type=strategy.product,
                order_tag="sl_short",
            ),
        )
        execution.register_order(
            result=result,
            intent=STOP_SHORT,
            symbol=strategy.symbol,
            expected_qty=qty,
            expected_price=sl_trigger,
            stale_timeout_sec=None,
        )
        order_id = result.get("orderid", "")
        log.info("Short SL placed trigger=%.2f orderid=%s", sl_trigger, order_id)
        return order_id
    except AngelOneAPIError as exc:
        log.error("Short SL order failed (no stop-loss active) — flattening position: %s", exc)
        return None


def _cancel_sl(session: AngelSession, execution: ExecutionManager, sl_order_id: str) -> None:
    log = get_logger("sl_manager")
    try:
        execution.call_with_retry(
            "cancel_stop_loss",
            lambda: cancel_order(session, sl_order_id),
            max_retries=1,
        )
        log.info("SL order %s cancelled", sl_order_id)
    except AngelOneAPIError as exc:
        log.warning("Could not cancel SL order %s: %s", sl_order_id, exc)


def execute_buy(
    session: AngelSession,
    strategy: DirectionalStrategy,
    risk_mgr: RiskManager,
    execution: ExecutionManager,
    ltp: float,
    dry_run: bool,
) -> Optional[str]:
    log = get_logger(f"execute_buy.{strategy.symbol}")
    qty = risk_mgr.position_size(ltp, sl_override=strategy.effective_sl_points())
    allowed, reason = execution.can_submit(ENTRY_LONG)
    if not allowed:
        log.warning("BUY blocked by execution guard: %s", reason)
        return None

    if dry_run:
        strategy.on_fill({
            "status": "complete",
            "transactiontype": "BUY",
            "averageprice": str(ltp),
            "filledshares": str(qty),
            "uniqueorderid": "DRY-BUY",
        })
        return None

    try:
        result = execution.call_with_retry(
            "place_buy",
            lambda: buy(
                session,
                strategy.symbol,
                strategy.token,
                quantity=qty,
                product_type=strategy.product,
                order_tag="entry_long",
            ),
        )
        unique_id = execution.register_order(
            result=result,
            intent=ENTRY_LONG,
            symbol=strategy.symbol,
            expected_qty=qty,
            expected_price=ltp,
            stale_timeout_sec=execution.config.entry_order_timeout_sec,
        )
        terminal = execution.wait_for_terminal(session, unique_id, execution.config.entry_order_timeout_sec)
        if terminal != "complete":
            log.error("BUY order did not complete (status=%s)", terminal or "timeout")
            return None
        sl_id = _place_sl(session, strategy, execution, strategy.entry_price, strategy.entry_qty)
        if sl_id is None and strategy.in_position:
            log.error("SL placement failed after BUY — immediately flattening position")
            execute_sell(session, strategy, execution, ltp, dry_run=False, sl_order_id=None)
        return sl_id
    except AngelOneAPIError as exc:
        log.error("BUY order failed: %s", exc)
        return None


def execute_sell(
    session: AngelSession,
    strategy: DirectionalStrategy,
    execution: ExecutionManager,
    ltp: float,
    dry_run: bool,
    sl_order_id: Optional[str] = None,
) -> None:
    log = get_logger(f"execute_sell.{strategy.symbol}")
    entry_qty = strategy.entry_qty
    if entry_qty <= 0:
        return

    if dry_run:
        strategy.on_fill({
            "status": "complete",
            "transactiontype": "SELL",
            "averageprice": str(ltp),
            "filledshares": str(entry_qty),
            "uniqueorderid": "DRY-SELL",
        })
        return

    # Place exit FIRST, cancel SL only AFTER exit is confirmed (#1 SL reliability)
    try:
        result = execution.call_with_retry(
            "place_sell_exit",
            lambda: sell(
                session,
                strategy.symbol,
                strategy.token,
                quantity=entry_qty,
                product_type=strategy.product,
                order_tag="exit_long",
            ),
        )
        unique_id = execution.register_order(
            result=result,
            intent=EXIT_LONG,
            symbol=strategy.symbol,
            expected_qty=entry_qty,
            expected_price=ltp,
            stale_timeout_sec=execution.config.exit_order_timeout_sec,
        )
        terminal = execution.wait_for_terminal(session, unique_id, execution.config.exit_order_timeout_sec)
        if terminal != "complete":
            log.error("SELL order did not complete (status=%s) — keeping SL active", terminal or "timeout")
            return
        # Exit confirmed → now safe to cancel SL
        if sl_order_id:
            _cancel_sl(session, execution, sl_order_id)
    except AngelOneAPIError as exc:
        log.error("SELL order failed — keeping SL active: %s", exc)


def execute_short(
    session: AngelSession,
    strategy: DirectionalStrategy,
    risk_mgr: RiskManager,
    execution: ExecutionManager,
    ltp: float,
    dry_run: bool,
) -> Optional[str]:
    log = get_logger(f"execute_short.{strategy.symbol}")
    qty = risk_mgr.position_size(ltp, sl_override=strategy.effective_sl_points())
    allowed, reason = execution.can_submit(ENTRY_SHORT)
    if not allowed:
        log.warning("SHORT blocked by execution guard: %s", reason)
        return None

    if dry_run:
        strategy.on_fill({
            "status": "complete",
            "transactiontype": "SELL",
            "averageprice": str(ltp),
            "filledshares": str(qty),
            "uniqueorderid": "DRY-SHORT",
        })
        return None

    try:
        result = execution.call_with_retry(
            "place_short_entry",
            lambda: sell(
                session,
                strategy.symbol,
                strategy.token,
                quantity=qty,
                product_type=strategy.product,
                order_tag="entry_short",
            ),
        )
        unique_id = execution.register_order(
            result=result,
            intent=ENTRY_SHORT,
            symbol=strategy.symbol,
            expected_qty=qty,
            expected_price=ltp,
            stale_timeout_sec=execution.config.entry_order_timeout_sec,
        )
        terminal = execution.wait_for_terminal(session, unique_id, execution.config.entry_order_timeout_sec)
        if terminal != "complete":
            log.error("SHORT order did not complete (status=%s)", terminal or "timeout")
            return None
        sl_id = _place_sl_short(session, strategy, execution, strategy.entry_price, strategy.entry_qty)
        if sl_id is None and strategy.in_position:
            log.error("SL placement failed after SHORT — immediately flattening position")
            execute_cover(session, strategy, execution, ltp, dry_run=False, sl_order_id=None)
        return sl_id
    except AngelOneAPIError as exc:
        log.error("SHORT order failed: %s", exc)
        return None


def execute_cover(
    session: AngelSession,
    strategy: DirectionalStrategy,
    execution: ExecutionManager,
    ltp: float,
    dry_run: bool,
    sl_order_id: Optional[str] = None,
) -> None:
    log = get_logger(f"execute_cover.{strategy.symbol}")
    entry_qty = strategy.entry_qty
    if entry_qty <= 0:
        return

    if dry_run:
        strategy.on_fill({
            "status": "complete",
            "transactiontype": "BUY",
            "averageprice": str(ltp),
            "filledshares": str(entry_qty),
            "uniqueorderid": "DRY-COVER",
        })
        return

    # Place cover FIRST, cancel SL only AFTER cover is confirmed (#1 SL reliability)
    try:
        result = execution.call_with_retry(
            "place_cover_exit",
            lambda: buy(
                session,
                strategy.symbol,
                strategy.token,
                quantity=entry_qty,
                product_type=strategy.product,
                order_tag="exit_short",
            ),
        )
        unique_id = execution.register_order(
            result=result,
            intent=EXIT_SHORT,
            symbol=strategy.symbol,
            expected_qty=entry_qty,
            expected_price=ltp,
            stale_timeout_sec=execution.config.exit_order_timeout_sec,
        )
        terminal = execution.wait_for_terminal(session, unique_id, execution.config.exit_order_timeout_sec)
        if terminal != "complete":
            log.error("COVER order did not complete (status=%s) — keeping SL active", terminal or "timeout")
            return
        # Cover confirmed → now safe to cancel SL
        if sl_order_id:
            _cancel_sl(session, execution, sl_order_id)
    except AngelOneAPIError as exc:
        log.error("COVER order failed — keeping SL active: %s", exc)


def _squareoff_with_retry(
    session: AngelSession,
    runtime: StrategyRuntime,
    ltp: float,
    dry_run: bool,
    max_attempts: int = 3,
) -> None:
    """EOD squareoff with retry escalation (#4).
    Retries exit order up to max_attempts with increasing timeouts."""
    log = get_logger(f"squareoff.{runtime.strategy.symbol}")
    strategy = runtime.strategy

    for attempt in range(max_attempts):
        if not strategy.in_position:
            return

        if attempt > 0:
            log.warning("Squareoff retry %d/%d for %s", attempt + 1, max_attempts, strategy.symbol)
            time.sleep(2 * attempt)  # Backoff between retries

        if strategy.direction == "LONG":
            execute_sell(session, strategy, runtime.execution, ltp, dry_run, runtime.sl_order_id)
        elif strategy.direction == "SHORT":
            execute_cover(session, strategy, runtime.execution, ltp, dry_run, runtime.sl_order_id)

        if not strategy.in_position:
            runtime.sl_order_id = None
            return

    if strategy.in_position:
        log.critical(
            "SQUAREOFF FAILED after %d attempts for %s %s qty=%d — MANUAL INTERVENTION REQUIRED",
            max_attempts, strategy.direction, strategy.symbol, strategy.entry_qty,
        )


def recover_positions(
    session: AngelSession,
    runtimes: list[StrategyRuntime],
    notifier: Optional[TelegramNotifier] = None,
) -> None:
    log = get_logger("recovery")
    try:
        positions = get_open_positions(session)
    except Exception as exc:
        log.warning("Position recovery skipped: %s", exc)
        return

    by_key = {
        (runtime.strategy.exchange.upper(), runtime.strategy.symbol.upper()): runtime
        for runtime in runtimes
    }

    for position in positions:
        exchange = str(position.get("exchange", "")).upper()
        symbol = str(position.get("tradingsymbol", "")).upper()
        runtime = by_key.get((exchange, symbol))
        if runtime is None or runtime.strategy.in_position:
            continue

        net_qty = _safe_int(position.get("netqty", 0))
        if net_qty == 0:
            continue
        entry_price = _infer_position_entry_price(position, net_qty)
        if entry_price <= 0:
            log.warning("Could not infer entry price for recovered %s:%s", exchange, symbol)
            continue

        direction = "LONG" if net_qty > 0 else "SHORT"
        runtime.strategy.recover_position(
            direction=direction,
            qty=abs(net_qty),
            entry_price=entry_price,
            order_id="RECOVERED",
        )
        if notifier is not None:
            notifier.notify_halt(
                f"Recovered {direction} position for {symbol} x{abs(net_qty)} @ Rs{entry_price:.2f}"
            )


def run_strategy_loop(
    session: AngelSession,
    runtimes: list[StrategyRuntime],
    risk_mgr: RiskManager,
    config: dict,
    stop_event: threading.Event,
    notifier: Optional[TelegramNotifier] = None,
    reselect_fn: Optional[Callable[[], list[StrategyRuntime]]] = None,
    orchestrator: Optional[AIOrchestrator] = None,
    cmd_handler: Optional[TelegramCommandHandler] = None,
) -> None:
    log = get_logger("strategy_loop")
    dry_run = bool(config["bot"]["dry_run"])
    loop_interval = int(config["bot"].get("loop_interval_sec", 15))
    screener_cfg = config.get("screener", {})
    screener_enabled = bool(screener_cfg.get("enabled", False)) and reselect_fn is not None
    win_h, win_m = map(int, str(screener_cfg.get("run_window_start", "09:00")).split(":"))
    last_halt_reason = ""
    current_day: Optional[date] = None
    screener_done_today = False
    mid_day_done_today = False

    # AI mid-day window time
    ai_cfg = config.get("ai", {})
    _mid_day_h, _mid_day_m = 12, 30
    mid_time_str = str(ai_cfg.get("mid_day_time", "12:30"))
    if ":" in mid_time_str:
        _mid_day_h, _mid_day_m = int(mid_time_str.split(":")[0]), int(mid_time_str.split(":")[1])

    # Market regime filter — gates entries during choppy conditions
    regime_cfg = config.get("regime_filter", {})
    regime_filter = MarketRegimeFilter(regime_cfg)
    regime_update_interval = int(regime_cfg.get("update_interval_sec", 300))
    _last_regime_update = 0.0

    log.info(
        "Strategy loop started interval=%ds dry_run=%s strategies=%d screener=%s regime=%s",
        loop_interval, dry_run, len(runtimes), screener_enabled,
        "on" if regime_filter.enabled else "off",
    )

    while not stop_event.is_set():
        try:
            for runtime in runtimes:
                runtime.execution.monitor_orders(session)

            now_ist = datetime.now(IST)
            today = now_ist.date()

            if today != current_day:
                current_day = today
                screener_done_today = False
                mid_day_done_today = False
                if orchestrator is not None:
                    orchestrator.clear_trades()

            if screener_enabled and not screener_done_today and (now_ist.hour, now_ist.minute) >= (win_h, win_m):
                screener_done_today = True
                log.info("Pre-market screener running for %s...", today)
                try:
                    new_runtimes = reselect_fn()
                    if new_runtimes:
                        runtimes[:] = new_runtimes
                        log.info(
                            "Symbols re-selected: %s",
                            [f"{rt.strategy.strategy_name}:{rt.strategy.symbol}" for rt in runtimes],
                        )
                except Exception as exc:
                    log.error("Daily symbol re-selection failed: %s", exc)

            if not is_market_open():
                mins = minutes_to_market_open()
                if mins:
                    log.info("Market opens in %d min - sleeping", mins)
                    stop_event.wait(timeout=min(mins * 60, 300))
                else:
                    log.info("Market closed - sleeping 5 min")
                    stop_event.wait(timeout=300)
                continue

            # AI mid-day review (once per day at configured time)
            if (
                orchestrator is not None
                and orchestrator.enabled
                and not mid_day_done_today
                and (now_ist.hour, now_ist.minute) >= (_mid_day_h, _mid_day_m)
            ):
                mid_day_done_today = True
                try:
                    trades_so_far = []
                    for rt in runtimes:
                        popped = rt.strategy.pop_completed_trades()
                        trades_so_far.extend(popped)
                        orchestrator.collect_trades(popped)
                    active_syms = [rt.strategy.symbol for rt in runtimes]
                    regime_state = orchestrator.get_regime_state(regime_filter)
                    adjustments = orchestrator.mid_day(trades_so_far, active_syms, regime_state)
                    if adjustments:
                        updated_config, syms_drop = orchestrator.apply_mid_day_adjustments(copy.deepcopy(config), adjustments)
                        config = updated_config
                        risk_cfg = config.get("risk", {})
                        # Propagate updated risk params to each runtime's own config and strategy
                        for rt in runtimes:
                            rt.config["risk"] = dict(risk_cfg)
                            if "sl_atr_multiplier" in adjustments.get("param_changes", {}):
                                rt.strategy.sl_atr_multiplier = float(risk_cfg.get("sl_atr_multiplier", rt.strategy.sl_atr_multiplier))
                            if "tp_atr_multiplier" in adjustments.get("param_changes", {}):
                                rt.strategy.tp_atr_multiplier = float(risk_cfg.get("tp_atr_multiplier", rt.strategy.tp_atr_multiplier))
                        if syms_drop:
                            log.info("AI mid-day: dropping symbols %s from new entries", syms_drop)
                except Exception as exc:
                    log.warning("AI mid-day review error: %s", exc)

            # Update market regime periodically (once per loop, not per symbol)
            now_mono = time.monotonic()
            if regime_filter.enabled and now_mono - _last_regime_update >= regime_update_interval:
                _last_regime_update = now_mono
                try:
                    regime_filter.update(session)
                except Exception as exc:
                    log.warning("Regime filter update error: %s", exc)

            for runtime in runtimes:
                strategy = runtime.strategy
                strat_cfg = runtime.config["strategy"]
                rlog = get_logger(f"loop.{strategy.symbol}.{strategy.strategy_name}")

                # Proactive session health check (#26)
                try:
                    session.refresh_if_needed(warn_minutes=30)
                except Exception as exc:
                    rlog.warning("Session refresh check failed: %s", exc)

                try:
                    ltp = get_ltp_single(
                        session,
                        strat_cfg["exchange"],
                        strat_cfg["symbol"],
                        strategy.token,
                    )
                except AngelOneAPIError as exc:
                    rlog.warning("LTP fetch failed: %s - skipping iteration", exc)
                    continue

                signal_name = strategy.generate_signal(session)

                # Check if paused via Telegram command (block entries, allow exits)
                _is_paused = cmd_handler is not None and cmd_handler.is_paused

                if signal_name == "BUY":
                    if _is_paused:
                        rlog.info("BUY blocked: bot is paused via Telegram")
                    else:
                        regime_ok, regime_reason = regime_filter.allows_entry()
                        if not regime_ok:
                            rlog.info("BUY blocked by regime filter: %s", regime_reason)
                        else:
                            can_trade, reason = risk_mgr.check_can_trade()
                            if can_trade:
                                runtime.sl_order_id = execute_buy(session, strategy, risk_mgr, runtime.execution, ltp, dry_run)
                            else:
                                rlog.warning("BUY blocked by risk manager: %s", reason)

                elif signal_name == "SELL":
                    execute_sell(session, strategy, runtime.execution, ltp, dry_run, runtime.sl_order_id)
                    if not strategy.in_position:
                        runtime.sl_order_id = None

                elif signal_name == "SHORT":
                    if _is_paused:
                        rlog.info("SHORT blocked: bot is paused via Telegram")
                    else:
                        regime_ok, regime_reason = regime_filter.allows_entry()
                        if not regime_ok:
                            rlog.info("SHORT blocked by regime filter: %s", regime_reason)
                        else:
                            can_trade, reason = risk_mgr.check_can_trade()
                            if can_trade:
                                runtime.sl_order_id = execute_short(session, strategy, risk_mgr, runtime.execution, ltp, dry_run)
                            else:
                                rlog.warning("SHORT blocked by risk manager: %s", reason)

                elif signal_name == "COVER":
                    execute_cover(session, strategy, runtime.execution, ltp, dry_run, runtime.sl_order_id)
                    if not strategy.in_position:
                        runtime.sl_order_id = None

                pos = strategy.get_state()
                if pos["in_position"]:
                    tsl_info = (
                        f" tsl={pos['tsl_sl']:.2f}{'(active)' if pos['tsl_activated'] else '(pending)'}"
                        if pos["tsl_sl"] > 0 else ""
                    )
                    rlog.info(
                        "OPEN %s qty=%d entry=%.2f ltp=%.2f unrealised=%.2f%s",
                        pos["direction"],
                        pos["entry_qty"],
                        pos["entry_price"],
                        pos["ltp"] or ltp,
                        pos["unrealised_pnl"],
                        tsl_info,
                    )

                if runtime.execution.is_circuit_open():
                    reason = runtime.execution.circuit_reason()
                    if notifier is not None and reason and reason != runtime.last_circuit_reason:
                        notifier.notify_halt(f"{strategy.symbol} execution guard: {reason}")
                    runtime.last_circuit_reason = reason
                else:
                    runtime.last_circuit_reason = ""

            risk = risk_mgr.status()
            extra = ""
            any_guard = any(runtime.execution.is_circuit_open() for runtime in runtimes)
            if risk["halted"]:
                extra += " HALTED"
                if notifier is not None and risk["halt_reason"] != last_halt_reason:
                    notifier.notify_halt(f"Risk halt: {risk['halt_reason']}")
                last_halt_reason = risk["halt_reason"]
            else:
                last_halt_reason = ""
            if any_guard:
                extra += " EXEC_GUARD"

            log.info(
                "Risk day_pnl=%.2f trades=%d/%d consec_losses=%d limit_used=%.1f%%%s",
                risk["daily_pnl"],
                risk["trades_today"],
                risk_mgr.max_trades_per_day,
                risk["consecutive_losses"],
                risk["loss_limit_used_pct"],
                extra,
            )

        except AngelOneAPIError as exc:
            log.error("API error in strategy loop: %s", exc)
        except Exception as exc:
            log.exception("Unexpected error in strategy loop: %s", exc)

        stop_event.wait(timeout=loop_interval)


def _exchange_type(exchange: str) -> int:
    return {
        "NSE": ExchangeType.NSE_CM,
        "BSE": ExchangeType.BSE_CM,
        "NFO": ExchangeType.NSE_FO,
        "MCX": ExchangeType.MCX_FO,
        "CDS": ExchangeType.CDS_FO,
    }.get(exchange.upper(), ExchangeType.NSE_CM)


def main() -> None:
    parser = argparse.ArgumentParser(description="AngelOne Trading Bot")
    parser.add_argument("--config", default="config.json", help="Path to config.json (default: config.json)")
    args = parser.parse_args()

    config = load_config(args.config)
    setup_logging(config["bot"].get("log_level", "INFO"))
    log = get_logger("main")

    notifier = TelegramNotifier.from_config(config)

    # Telegram command handler (bidirectional control)
    cmd_handler: Optional[TelegramCommandHandler] = None
    tg_cfg = config.get("notifications", {}).get("telegram", {})
    if tg_cfg.get("enabled") and tg_cfg.get("commands_enabled", True):
        cmd_handler = TelegramCommandHandler(
            notifier,
            poll_interval_sec=tg_cfg.get("poll_interval_sec", 3.0),
        )

    journal = None
    if config.get("trade_journal", {}).get("enabled", True):
        journal = TradeJournal(path=config.get("trade_journal", {}).get("path", "data/journal/trades.sqlite3"))

    log.info("=== Bot starting dry_run=%s ===", config["bot"]["dry_run"])
    if config["bot"]["dry_run"]:
        log.info("╔══════════════════════════════════════════╗")
        log.info("║         DRY RUN MODE — NO REAL ORDERS    ║")
        log.info("╚══════════════════════════════════════════╝")
    else:
        log.warning("╔══════════════════════════════════════════╗")
        log.warning("║   ⚠  LIVE TRADING — REAL MONEY AT RISK   ║")
        log.warning("╚══════════════════════════════════════════╝")

    # AI orchestrator (3-window: pre-market, mid-day, post-market)
    orchestrator = AIOrchestrator(config)
    if orchestrator.enabled:
        log.info("AI orchestrator enabled: %s/%s", orchestrator.client.provider, orchestrator.client.model)

    session = AngelSession.from_env()
    stop_event = threading.Event()
    market_feed: Optional[MarketFeed] = None
    order_feed: Optional[OrderFeed] = None
    refresh_stop = threading.Event()
    runtimes: list[StrategyRuntime] = []
    risk_mgr: Optional[RiskManager] = None

    def _shutdown(sig, frame):
        log.info("Shutdown signal received - stopping gracefully...")
        stop_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Emergency cleanup: cancel SL orders on unplanned process death (#7)
    def _emergency_cleanup():
        for rt in runtimes:
            if rt.sl_order_id:
                try:
                    cancel_order(session, rt.sl_order_id)
                except Exception:
                    pass
            if rt.strategy.in_position and not config["bot"]["dry_run"]:
                log.warning("Emergency: %s still has open position — manual intervention needed", rt.strategy.symbol)

    atexit.register(_emergency_cleanup)

    try:
        session.login()
        log.info("Logged in: %s", session.tokens.client_code)

        runtime_configs = build_strategy_configs(config, session=session)
        if not runtime_configs:
            if config.get("screener", {}).get("enabled", False):
                log.warning("Screener returned 0 symbols at startup — bot will idle and retry at next screener window")
                runtime_configs = []
            else:
                raise RuntimeError("No strategies resolved from config — check config.json strategy section")

        # AI Pre-Market Window: adjust strategy/params before trading starts
        if orchestrator.enabled:
            try:
                screener_picks = []
                for rc in runtime_configs:
                    strat = rc.get("strategy", {})
                    screener_picks.append({
                        "symbol": strat.get("symbol", ""),
                        "score": strat.get("screener_score", 0),
                        "close": strat.get("close", 0),
                        "atr": strat.get("atr", 0),
                    })
                regime_state = {"regime": "UNKNOWN", "adx": 0.0, "atr_pct": 0.0}
                # Try to compute real regime state for AI pre-market
                try:
                    regime_cfg = config.get("regime_filter", {})
                    if regime_cfg.get("enabled", False):
                        pre_regime = MarketRegimeFilter(regime_cfg)
                        pre_regime.update(session)
                        regime_state = pre_regime.state()
                except Exception:
                    pass  # Fall back to UNKNOWN
                plan = orchestrator.pre_market(
                    screener_picks=screener_picks,
                    regime_state=regime_state,
                    journal_path=config.get("trade_journal", {}).get("path", "data/journal/trades.sqlite3"),
                )
                if plan:
                    config = orchestrator.apply_day_plan(copy.deepcopy(config), plan)
                    # Re-build runtime configs if AI changed the strategy
                    if "strategy" in plan:
                        log.info("AI changed strategy, rebuilding configs...")
                        runtime_configs = build_strategy_configs(config, session=session)
                        if not runtime_configs:
                            raise RuntimeError("No strategies resolved after AI adjustment")
            except Exception as exc:
                log.warning("AI pre-market failed (continuing with defaults): %s", exc)

        risk_mgr = RiskManager(config)
        risk_mgr.sync_from_portfolio(session)
        exec_cfg = ExecutionProtectionConfig.from_config(config)

        for runtime_config in runtime_configs:
            strategy = load_strategy(runtime_config)
            strategy.on_start(session)
            execution = ExecutionManager(
                strategy=strategy,
                risk_mgr=risk_mgr,
                config=exec_cfg,
                journal=journal,
                notifier=notifier,
            )
            runtimes.append(
                StrategyRuntime(
                    config=runtime_config,
                    strategy=strategy,
                    execution=execution,
                )
            )

        recover_positions(session, runtimes, notifier=notifier)

        token_to_runtime = {str(runtime.strategy.token): runtime for runtime in runtimes}
        subscriptions = [
            (
                f"{runtime.strategy.exchange}:{runtime.strategy.symbol}",
                _exchange_type(runtime.strategy.exchange),
                [runtime.strategy.token],
            )
            for runtime in runtimes
        ]

        def _route_tick(tick: dict) -> None:
            runtime = token_to_runtime.get(str(tick.get("token", "")))
            if runtime is not None:
                runtime.strategy.on_tick(tick)

        def _route_order_update(update: dict) -> None:
            for runtime in runtimes:
                runtime.execution.process_order_update(update, source="ws")

        market_feed = MarketFeed(
            session=session,
            on_tick=_route_tick,
            on_error=lambda exc: log.error("MarketFeed error: %s", exc),
            on_connect=lambda: log.info("MarketFeed connected"),
            on_disconnect=lambda: log.warning("MarketFeed disconnected"),
        )
        market_feed.subscribe(instruments=subscriptions, mode=1)
        market_feed.start()

        def _reselect() -> list[StrategyRuntime]:
            nonlocal market_feed
            log.info("Re-selecting symbols via screener...")
            try:
                new_configs = build_strategy_configs(config, session=session, force_screener=True)
            except Exception as exc:
                log.error("build_strategy_configs failed during re-selection: %s", exc)
                return list(runtimes)

            existing = {
                (rt.strategy.exchange.upper(), rt.strategy.symbol.upper()): rt
                for rt in runtimes
            }
            new_runtimes: list[StrategyRuntime] = []
            for cfg in new_configs:
                sym = str(cfg["strategy"]["symbol"]).upper()
                exch = str(cfg["strategy"]["exchange"]).upper()
                existing_rt = existing.get((exch, sym))
                if existing_rt is not None:
                    new_runtimes.append(existing_rt)
                else:
                    try:
                        strategy = load_strategy(cfg)
                        strategy.on_start(session)
                        execution = ExecutionManager(
                            strategy=strategy,
                            risk_mgr=risk_mgr,
                            config=exec_cfg,
                            journal=journal,
                            notifier=notifier,
                        )
                        new_runtimes.append(StrategyRuntime(config=cfg, strategy=strategy, execution=execution))
                    except Exception as exc:
                        log.error("Could not build runtime for %s:%s: %s", exch, sym, exc)

            if not new_runtimes:
                log.warning("Re-selection returned no valid symbols — keeping current runtimes")
                return list(runtimes)

            new_keys = {(rt.strategy.exchange.upper(), rt.strategy.symbol.upper()) for rt in new_runtimes}
            for rt in runtimes:
                if (rt.strategy.exchange.upper(), rt.strategy.symbol.upper()) not in new_keys:
                    try:
                        rt.strategy.on_stop()
                    except Exception:
                        pass

            token_to_runtime.clear()
            token_to_runtime.update({str(rt.strategy.token): rt for rt in new_runtimes})

            new_subscriptions = [
                (
                    f"{rt.strategy.exchange}:{rt.strategy.symbol}",
                    _exchange_type(rt.strategy.exchange),
                    [rt.strategy.token],
                )
                for rt in new_runtimes
            ]
            # Atomic feed swap: build new feed first, only stop old after new connects
            new_feed = MarketFeed(
                session=session,
                on_tick=_route_tick,
                on_error=lambda exc: log.error("MarketFeed error: %s", exc),
                on_connect=lambda: log.info("MarketFeed connected after re-selection"),
                on_disconnect=lambda: log.warning("MarketFeed disconnected"),
            )
            new_feed.subscribe(instruments=new_subscriptions, mode=1)
            try:
                new_feed.start()
            except Exception as exc:
                log.error("New MarketFeed failed to start — keeping old feed: %s", exc)
                return list(runtimes)
            # New feed started successfully; stop old feed
            try:
                market_feed.stop()
            except Exception:
                pass
            market_feed = new_feed

            log.info("Re-selection: %d → %d strategies", len(runtimes), len(new_runtimes))
            return new_runtimes

        order_feed = OrderFeed(
            session=session,
            on_order_update=_route_order_update,
            on_error=lambda exc: log.warning("OrderFeed: %s", exc),
        )
        order_feed.start()

        refresh_thread = threading.Thread(
            target=_session_refresh_loop,
            args=(session, refresh_stop),
            daemon=True,
            name="SessionRefresh",
        )
        refresh_thread.start()

        log.info(
            "=== All feeds started - running %d strategy instance(s): %s ===",
            len(runtimes),
            [f"{runtime.strategy.strategy_name}:{runtime.strategy.symbol}" for runtime in runtimes],
        )

        # Start Telegram command handler (bidirectional control)
        if cmd_handler is not None:
            cmd_handler.set_bot_context(
                stop_event=stop_event,
                runtimes=runtimes,
                risk_mgr=risk_mgr,
                session=session,
                config=config,
                squareoff_fn=_squareoff_with_retry,
            )
            cmd_handler.start()

        screener_live = bool(config.get("screener", {}).get("enabled", False))
        run_strategy_loop(
            session, runtimes, risk_mgr, config, stop_event,
            notifier=notifier,
            reselect_fn=_reselect if screener_live else None,
            orchestrator=orchestrator,
            cmd_handler=cmd_handler,
        )

    except KeyboardInterrupt:
        stop_event.set()

    finally:
        log.info("=== Shutting down ===")

        # AI Post-Market Window: review the day's trades and extract lessons
        if orchestrator.enabled:
            try:
                # Collect any remaining trades not yet popped
                for runtime in runtimes:
                    orchestrator.collect_trades(runtime.strategy.pop_completed_trades())
                regime_state = {"regime": "UNKNOWN", "adx": 0.0, "atr_pct": 0.0}
                orchestrator.post_market(orchestrator.get_collected_trades(), regime_state)
            except Exception as exc:
                log.warning("AI post-market review failed: %s", exc)

        # Stop Telegram command handler
        if cmd_handler is not None:
            try:
                cmd_handler.stop()
            except Exception:
                pass

        # Force-close any open positions with retry before shutdown (#4)
        for runtime in runtimes:
            if runtime.strategy.in_position and not config["bot"]["dry_run"]:
                try:
                    ltp = get_ltp_single(session, runtime.strategy.exchange, runtime.strategy.symbol, runtime.strategy.token)
                    _squareoff_with_retry(session, runtime, ltp, dry_run=False)
                except Exception as exc:
                    log.critical("Emergency squareoff failed for %s: %s", runtime.strategy.symbol, exc)

        for runtime in runtimes:
            try:
                runtime.strategy.on_stop()
            except Exception:
                pass

        if notifier is not None and risk_mgr is not None:
            try:
                risk = risk_mgr.status()
                notifier.notify_daily_summary(
                    f"Strategies: {len(runtimes)} | day_pnl={risk.get('daily_pnl', 0.0):.2f} | trades={risk.get('trades_today', 0)}"
                )
            except Exception:
                pass

        try:
            if market_feed is not None:
                market_feed.stop()
        except Exception:
            pass
        try:
            if order_feed is not None:
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
