"""
screener/universe.py — Universe loading for the stock screener
==============================================================
Supports explicit watchlists plus the `nifty50` shorthand.
"""

from __future__ import annotations

import csv
from pathlib import Path

import requests

from utils import get_logger

_log = get_logger(__name__)

NIFTY50_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
FALLBACK_CSV = Path(__file__).resolve().parent / "data" / "nifty50.csv"


def _read_csv_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [row for row in reader]


def _download_nifty50() -> list[dict]:
    response = requests.get(NIFTY50_URL, timeout=15)
    response.raise_for_status()
    lines = response.text.splitlines()
    reader = csv.DictReader(lines)
    rows = []
    for row in reader:
        symbol = str(row.get("Symbol") or row.get("symbol") or "").strip().upper()
        if symbol:
            rows.append({"exchange": "NSE", "symbol": symbol})
    return rows


def _parse_explicit_symbols(items: list, default_exchange: str = "NSE") -> list[dict]:
    symbols: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            symbol = str(item.get("symbol", "")).strip().upper()
            exchange = str(item.get("exchange", default_exchange)).strip().upper()
        else:
            text = str(item).strip().upper()
            if ":" in text:
                exchange, symbol = text.split(":", 1)
            else:
                exchange, symbol = default_exchange, text
        if symbol:
            symbols.append({"exchange": exchange, "symbol": symbol})
    return symbols


def load_universe(watchlist, default_exchange: str = "NSE") -> list[dict]:
    if isinstance(watchlist, str):
        watchlist = [watchlist]

    if not isinstance(watchlist, list):
        return []

    symbols: list[dict] = []
    for item in watchlist:
        if isinstance(item, str) and item.strip().lower() == "nifty50":
            try:
                downloaded = _download_nifty50()
                _log.info("Loaded %d Nifty 50 symbols from official constituent file", len(downloaded))
                symbols.extend(downloaded)
            except Exception as exc:
                _log.warning("Nifty 50 download failed (%s) - using fallback snapshot", exc)
                rows = _read_csv_rows(FALLBACK_CSV)
                symbols.extend(
                    {"exchange": row.get("exchange", "NSE"), "symbol": row.get("symbol", "").upper()}
                    for row in rows
                    if row.get("symbol")
                )
            continue

        symbols.extend(_parse_explicit_symbols([item], default_exchange=default_exchange))

    deduped: dict[tuple[str, str], dict] = {}
    for symbol in symbols:
        key = (symbol["exchange"].upper(), symbol["symbol"].upper())
        deduped[key] = {"exchange": key[0], "symbol": key[1]}
    return list(deduped.values())
