"""
websocket_feed.py — AngelOne SmartAPI · Real-Time WebSocket Feeds
=================================================================
Two independent WebSocket connections:

  1. MarketFeed  — SmartWebSocketV2 streaming live tick data (LTP / Quote / SnapQuote)
  2. OrderFeed   — Order status updates (fills, rejections, cancellations)

Both run in background daemon threads so they don't block your strategy loop.

Design:
  • Prices from the MarketFeed are in PAISE — always divide by 100 before using.
    The parsed-tick helpers in this module do this conversion automatically.
  • Reconnection with exponential backoff is built-in for both feeds.
  • Use the `on_tick` callback to pipe ticks into your strategy signal logic.

Dependencies:
    pip install smartapi-python websocket-client

Usage:
    feed = MarketFeed(
        session    = session,
        on_tick    = my_tick_handler,
        on_error   = my_error_handler,
    )
    feed.subscribe([("NSE", ExchangeType.NSE_CM, ["3045", "2885"])], mode=WSMode.SNAP_QUOTE)
    feed.start()   # background thread
    ...
    feed.stop()
"""

import time
import threading
import logging
from typing import Callable, List, Optional, Tuple

from broker.constants import ExchangeType, WSMode
from broker.session import AngelSession, SessionTokens
from utils import get_logger, paise_to_rupees, AngelOneAPIError

_log = get_logger(__name__)

# Maximum seconds to wait between reconnect attempts
MAX_RECONNECT_DELAY = 60
INITIAL_RECONNECT_DELAY = 2
RATE_LIMIT_RECONNECT_DELAY = 30   # 429: initial wait; doubles each attempt up to MAX
RATE_LIMIT_MAX_DELAY      = 300  # 5 minutes max between 429 retries


class _RateLimitError(Exception):
    """Raised when the WebSocket server responds with HTTP 429."""


# ──────────────────────────────────────────────────────────────────────────────
# TICK PARSER — convert raw AngelOne tick dict to clean ₹ values
# ──────────────────────────────────────────────────────────────────────────────

def parse_tick(raw: dict) -> dict:
    """
    Parse a raw tick message from SmartWebSocketV2 into clean rupee values.

    AngelOne transmits all price fields in paise (integer).
    This function divides all price fields by 100, returning rupee floats.

    Args:
        raw : raw tick dict from on_data callback

    Returns:
        Cleaned tick dict with all prices in rupees (₹):
            token           : instrument token string
            exchange_type   : exchange type int
            ltp             : last traded price (₹)
            open            : day open price (₹)
            high            : day high price (₹)
            low             : day low price (₹)
            close           : previous close (₹)
            avg_trade_price : volume-weighted average price (₹)
            volume          : total traded volume today
            buy_qty         : total buy quantity
            sell_qty        : total sell quantity
            last_qty        : last trade quantity
            timestamp       : last trade timestamp string
            best_5_buy      : list of {price, qty} dicts
            best_5_sell     : list of {price, qty} dicts

    Example:
        def on_tick(tick):
            t = parse_tick(tick)
            print(f"Token {t['token']}: LTP ₹{t['ltp']}")
    """
    price_fields = [
        "last_traded_price",
        "average_traded_price",
        "open_price_of_the_day",
        "high_price_of_the_day",
        "low_price_of_the_day",
        "closed_price",
        "52_week_high_price",
        "52_week_low_price",
        "upper_circuit",
        "lower_circuit",
    ]

    cleaned = {
        "token":        str(raw.get("token", "")),
        "exchange_type": raw.get("exchange_type", 0),
        "ltp":          paise_to_rupees(raw.get("last_traded_price", 0)),
        "open":         paise_to_rupees(raw.get("open_price_of_the_day", 0)),
        "high":         paise_to_rupees(raw.get("high_price_of_the_day", 0)),
        "low":          paise_to_rupees(raw.get("low_price_of_the_day", 0)),
        "close":        paise_to_rupees(raw.get("closed_price", 0)),
        "avg_trade_price": paise_to_rupees(raw.get("average_traded_price", 0)),
        "volume":       raw.get("volume_trade_for_the_day", 0),
        "buy_qty":      raw.get("total_buy_quantity", 0),
        "sell_qty":     raw.get("total_sell_quantity", 0),
        "last_qty":     raw.get("last_traded_quantity", 0),
        "timestamp":    raw.get("last_traded_timestamp", ""),
        "week_52_high": paise_to_rupees(raw.get("52_week_high_price", 0)),
        "week_52_low":  paise_to_rupees(raw.get("52_week_low_price", 0)),
    }

    # Parse best 5 buy/sell depth
    cleaned["best_5_buy"]  = [
        {"price": paise_to_rupees(d.get("price", 0)), "qty": d.get("quantity", 0)}
        for d in raw.get("best_5_buy_data", [])
    ]
    cleaned["best_5_sell"] = [
        {"price": paise_to_rupees(d.get("price", 0)), "qty": d.get("quantity", 0)}
        for d in raw.get("best_5_sell_data", [])
    ]

    return cleaned


# ──────────────────────────────────────────────────────────────────────────────
# MARKET FEED — SmartWebSocketV2 wrapper
# ──────────────────────────────────────────────────────────────────────────────

class MarketFeed:
    """
    Real-time market data feed using AngelOne SmartWebSocketV2.

    Streams live ticks for subscribed instruments. Runs in a background
    daemon thread. Automatically reconnects with exponential backoff.

    Usage:
        def handle_tick(tick: dict):
            t = parse_tick(tick)
            print(f"{t['token']}: ₹{t['ltp']}")

        feed = MarketFeed(session=session, on_tick=handle_tick)
        feed.subscribe([
            ("NSE_CM", ExchangeType.NSE_CM, ["3045", "26009"]),   # SBIN + NIFTY50
            ("NFO",    ExchangeType.NSE_FO,  ["35001"]),
        ], mode=WSMode.SNAP_QUOTE)
        feed.start()
        time.sleep(60)
        feed.stop()
    """

    def __init__(
        self,
        session:          AngelSession,
        on_tick:          Callable[[dict], None],
        on_error:         Optional[Callable[[Exception], None]] = None,
        on_connect:       Optional[Callable[[], None]] = None,
        on_disconnect:    Optional[Callable[[], None]] = None,
        auto_reconnect:   bool = True,
        parse_prices:     bool = True,
    ):
        """
        Args:
            session        : authenticated AngelSession (must have valid feed_token)
            on_tick        : callback(tick_dict) — called for every incoming tick
                             tick_dict prices are in ₹ if parse_prices=True, else paise
            on_error       : optional callback(exception) — called on WebSocket errors
            on_connect     : optional callback() — called when connection is established
            on_disconnect  : optional callback() — called when connection drops
            auto_reconnect : if True (default), reconnects with exponential backoff
            parse_prices   : if True (default), runs parse_tick() before calling on_tick
        """
        self._session        = session
        self._on_tick        = on_tick
        self._on_error       = on_error
        self._on_connect     = on_connect
        self._on_disconnect  = on_disconnect
        self._auto_reconnect = auto_reconnect
        self._parse_prices   = parse_prices

        # Pending subscriptions (exchange_label, exchangeType, tokens, mode)
        self._subscriptions: List[Tuple[str, int, List[str], int]] = []
        self._sub_lock       = threading.Lock()  # guards _subscriptions and _ws

        self._ws             = None
        self._thread: Optional[threading.Thread] = None
        self._running        = False
        self._correlation_id = "bot_feed_001"
        self._last_tick_time = 0.0  # Track last tick for gap detection (#21)
        self._gap_warned     = False

    def subscribe(
        self,
        instruments: List[Tuple[str, int, List[str]]],
        mode: int = WSMode.SNAP_QUOTE,
    ) -> None:
        """
        Register instruments to subscribe to once connected.

        Call this before start() or after the feed is running.
        On reconnection, all registered subscriptions are re-applied automatically.

        Args:
            instruments : list of (label, exchangeType, tokens) tuples
                          label      : any descriptive string (used for logging)
                          exchangeType : ExchangeType constant e.g. ExchangeType.NSE_CM
                          tokens     : list of instrument token strings

            mode        : WSMode constant
                          WSMode.LTP        = LTP only (fastest, least data)
                          WSMode.QUOTE      = basic quote
                          WSMode.SNAP_QUOTE = full market depth + OHLCV (default)

        Example:
            feed.subscribe([
                ("cash_equities", ExchangeType.NSE_CM, ["3045", "2885"]),
                ("nifty_index",   ExchangeType.NSE_CM, ["26009"]),
            ], mode=WSMode.SNAP_QUOTE)
        """
        for (label, exch_type, tokens) in instruments:
            with self._sub_lock:
                self._subscriptions.append((label, exch_type, tokens, mode))
            _log.info(
                "Registered subscription: %s tokens=%s mode=%d", label, tokens, mode
            )

    def _build_token_list(self) -> List[dict]:
        """
        Build the token_list structure expected by SmartWebSocketV2.subscribe().
        Groups subscriptions by exchangeType.
        """
        with self._sub_lock:
            subs = list(self._subscriptions)
        groups: dict = {}
        for (_, exch_type, tokens, _) in subs:
            if exch_type not in groups:
                groups[exch_type] = []
            groups[exch_type].extend(tokens)
        return [{"exchangeType": k, "tokens": list(set(v))} for k, v in groups.items()]

    def _get_mode(self) -> int:
        """Return the highest-priority mode across all subscriptions."""
        with self._sub_lock:
            subs = list(self._subscriptions)
        if not subs:
            return WSMode.SNAP_QUOTE
        return max(s[3] for s in subs)

    def start(self) -> None:
        """
        Start the market feed in a background daemon thread.

        Returns immediately. The connection and subscriptions happen asynchronously.
        Use on_connect callback to know when data starts flowing.

        Example:
            feed.start()
            print("Feed started — ticks will arrive in on_tick callback")
        """
        if self._running:
            _log.warning("MarketFeed already running — call stop() first to restart")
            return
        self._running = True
        self._thread  = threading.Thread(target=self._run_loop, daemon=True, name="MarketFeed")
        self._thread.start()
        _log.info("MarketFeed thread started")

    def stop(self) -> None:
        """
        Stop the market feed and close the WebSocket connection.

        Safe to call even if feed is not running.
        """
        self._running = False
        with self._sub_lock:
            ws = self._ws
        if ws:
            try:
                ws.close_connection()
            except Exception:
                pass
        _log.info("MarketFeed stopped")

    def _run_loop(self) -> None:
        """
        Internal reconnection loop.
        Runs in a background thread and handles exponential backoff reconnects.
        """
        delay      = INITIAL_RECONNECT_DELAY
        rl_delay   = RATE_LIMIT_RECONNECT_DELAY
        while self._running:
            try:
                self._connect()
                delay    = INITIAL_RECONNECT_DELAY   # reset on successful connect
                rl_delay = RATE_LIMIT_RECONNECT_DELAY
            except _RateLimitError as e:
                # 429: zombie connections still alive on the server; back off exponentially
                _log.warning(
                    "MarketFeed rate-limited (429) — waiting %ds for server to release "
                    "old connections from previous runs...", rl_delay
                )
                if self._on_error:
                    try:
                        self._on_error(e)
                    except Exception:
                        pass
                if not self._running:
                    break
                time.sleep(rl_delay)
                rl_delay = min(rl_delay * 2, RATE_LIMIT_MAX_DELAY)
                continue
            except Exception as e:
                _log.error("MarketFeed connection error: %s", e)
                if self._on_error:
                    try:
                        self._on_error(e)
                    except Exception:
                        pass

            if not self._running:
                break

            if self._auto_reconnect:
                _log.info("Reconnecting MarketFeed in %ds...", delay)
                time.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)
            else:
                break

    def _connect(self) -> None:
        """Create and connect a SmartWebSocketV2 instance."""
        # Explicitly close any previous instance before creating a new one
        with self._sub_lock:
            old_ws = self._ws
            self._ws = None
        if old_ws is not None:
            try:
                old_ws.close_connection()
            except Exception:
                pass

        tokens = self._session.tokens
        if not tokens:
            raise AngelOneAPIError("Session has no tokens — login first")

        try:
            from SmartApi.smartWebSocketV2 import SmartWebSocketV2
        except ImportError as e:
            raise ImportError(
                "smartapi-python is required for MarketFeed: pip install smartapi-python"
            ) from e

        ws = SmartWebSocketV2(
            f"Bearer {tokens.jwt_token}",
            tokens.api_key,
            tokens.client_code,
            tokens.feed_token,
        )

        # ── Intercept raw library callbacks before they are swallowed ─────────
        import types as _types
        _429_detected = threading.Event()
        _429_detected.clear()  # ensure clean state for each connection attempt
        _orig_internal_on_error = ws._on_error.__func__
        _orig_internal_on_close = ws._on_close.__func__

        def _raw_on_error(self_ws, wsapp, error):
            err_str = str(error)
            _log.error("MarketFeed raw server error: [%s] %s", type(error).__name__, error)
            if "429" in err_str:
                _429_detected.set()
                self_ws.RESUBSCRIBE_FLAG = False  # stop library's internal reconnect loop
            _orig_internal_on_error(self_ws, wsapp, error)

        def _patched_on_close(self_ws, wsapp, close_status_code=None, close_msg=None):
            # websocket-client calls on_close(ws, code, msg) but library expects 1 arg
            _orig_internal_on_close(self_ws, wsapp)

        ws._on_error = _types.MethodType(_raw_on_error, ws)
        ws._on_close = _types.MethodType(_patched_on_close, ws)

        # ── Bind callbacks ────────────────────────────────────────────────────
        def _on_open(wsapp):
            _log.info("MarketFeed WebSocket connected")
            if self._on_connect:
                self._on_connect()
            token_list = self._build_token_list()
            if token_list:
                ws.subscribe(
                    correlation_id = self._correlation_id,
                    mode           = self._get_mode(),
                    token_list     = token_list,
                )
                _log.info(
                    "Subscribed %d exchange groups, mode=%d",
                    len(token_list), self._get_mode()
                )

        def _on_data(wsapp, message):
            try:
                tick = parse_tick(message) if self._parse_prices else message
                # Detect tick gaps from reconnection (#21)
                now = time.monotonic()
                if self._last_tick_time > 0:
                    gap = now - self._last_tick_time
                    if gap > 10.0 and not self._gap_warned:
                        _log.warning(
                            "Tick gap detected: %.1fs since last tick — TSL may have missed price moves",
                            gap,
                        )
                        self._gap_warned = True
                    elif gap <= 5.0:
                        self._gap_warned = False
                self._last_tick_time = now
                self._on_tick(tick)
            except Exception as e:
                _log.warning("Error processing tick: %s", e)

        def _on_error(wsapp, error):
            _log.error("MarketFeed WebSocket error: %s", error)
            if self._on_error:
                self._on_error(error)

        def _on_close(wsapp):
            _log.warning("MarketFeed WebSocket closed")
            if self._on_disconnect:
                self._on_disconnect()

        ws.on_open  = _on_open
        ws.on_data  = _on_data
        ws.on_error = _on_error
        ws.on_close = _on_close

        with self._sub_lock:
            self._ws = ws
        ws.connect()   # blocking until connection closes

        if _429_detected.is_set():
            raise _RateLimitError(
                "429 Too Many Requests — server connection limit exceeded. "
                "Old sessions from previous runs are still open. Waiting for them to expire."
            )


# ──────────────────────────────────────────────────────────────────────────────
# ORDER UPDATE FEED — real-time order status stream
# ──────────────────────────────────────────────────────────────────────────────

class OrderFeed:
    """
    Real-time order update feed — streams fill/rejection/cancellation events.

    Runs in a background daemon thread. Use the on_order_update callback
    to update your bot's open-position state without polling the order book.

    Usage:
        def handle_order(update: dict):
            print(f"Order {update['uniqueorderid']}: {update['status']}")
            if update['status'] == 'complete':
                # record fill in your position tracker
                pass

        order_feed = OrderFeed(session=session, on_order_update=handle_order)
        order_feed.start()
    """

    def __init__(
        self,
        session:           AngelSession,
        on_order_update:   Callable[[dict], None],
        on_error:          Optional[Callable[[Exception], None]] = None,
        auto_reconnect:    bool = True,
    ):
        """
        Args:
            session          : authenticated AngelSession
            on_order_update  : callback(order_update_dict) — called on each order event
            on_error         : optional callback(exception)
            auto_reconnect   : reconnect on disconnect (default True)
        """
        self._session         = session
        self._on_order_update = on_order_update
        self._on_error        = on_error
        self._auto_reconnect  = auto_reconnect
        self._running         = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the order update feed in a background daemon thread."""
        if self._running:
            _log.warning("OrderFeed already running")
            return
        self._running = True
        self._thread  = threading.Thread(target=self._run_loop, daemon=True, name="OrderFeed")
        self._thread.start()
        _log.info("OrderFeed thread started")

    def stop(self) -> None:
        """Stop the order update feed."""
        self._running = False
        _log.info("OrderFeed stopped")

    def _run_loop(self) -> None:
        delay = INITIAL_RECONNECT_DELAY
        while self._running:
            try:
                self._connect()
                delay = INITIAL_RECONNECT_DELAY
            except AngelOneAPIError as e:
                err_str = str(e)
                if "403" in err_str or "401" in err_str:
                    _log.warning(
                        "OrderFeed disabled — server rejected auth (403/401). "
                        "Order fills will not be confirmed in real-time. "
                        "Check AngelOne API plan or IP whitelist settings."
                    )
                    self._running = False
                    break
                _log.error("OrderFeed connection error: %s", e)
                if self._on_error:
                    try:
                        self._on_error(e)
                    except Exception:
                        pass
            except Exception as e:
                _log.error("OrderFeed connection error: %s", e)
                if self._on_error:
                    try:
                        self._on_error(e)
                    except Exception:
                        pass

            if not self._running:
                break
            if self._auto_reconnect:
                _log.info("Reconnecting OrderFeed in %ds...", delay)
                time.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)
            else:
                break

    def _connect(self) -> None:
        tokens = self._session.tokens
        if not tokens:
            raise AngelOneAPIError("Session has no tokens — login first")

        try:
            from SmartApi.smartWebSocketOrderUpdate import SmartWebSocketOrderUpdate
        except ImportError as e:
            raise ImportError(
                "smartapi-python is required for OrderFeed: pip install smartapi-python"
            ) from e

        client = SmartWebSocketOrderUpdate(
            auth_token  = f"Bearer {tokens.jwt_token}",
            api_key     = tokens.api_key,
            client_code = tokens.client_code,
            feed_token  = tokens.feed_token,
        )

        # Track permanent auth failures (403) so we don't retry forever
        _auth_failed = threading.Event()

        def _on_message(ws, message):
            try:
                import json
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                if isinstance(message, str):
                    message = json.loads(message)
                if not isinstance(message, dict):
                    return  # ignore non-dict messages (e.g. heartbeat pings)
                self._on_order_update(message)
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # ignore malformed or binary-only frames
            except Exception as e:
                _log.warning("Error in on_order_update handler: %s", e)

        def _on_error(wsapp, error):
            err_str = str(error)
            if "403" in err_str or "401" in err_str:
                _auth_failed.set()
            client.__class__.on_error(client, wsapp, error)

        client.on_message = _on_message
        client.on_error   = _on_error

        _log.info("OrderFeed WebSocket connecting...")
        client.connect()   # blocking (library retries internally up to MAX_CONNECTION_RETRY_ATTEMPTS)

        if _auth_failed.is_set():
            raise AngelOneAPIError(
                "OrderFeed auth rejected (403/401) — check AngelOne account permissions "
                "or whether IP whitelisting is required for the order update socket"
            )
