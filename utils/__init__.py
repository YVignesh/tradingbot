"""
utils.py — AngelOne SmartAPI · Shared Utilities
================================================
Currency conversion, logging setup, rate-limiter, and request header builder.
Import these helpers in every other module — never duplicate them.
"""

import time
import logging
import socket
import uuid
import threading
from typing import Dict, Optional
from datetime import datetime, timezone

from broker.constants import PAISE_PER_RUPEE, RateLimits

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────

def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Return a named logger with a consistent format.

    Usage:
        logger = get_logger(__name__)
        logger.info("Session started")

    Args:
        name  : usually pass __name__ from calling module
        level : logging level (default INFO)
    Returns:
        Configured Logger instance
    """
    logger = logging.getLogger(name)
    if not logger.handlers:                     # avoid duplicate handlers on re-import
        handler = logging.StreamHandler()
        fmt = logging.Formatter(
            "%(asctime)s  [%(levelname)-8s]  %(name)s  — %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


_log = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# CURRENCY CONVERSION
# ──────────────────────────────────────────────────────────────────────────────

def paise_to_rupees(paise: int | float) -> float:
    """
    Convert paise (integer) → rupees (float).

    AngelOne WebSocket V2 tick data and GTT prices are transmitted in paise.
    Always call this before displaying or using a price from those sources.

    Args:
        paise: price value in paise (e.g. 55075)
    Returns:
        price in rupees (e.g. 550.75)

    Example:
        >>> paise_to_rupees(55075)
        550.75
    """
    return round(paise / PAISE_PER_RUPEE, 2)


def rupees_to_paise(rupees: float) -> int:
    """
    Convert rupees (float) → paise (int).

    Required when sending prices to GTT create/modify endpoints.

    Args:
        rupees: price in rupees (e.g. 550.75)
    Returns:
        price in paise (e.g. 55075)

    Example:
        >>> rupees_to_paise(550.75)
        55075
    """
    return int(round(rupees * PAISE_PER_RUPEE))


def format_price(rupees: float) -> str:
    """
    Format a rupee price for display with ₹ prefix and 2 decimal places.

    Args:
        rupees: float price
    Returns:
        formatted string e.g. "₹550.75"
    """
    return f"₹{rupees:,.2f}"


# ──────────────────────────────────────────────────────────────────────────────
# REQUEST HEADER BUILDER
# ──────────────────────────────────────────────────────────────────────────────

def _get_local_ip() -> str:
    """Attempt to discover the machine's LAN IP address."""
    try:
        # Connect to a public address to discover local outbound IP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _get_mac_address() -> str:
    """Return the primary network interface MAC address as a hex string."""
    mac = uuid.getnode()
    return ":".join(f"{(mac >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))


def build_headers(
    jwt_token: str,
    api_key: str,
    public_ip: Optional[str] = None,
    local_ip: Optional[str] = None,
    mac_address: Optional[str] = None,
) -> Dict[str, str]:
    """
    Build the standard HTTP headers required by every AngelOne API call.

    AngelOne rejects requests that are missing any of these headers.
    For order endpoints (place/modify/cancel), the public IP MUST match
    the whitelisted static IP registered in the SmartAPI dashboard.

    Args:
        jwt_token   : Bearer token from generateSession / generateToken
        api_key     : Your SmartAPI private key
        public_ip   : Your static public IP (optional; auto-detected if None)
        local_ip    : Machine's LAN IP (optional; auto-detected if None)
        mac_address : Network MAC address (optional; auto-detected if None)
    Returns:
        Dict of headers ready to pass to requests.get/post
    """
    return {
        "Authorization":    f"Bearer {jwt_token}",
        "Content-Type":     "application/json",
        "Accept":           "application/json",
        "X-UserType":       "USER",
        "X-SourceID":       "WEB",
        "X-PrivateKey":     api_key,
        "X-ClientLocalIP":  local_ip or _get_local_ip(),
        "X-ClientPublicIP": public_ip or "0.0.0.0",   # must be set correctly for orders
        "X-MACAddress":     mac_address or _get_mac_address(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# TOKEN BUCKET RATE LIMITER
# ──────────────────────────────────────────────────────────────────────────────

class RateLimiter:
    """
    Thread-safe token-bucket rate limiter.

    Prevents hitting AngelOne's 10 orders/second cap (SEBI mandate, Apr 2026).
    Call `acquire()` before each order API request.

    Usage:
        limiter = RateLimiter(max_calls=10, period=1.0)
        limiter.acquire()          # blocks if needed; never raises
        smartApi.placeOrder(...)   # safe to call after acquire()

    Args:
        max_calls : maximum number of calls allowed in `period` seconds
        period    : time window in seconds (default 1.0 = per second)
    """

    def __init__(self, max_calls: int = RateLimits.ORDERS_PER_SECOND, period: float = 1.0):
        self.max_calls = max_calls
        self.period    = period
        self._lock     = threading.Lock()
        self._calls    = []   # timestamps of recent calls

    def acquire(self) -> None:
        """
        Block until a call slot is available within the rate window.
        Returns immediately if under the limit; sleeps otherwise.
        """
        with self._lock:
            now = time.monotonic()
            # Remove timestamps older than the window
            self._calls = [t for t in self._calls if now - t < self.period]
            if len(self._calls) >= self.max_calls:
                # Sleep until the oldest call falls out of the window
                sleep_for = self.period - (now - self._calls[0])
                _log.debug("Rate limit reached — sleeping %.3fs", sleep_for)
                time.sleep(max(sleep_for, 0))
            self._calls.append(time.monotonic())


# Singleton rate limiter shared across all order calls in the same process
order_rate_limiter = RateLimiter(max_calls=RateLimits.ORDERS_PER_SECOND, period=1.0)


# ──────────────────────────────────────────────────────────────────────────────
# API RESPONSE VALIDATOR
# ──────────────────────────────────────────────────────────────────────────────

class AngelOneAPIError(Exception):
    """
    Raised when AngelOne returns a non-success API response.

    Attributes:
        message   : human-readable error description
        errorcode : AngelOne error code string (e.g. "AG8001")
        raw       : full raw response dict for debugging
    """
    def __init__(self, message: str, errorcode: str = "", raw: dict = None):
        super().__init__(message)
        self.errorcode = errorcode
        self.raw       = raw or {}

    def __str__(self):
        return f"[{self.errorcode}] {super().__str__()}"


def validate_response(response: dict, context: str = "") -> dict:
    """
    Validate an API response dict and return the `data` payload.

    AngelOne always returns {"status": bool, "message": str, "data": ...}.
    This helper raises AngelOneAPIError on failure so callers don't need
    to check `status` themselves.

    Args:
        response : parsed JSON dict from the API
        context  : short description of the call (used in error messages)
    Returns:
        response["data"] on success
    Raises:
        AngelOneAPIError on status == False or missing keys
    """
    if not isinstance(response, dict):
        raise AngelOneAPIError(f"{context}: expected dict, got {type(response)}")

    if not response.get("status", False):
        msg  = response.get("message", "Unknown error")
        code = response.get("errorcode", "")
        _log.error("%s failed — [%s] %s", context or "API call", code, msg)
        raise AngelOneAPIError(msg, errorcode=code, raw=response)

    return response.get("data", {})


# ──────────────────────────────────────────────────────────────────────────────
# DATE / TIME HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def now_ist_str(fmt: str = "%Y-%m-%d %H:%M") -> str:
    """
    Return the current IST datetime as a string, suitable for the
    historical candle data `fromdate` / `todate` fields.

    Args:
        fmt: strftime format string (default matches AngelOne's expected format)
    Returns:
        e.g. "2024-01-15 09:15"
    """
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(IST).strftime(fmt)


def today_ist_str() -> str:
    """Return today's date in 'YYYY-MM-DD' format (IST)."""
    return now_ist_str("%Y-%m-%d")
