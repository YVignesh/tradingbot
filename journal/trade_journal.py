"""
journal/trade_journal.py — SQLite trade journal
===============================================
Persists fills and completed trades for later analysis across symbols.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from broker.charges import Segment, calculate_charges
from utils import get_logger

IST = timezone(timedelta(hours=5, minutes=30))


def _resolve_trade_segment(symbol: str, exchange: str, product: str, configured: str = "") -> str:
    if configured:
        return configured

    symbol = str(symbol).upper()
    exchange = str(exchange).upper()
    product = str(product).upper()
    looks_like_option = bool(re.search(r"(CE|PE)(?:[-_ ]|$)", symbol))

    if exchange in {"NSE", "BSE"}:
        return Segment.EQUITY_INTRADAY if product == "INTRADAY" else Segment.EQUITY_DELIVERY
    if exchange == "NFO":
        return Segment.EQUITY_OPTIONS if looks_like_option else Segment.EQUITY_FUTURES
    if exchange == "CDS":
        return Segment.CURRENCY_OPTIONS if looks_like_option else Segment.CURRENCY_FUTURES
    if exchange in {"MCX", "NCDEX"}:
        return Segment.COMMODITY_OPTIONS if looks_like_option else Segment.COMMODITY_FUTURES
    return Segment.EQUITY_INTRADAY


class TradeJournal:
    def __init__(self, path: str = "data/journal/trades.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.log = get_logger(__name__)
        self._init_db()

    def record_fill(self, fill: dict) -> None:
        recorded_at = self._fmt(fill.get("recorded_at"))
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO fills (
                    recorded_at, strategy, symbol, exchange, intent, transaction_type,
                    direction_before, direction_after, order_id, fill_qty, fill_price,
                    status, source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recorded_at,
                    fill.get("strategy", ""),
                    fill.get("symbol", ""),
                    fill.get("exchange", ""),
                    fill.get("intent", ""),
                    fill.get("transaction_type", ""),
                    fill.get("direction_before", ""),
                    fill.get("direction_after", ""),
                    fill.get("order_id", ""),
                    int(fill.get("fill_qty", 0) or 0),
                    float(fill.get("fill_price", 0.0) or 0.0),
                    fill.get("status", ""),
                    fill.get("source", ""),
                ),
            )
            conn.commit()

    def record_trade(
        self,
        trade: dict,
        *,
        product: str,
        charge_segment: str = "",
    ) -> dict:
        direction = str(trade["direction"]).upper()
        qty = int(trade["qty"])
        entry_price = float(trade["entry_price"])
        exit_price = float(trade["exit_price"])
        exchange = str(trade["exchange"]).upper()
        symbol = str(trade["symbol"]).upper()
        segment = _resolve_trade_segment(symbol, exchange, product, configured=charge_segment)

        if direction == "LONG":
            buy_price = entry_price
            sell_price = exit_price
        else:
            buy_price = exit_price
            sell_price = entry_price

        charges = calculate_charges(
            segment=segment,
            buy_price=buy_price,
            sell_price=sell_price,
            quantity=qty,
            exchange=exchange,
        )

        record = {
            "recorded_at": self._fmt(datetime.now(IST)),
            "strategy": trade.get("strategy", ""),
            "symbol": symbol,
            "exchange": exchange,
            "direction": direction,
            "entry_time": self._fmt(trade.get("entry_time")),
            "exit_time": self._fmt(trade.get("exit_time")),
            "qty": qty,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "gross_pnl": round(float(trade["gross_pnl"]), 2),
            "charges": round(charges.total_charges, 2),
            "net_pnl": round(float(trade["gross_pnl"]) - charges.total_charges, 2),
            "segment": segment,
            "recovered": 1 if trade.get("recovered") else 0,
            "mae": round(float(trade.get("mae", 0.0)), 2),
            "mfe": round(float(trade.get("mfe", 0.0)), 2),
        }

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trades (
                    recorded_at, strategy, symbol, exchange, direction, entry_time, exit_time,
                    qty, entry_price, exit_price, gross_pnl, charges, net_pnl, segment, recovered,
                    mae, mfe
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["recorded_at"],
                    record["strategy"],
                    record["symbol"],
                    record["exchange"],
                    record["direction"],
                    record["entry_time"],
                    record["exit_time"],
                    record["qty"],
                    record["entry_price"],
                    record["exit_price"],
                    record["gross_pnl"],
                    record["charges"],
                    record["net_pnl"],
                    record["segment"],
                    record["recovered"],
                    record["mae"],
                    record["mfe"],
                ),
            )
            conn.commit()
        return record

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    transaction_type TEXT NOT NULL,
                    direction_before TEXT NOT NULL,
                    direction_after TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    fill_qty INTEGER NOT NULL,
                    fill_price REAL NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    recorded_at TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    gross_pnl REAL NOT NULL,
                    charges REAL NOT NULL,
                    net_pnl REAL NOT NULL,
                    segment TEXT NOT NULL,
                    recovered INTEGER NOT NULL DEFAULT 0,
                    mae REAL NOT NULL DEFAULT 0.0,
                    mfe REAL NOT NULL DEFAULT 0.0
                );
                """
            )
            # Migrate existing databases: add MAE/MFE columns if missing
            cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
            if "mae" not in cols:
                conn.execute("ALTER TABLE trades ADD COLUMN mae REAL NOT NULL DEFAULT 0.0")
            if "mfe" not in cols:
                conn.execute("ALTER TABLE trades ADD COLUMN mfe REAL NOT NULL DEFAULT 0.0")
            conn.commit()

    def _fmt(self, value: Optional[datetime | str]) -> str:
        if isinstance(value, datetime):
            return value.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
