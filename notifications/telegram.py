"""
notifications/telegram.py — Optional Telegram alerts
====================================================
Sends fills, halts, and closed-trade summaries to a chat.
"""

from __future__ import annotations

import os
from typing import Optional

import requests

from utils import get_logger


class TelegramNotifier:
    def __init__(
        self,
        *,
        enabled: bool = False,
        bot_token: str = "",
        chat_id: str = "",
        timeout_sec: int = 10,
    ):
        self.enabled = enabled and bool(bot_token and chat_id)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_sec = timeout_sec
        self.log = get_logger(__name__)

    @classmethod
    def from_config(cls, config: dict) -> "TelegramNotifier":
        cfg = config.get("notifications", {}).get("telegram", {})
        return cls(
            enabled=bool(cfg.get("enabled", False)),
            bot_token=str(cfg.get("bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")),
            chat_id=str(cfg.get("chat_id") or os.getenv("TELEGRAM_CHAT_ID", "")),
            timeout_sec=int(cfg.get("timeout_sec", 10)),
        )

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            self.log.warning("Telegram notify failed: %s", exc)
            return False

    def notify_fill(self, fill: dict) -> bool:
        text = (
            f"*Fill* `{fill.get('strategy', '')}`\n"
            f"{fill.get('symbol', '')} {fill.get('transaction_type', '')} "
            f"{int(fill.get('fill_qty', 0) or 0)} @ Rs{float(fill.get('fill_price', 0.0) or 0.0):.2f}\n"
            f"Intent: `{fill.get('intent', '')}` via {fill.get('source', '')}"
        )
        return self.send(text)

    def notify_trade(self, trade: dict) -> bool:
        pnl = float(trade.get("net_pnl", 0.0) or 0.0)
        sign = "+" if pnl >= 0 else ""
        text = (
            f"*Closed trade* `{trade.get('strategy', '')}`\n"
            f"{trade.get('symbol', '')} {trade.get('direction', '')} x{trade.get('qty', 0)}\n"
            f"Entry Rs{float(trade.get('entry_price', 0.0) or 0.0):.2f} -> "
            f"Exit Rs{float(trade.get('exit_price', 0.0) or 0.0):.2f}\n"
            f"Net P&L: {sign}Rs{pnl:.2f}"
        )
        return self.send(text)

    def notify_halt(self, message: str) -> bool:
        return self.send(f"*Bot alert*\n{message}")

    def notify_daily_summary(self, summary: str) -> bool:
        return self.send(f"*Daily summary*\n{summary}")
