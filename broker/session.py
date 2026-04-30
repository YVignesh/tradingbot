"""
session.py — AngelOne SmartAPI · Authentication & Session Management
====================================================================
Handles login with TOTP, token refresh, profile fetch, and logout.
The `AngelSession` object is the entry point for every other module —
all helpers accept a session instance rather than raw credentials.

Key points:
  • Sessions expire daily at midnight IST — call refresh_if_needed() in
    any long-running bot's scheduler (e.g. at 23:30 IST).
  • Feed token (for WebSocket) is a separate credential from the JWT.
  • Static IP must be whitelisted in the SmartAPI dashboard for orders.

Dependencies:
    pip install smartapi-python pyotp requests
"""

import os
import sys
import ctypes
import pyotp
import requests
import threading
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

from broker.constants import BASE_URL, ENDPOINTS, REQUEST_TIMEOUT
from utils import get_logger, build_headers, validate_response, AngelOneAPIError

_log = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


# ──────────────────────────────────────────────────────────────────────────────
# DATA CLASS — Session Credentials
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SessionTokens:
    """
    Holds all tokens returned after a successful login.

    Attributes:
        jwt_token     : Bearer token for REST API authentication
        refresh_token : Used to renew jwt_token without re-login
        feed_token    : Used for WebSocket V2 connection
        client_code   : AngelOne client code (user ID)
        api_key       : SmartAPI private key
        public_ip     : Registered static IP (for order endpoints)
        local_ip      : Machine's LAN IP
        mac_address   : Machine's MAC address
        created_at    : Timestamp when session was created (IST)
    """
    jwt_token:     str
    refresh_token: str
    feed_token:    str
    client_code:   str
    api_key:       str
    public_ip:     str  = ""
    local_ip:      str  = ""
    mac_address:   str  = ""
    created_at:    datetime = field(default_factory=lambda: datetime.now(IST))

    @property
    def headers(self) -> dict:
        """Ready-to-use headers dict for REST API calls."""
        return build_headers(
            jwt_token   = self.jwt_token,
            api_key     = self.api_key,
            public_ip   = self.public_ip,
            local_ip    = self.local_ip,
            mac_address = self.mac_address,
        )

    def is_near_expiry(self, warn_minutes: int = 60) -> bool:
        """
        Returns True if the session is within `warn_minutes` of midnight IST.
        Use this to trigger a proactive token refresh in a scheduler.
        """
        now = datetime.now(IST)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return (midnight - now).total_seconds() < warn_minutes * 60


# ──────────────────────────────────────────────────────────────────────────────
# SESSION MANAGER
# ──────────────────────────────────────────────────────────────────────────────

class AngelSession:
    """
    Manages the full lifecycle of an AngelOne SmartAPI session.

    Usage (minimal):
        session = AngelSession(
            api_key    = "your_api_key",
            client_code= "your_client_code",
            mpin       = "your_mpin",
            totp_secret= "your_totp_base32_secret",
        )
        session.login()
        tokens = session.tokens   # use in all other helpers

    Usage (with env vars — recommended for bots):
        # Set ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_MPIN, ANGEL_TOTP in environment
        session = AngelSession.from_env()
        session.login()

    The session object is thread-safe for reads; login/refresh should be
    called from a single scheduler thread.
    """

    def __init__(
        self,
        api_key:     str,
        client_code: str,
        mpin:        str,
        totp_secret: str,
        public_ip:   str = "",
        local_ip:    str = "",
        mac_address: str = "",
    ):
        """
        Args:
            api_key     : SmartAPI private key from the developer portal
            client_code : Your AngelOne user ID (e.g. "A12345")
            mpin        : Your trading PIN / MPIN
            totp_secret : Base32 secret from your TOTP authenticator QR scan
            public_ip   : Whitelisted static public IP for order APIs
            local_ip    : Machine's local IP (auto-detected if empty)
            mac_address : Machine's MAC (auto-detected if empty)
        """
        self._api_key     = api_key
        self._client_code = client_code
        self._mpin        = mpin
        self._totp_secret = totp_secret
        self._public_ip   = public_ip
        self._local_ip    = local_ip
        self._mac_address = mac_address
        self.tokens: Optional[SessionTokens] = None
        self._token_lock  = threading.Lock()  # guards self.tokens read/write

    # ── Factory: load credentials from environment variables ─────────────────
    @classmethod
    def from_env(cls) -> "AngelSession":
        """
        Construct an AngelSession from environment variables.
        Required env vars:
            ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_MPIN, ANGEL_TOTP_SECRET
        Optional:
            ANGEL_PUBLIC_IP, ANGEL_LOCAL_IP, ANGEL_MAC_ADDRESS

        Raises:
            EnvironmentError if any required variable is missing.
        """
        required = ["ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_MPIN", "ANGEL_TOTP_SECRET"]
        missing  = [k for k in required if not os.getenv(k)]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
        return cls(
            api_key     = os.environ["ANGEL_API_KEY"],
            client_code = os.environ["ANGEL_CLIENT_CODE"],
            mpin        = os.environ["ANGEL_MPIN"],
            totp_secret = os.environ["ANGEL_TOTP_SECRET"],
            public_ip   = os.getenv("ANGEL_PUBLIC_IP", ""),
            local_ip    = os.getenv("ANGEL_LOCAL_IP",  ""),
            mac_address = os.getenv("ANGEL_MAC_ADDRESS", ""),
        )

    # ── Internal helper: POST request ─────────────────────────────────────────
    def _post(self, endpoint_key: str, payload: dict, auth: bool = True) -> dict:
        """
        Generic authenticated POST helper.

        Args:
            endpoint_key : key in config.ENDPOINTS dict
            payload      : JSON body
            auth         : if True, include Authorization header (default True)
        Returns:
            Parsed JSON response dict
        Raises:
            AngelOneAPIError on non-200 HTTP or API error
            requests.RequestException on network failure
        """
        url = BASE_URL + ENDPOINTS[endpoint_key]
        with self._token_lock:
            tok = self.tokens
        if auth and tok:
            headers = tok.headers
        else:
            headers = build_headers(
                jwt_token   = "",
                api_key     = self._api_key,
                public_ip   = self._public_ip,
                local_ip    = self._local_ip,
                mac_address = self._mac_address,
            )
            headers.pop("Authorization", None)
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            raise AngelOneAPIError(f"HTTP {e.response.status_code} on {endpoint_key}") from e
        except requests.RequestException as e:
            raise AngelOneAPIError(f"Network error on {endpoint_key}: {e}") from e

    def _get(self, endpoint_key: str, params: dict = None) -> dict:
        """
        Generic authenticated GET helper.

        Args:
            endpoint_key : key in config.ENDPOINTS dict
            params       : optional query string params
        Returns:
            Parsed JSON response dict
        """
        with self._token_lock:
            tok = self.tokens
        if not tok:
            raise AngelOneAPIError("Session not initialised — call login() first")
        url = BASE_URL + ENDPOINTS[endpoint_key]
        try:
            resp = requests.get(
                url, params=params, headers=tok.headers, timeout=REQUEST_TIMEOUT
            )
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            raise AngelOneAPIError(f"HTTP {e.response.status_code} on {endpoint_key}") from e
        except requests.RequestException as e:
            raise AngelOneAPIError(f"Network error on {endpoint_key}: {e}") from e

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_totp(self) -> str:
        """
        Generate the current 6-digit TOTP code from the stored secret.

        Returns:
            6-digit TOTP string (valid for ~30 seconds)
        Raises:
            ValueError if the TOTP secret is invalid
        """
        try:
            return pyotp.TOTP(self._totp_secret).now()
        except Exception as e:
            raise ValueError(f"Invalid TOTP secret: {e}") from e

    def login(self) -> SessionTokens:
        """
        Authenticate with AngelOne and create a new session.

        Generates a fresh TOTP on each call, so it is safe to retry.
        Stores tokens in self.tokens and also returns them.

        Returns:
            SessionTokens with jwt_token, refresh_token, feed_token
        Raises:
            AngelOneAPIError on authentication failure
            ValueError if TOTP secret is invalid

        Example:
            session = AngelSession(api_key=..., ...)
            tokens = session.login()
            print(tokens.jwt_token)
        """
        totp = self.generate_totp()
        payload = {
            "clientcode": self._client_code,
            "password":   self._mpin,
            "totp":       totp,
        }
        _log.info("Logging in as client: %s", self._client_code)
        raw = self._post("login", payload, auth=False)
        data = validate_response(raw, context="login")

        # feedToken is inside the data dict (AngelOne API v1 login response)
        feed_token = data.get("feedToken", "")

        new_tokens = SessionTokens(
            jwt_token     = data["jwtToken"].replace("Bearer ", "").strip(),
            refresh_token = data["refreshToken"],
            feed_token    = feed_token,
            client_code   = self._client_code,
            api_key       = self._api_key,
            public_ip     = self._public_ip,
            local_ip      = self._local_ip,
            mac_address   = self._mac_address,
        )
        with self._token_lock:
            self.tokens = new_tokens
        _log.info("Login successful. Session created at %s IST",
                  self.tokens.created_at.strftime("%H:%M:%S"))
        return self.tokens

    def refresh(self) -> SessionTokens:
        """
        Renew the JWT token using the existing refresh token.
        Avoids re-login and TOTP regeneration for session continuity.

        Call this at ~23:30 IST daily to ensure uninterrupted bot operation.

        Returns:
            Updated SessionTokens
        Raises:
            AngelOneAPIError if refresh_token is expired (must call login() again)
        """
        with self._token_lock:
            tok = self.tokens
        if not tok:
            _log.warning("No active session — calling login() instead of refresh()")
            return self.login()

        _log.info("Refreshing session tokens...")
        payload = {"refreshToken": tok.refresh_token}
        raw  = self._post("refresh_token", payload)
        data = validate_response(raw, context="refresh_token")

        # Build a new immutable SessionTokens (atomic swap under lock)
        refreshed = SessionTokens(
            jwt_token     = data["jwtToken"].replace("Bearer ", "").strip(),
            refresh_token = data["refreshToken"],
            feed_token    = tok.feed_token,
            client_code   = tok.client_code,
            api_key       = tok.api_key,
            public_ip     = tok.public_ip,
            local_ip      = tok.local_ip,
            mac_address   = tok.mac_address,
            created_at    = datetime.now(IST),
        )
        with self._token_lock:
            self.tokens = refreshed
        _log.info("Token refreshed successfully")
        return refreshed

    def refresh_if_needed(self, warn_minutes: int = 60) -> bool:
        """
        Proactively refresh if within `warn_minutes` of midnight expiry.
        Safe to call from a scheduler every 30 minutes.

        Returns:
            True if a refresh was performed, False if not needed
        """
        with self._token_lock:
            tok = self.tokens
        if tok and tok.is_near_expiry(warn_minutes):
            _log.info("Session near expiry — refreshing proactively")
            self.refresh()
            return True
        return False

    def get_profile(self) -> dict:
        """
        Fetch the authenticated user's profile.

        Returns dict with keys:
            clientcode, name, email, mobileno, exchanges, products,
            lastlogintime, brokerid

        Returns:
            User profile dict
        Raises:
            AngelOneAPIError if session is invalid
        """
        with self._token_lock:
            tok = self.tokens
        if not tok:
            raise AngelOneAPIError("Session not initialised — call login() first")
        raw  = self._get("profile", params={"refreshToken": tok.refresh_token})
        data = validate_response(raw, context="get_profile")
        _log.info("Profile fetched for: %s (%s)", data.get("name"), data.get("clientcode"))
        return data

    def logout(self) -> bool:
        """
        Terminate the current session on AngelOne's servers.
        Always call this when the bot shuts down gracefully.

        Returns:
            True on successful logout
        Raises:
            AngelOneAPIError on failure
        """
        with self._token_lock:
            tok = self.tokens
        if not tok:
            _log.warning("logout() called but no active session exists — skipping")
            return False
        payload = {"clientcode": tok.client_code}
        raw = self._post("logout", payload)
        validate_response(raw, context="logout")
        _log.info("Logged out successfully")
        with self._token_lock:
            self.tokens = None
        # Clear sensitive credentials from memory
        self._clear_credentials()
        return True

    def _clear_credentials(self) -> None:
        """Overwrite sensitive credential strings in memory with zeros."""
        for attr in ("_mpin", "_totp_secret"):
            val = getattr(self, attr, None)
            if isinstance(val, str) and val:
                # Best-effort: overwrite the string's buffer (CPython-specific)
                try:
                    buf = ctypes.cast(
                        id(val) + sys.getsizeof("") - 1,       # compact str header
                        ctypes.POINTER(ctypes.c_char * len(val))
                    )
                    ctypes.memset(buf, 0, len(val))
                except Exception:
                    pass
                setattr(self, attr, "")

    def __enter__(self) -> "AngelSession":
        """Support `with AngelSession(...) as s:` context manager."""
        self.login()
        return self

    def __exit__(self, *_) -> None:
        """Auto-logout when exiting a `with` block."""
        try:
            self.logout()
        except Exception:
            pass  # Best-effort logout; don't raise in __exit__
