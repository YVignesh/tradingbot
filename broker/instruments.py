"""
instruments.py — AngelOne SmartAPI · Instruments Master
========================================================
Downloads, caches, and provides fast O(1) lookups for all
tradeable instruments (symbol → token and token → symbol).

The instruments master JSON refreshes before each market open.
Call InstrumentMaster.refresh() at bot startup and again each morning.

Usage:
    master = InstrumentMaster()
    master.load()                          # download + build lookup maps

    token = master.get_token("NSE", "SBIN-EQ")          # "3045"
    info  = master.get_info("NSE", "SBIN-EQ")            # full instrument dict
    nse_fo = master.get_by_exchange("NFO")               # list of all NFO instruments
    token = master.search("RELIANCE")                    # fuzzy name search

Dependencies:
    pip install requests
"""

import json
import time
import tempfile
import threading
import requests
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from broker.constants import INSTRUMENTS_MASTER_URL, REQUEST_TIMEOUT
from utils import get_logger, AngelOneAPIError

_log = get_logger(__name__)

# Use the OS temp directory so the cache works on Windows, macOS, and Linux
DEFAULT_CACHE_PATH = Path(tempfile.gettempdir()) / "angelone_instruments.json"
CACHE_MAX_AGE_HOURS = 12  # refresh cache if older than this


class InstrumentMaster:
    """
    Download and query the AngelOne instruments master.

    The master file is a JSON array of instrument records.  This class
    builds two in-memory indices for O(1) lookups:
      • (exchange, symbol) → instrument dict
      • (exchange, token)  → instrument dict

    Attributes:
        instruments : raw list of all instrument dicts
        _by_symbol  : {(exchange, symbol): instrument}
        _by_token   : {(exchange, token):  instrument}

    Thread Safety:
        All reads are thread-safe after initial load.
        Call refresh() from a single thread only.
    """

    def __init__(self, cache_path: Path = DEFAULT_CACHE_PATH):
        """
        Args:
            cache_path : local file path to cache the downloaded JSON.
                         Set to None to disable caching (downloads every time).
        """
        self.cache_path  = cache_path
        self.instruments: List[dict] = []
        self._by_symbol: Dict[Tuple[str, str], dict] = {}
        self._by_token:  Dict[Tuple[str, str], dict] = {}
        self._lock       = threading.RLock()
        self._loaded     = False

    # ── Download & Load ───────────────────────────────────────────────────────

    def load(self, force_download: bool = False) -> int:
        """
        Load the instruments master, using a local cache when possible.

        Download order:
          1. If cache exists and is fresh (< CACHE_MAX_AGE_HOURS old), use it.
          2. Otherwise, download from AngelOne and save to cache.

        Args:
            force_download : bypass cache and always download fresh data
        Returns:
            Number of instruments loaded
        Raises:
            AngelOneAPIError on download or parse failure
        """
        with self._lock:
            if not force_download and self._use_cache():
                return len(self.instruments)
            self._download()
            self._build_index()
            self._save_cache()
            self._loaded = True
            _log.info("Instruments master loaded: %d instruments", len(self.instruments))
            return len(self.instruments)

    def refresh(self) -> int:
        """
        Force a fresh download from AngelOne, regardless of cache age.
        Call this at bot startup and every morning before market open.

        Returns:
            Number of instruments loaded
        """
        return self.load(force_download=True)

    def _use_cache(self) -> bool:
        """
        Try to load from a local cache file.
        Returns True if cache was loaded successfully and is fresh.
        """
        if not self.cache_path or not self.cache_path.exists():
            return False
        age_hours = (time.time() - self.cache_path.stat().st_mtime) / 3600
        if age_hours > CACHE_MAX_AGE_HOURS:
            _log.debug("Cache is %.1f hours old — will re-download", age_hours)
            return False
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.instruments = data
            self._build_index()
            self._loaded = True
            _log.info("Loaded instruments from cache (%d records)", len(self.instruments))
            return True
        except Exception as e:
            _log.warning("Cache load failed (%s) — will download fresh", e)
            return False

    def _download(self) -> None:
        """Download the instruments JSON from AngelOne's CDN."""
        _log.info("Downloading instruments master from AngelOne...")
        try:
            resp = requests.get(INSTRUMENTS_MASTER_URL, timeout=30)
            resp.raise_for_status()
            self.instruments = resp.json()
        except requests.RequestException as e:
            raise AngelOneAPIError(f"Instruments master download failed: {e}") from e
        except ValueError as e:
            raise AngelOneAPIError(f"Instruments master JSON parse error: {e}") from e

    def _build_index(self) -> None:
        """Build O(1) lookup dictionaries from the raw instrument list."""
        self._by_symbol = {}
        self._by_token  = {}
        for inst in self.instruments:
            exchange = inst.get("exch_seg", "").upper()
            symbol   = inst.get("symbol", "").upper()
            token    = inst.get("token", "")
            if exchange and symbol:
                self._by_symbol[(exchange, symbol)] = inst
            if exchange and token:
                self._by_token[(exchange, token)] = inst

    def _save_cache(self) -> None:
        """Persist the downloaded data to the local cache file."""
        if not self.cache_path:
            return
        try:
            self.cache_path.write_text(
                json.dumps(self.instruments), encoding="utf-8"
            )
            _log.debug("Instruments master cached to %s", self.cache_path)
        except OSError as e:
            _log.warning("Could not save instruments cache: %s", e)

    def _check_loaded(self) -> None:
        if not self._loaded:
            raise RuntimeError(
                "InstrumentMaster not loaded — call .load() or .refresh() first"
            )

    # ── Lookup API ────────────────────────────────────────────────────────────

    def get_token(self, exchange: str, symbol: str) -> Optional[str]:
        """
        Get the symbol token for an exchange + trading symbol pair.

        Args:
            exchange : e.g. "NSE", "BSE", "NFO"
            symbol   : e.g. "SBIN-EQ", "RELIANCE-EQ", "NIFTY24JANFUT"
        Returns:
            Token string (e.g. "3045") or None if not found

        Example:
            token = master.get_token("NSE", "SBIN-EQ")  # "3045"
        """
        self._check_loaded()
        inst = self._by_symbol.get((exchange.upper(), symbol.upper()))
        return inst["token"] if inst else None

    def get_token_strict(self, exchange: str, symbol: str) -> str:
        """
        Like get_token() but raises KeyError if symbol is not found.
        Use when you want to fail fast on a bad symbol.

        Raises:
            KeyError if (exchange, symbol) not in master
        """
        token = self.get_token(exchange, symbol)
        if token is None:
            raise KeyError(f"Symbol not found in instruments master: {exchange}:{symbol}")
        return token

    def get_symbol(self, exchange: str, token: str) -> Optional[str]:
        """
        Reverse lookup: get trading symbol from exchange + token.

        Args:
            exchange : e.g. "NSE"
            token    : numeric token string e.g. "3045"
        Returns:
            Symbol string or None if not found

        Example:
            symbol = master.get_symbol("NSE", "3045")  # "SBIN-EQ"
        """
        self._check_loaded()
        inst = self._by_token.get((exchange.upper(), str(token)))
        return inst["symbol"] if inst else None

    def get_info(self, exchange: str, symbol: str) -> Optional[dict]:
        """
        Get the full instrument info dict for a symbol.

        Returned dict contains:
            token, symbol, name, expiry, strike, lotsize,
            instrumenttype, exch_seg, tick_size

        Args:
            exchange : e.g. "NSE"
            symbol   : e.g. "SBIN-EQ"
        Returns:
            Full instrument dict, or None if not found

        Example:
            info = master.get_info("NSE", "SBIN-EQ")
            lot_size = int(info["lotsize"])
        """
        self._check_loaded()
        return self._by_symbol.get((exchange.upper(), symbol.upper()))

    def get_lot_size(self, exchange: str, symbol: str) -> int:
        """
        Get the lot size for a derivative or equity instrument.
        Equity cash market instruments have lot size = 1.

        Args:
            exchange : e.g. "NFO"
            symbol   : e.g. "NIFTY24JANFUT"
        Returns:
            Lot size as integer (default 1 if not found)
        """
        info = self.get_info(exchange, symbol)
        if not info:
            return 1
        return int(info.get("lotsize", 1))

    def get_tick_size(self, exchange: str, symbol: str) -> float:
        """
        Get the minimum price tick (in paise as string) for a symbol.

        Returns:
            Tick size in rupees as float (e.g. 0.05 for NSE equity)
        """
        info = self.get_info(exchange, symbol)
        if not info:
            return 0.05
        return float(info.get("tick_size", 5)) / 100   # stored in paise

    def get_by_exchange(self, exchange: str) -> List[dict]:
        """
        Get all instruments for a given exchange segment.

        Args:
            exchange : e.g. "NSE", "NFO", "MCX"
        Returns:
            List of instrument dicts

        Example:
            nfo_instruments = master.get_by_exchange("NFO")
        """
        self._check_loaded()
        exch = exchange.upper()
        return [i for i in self.instruments if i.get("exch_seg", "").upper() == exch]

    def search(self, name_fragment: str, exchange: str = None, limit: int = 10) -> List[dict]:
        """
        Search instruments by name fragment (case-insensitive substring match).
        Useful for interactive symbol discovery.

        Args:
            name_fragment : partial name string, e.g. "RELIANCE", "NIFTY"
            exchange      : optional exchange filter (e.g. "NSE")
            limit         : max number of results to return
        Returns:
            List of matching instrument dicts (up to `limit`)

        Example:
            results = master.search("SBIN", exchange="NSE")
            for r in results:
                print(r["symbol"], r["token"])
        """
        self._check_loaded()
        frag  = name_fragment.upper()
        exch  = exchange.upper() if exchange else None
        found = []
        for inst in self.instruments:
            if exch and inst.get("exch_seg", "").upper() != exch:
                continue
            if frag in inst.get("name", "").upper() or frag in inst.get("symbol", "").upper():
                found.append(inst)
                if len(found) >= limit:
                    break
        return found

    def build_token_map(self, symbols: List[Tuple[str, str]]) -> Dict[str, str]:
        """
        Build a {exchange:symbol -> token} map for a list of instruments.
        Useful for initialising a watchlist.

        Args:
            symbols : list of (exchange, symbol) tuples
                      e.g. [("NSE", "SBIN-EQ"), ("NSE", "RELIANCE-EQ")]
        Returns:
            {"NSE:SBIN-EQ": "3045", "NSE:RELIANCE-EQ": "2885", ...}
            Missing symbols are silently skipped (logged as warnings).

        Example:
            watchlist = [("NSE", "SBIN-EQ"), ("NSE", "TCS-EQ")]
            token_map = master.build_token_map(watchlist)
        """
        self._check_loaded()
        result = {}
        for exchange, symbol in symbols:
            token = self.get_token(exchange, symbol)
            if token:
                result[f"{exchange.upper()}:{symbol.upper()}"] = token
            else:
                _log.warning("Symbol not found in instruments master: %s:%s", exchange, symbol)
        return result

    def __len__(self) -> int:
        return len(self.instruments)

    def __repr__(self) -> str:
        return f"<InstrumentMaster loaded={self._loaded} count={len(self.instruments)}>"
