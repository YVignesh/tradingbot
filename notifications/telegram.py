"""
notifications/telegram.py — Telegram alerts + command handler
=============================================================
Sends fills, halts, and closed-trade summaries to a chat.
Also provides a bidirectional command handler for remote bot control.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import requests

from utils import get_logger

IST = timezone(timedelta(hours=5, minutes=30))


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


# ──────────────────────────────────────────────────────────────────────────────
# BIDIRECTIONAL COMMAND HANDLER
# ──────────────────────────────────────────────────────────────────────────────

class TelegramCommandHandler:
    """
    Polls Telegram for incoming commands and executes them against the bot.

    Runs in a daemon thread. Only processes messages from the authorized chat_id.

    Supported commands:
        /status     — positions, P&L, risk state
        /positions  — detailed open positions
        /trades     — today's completed trades count + P&L
        /risk       — risk manager state
        /pause      — stop new entries (keep positions)
        /resume     — resume trading
        /squareoff  — force-close ALL positions (requires /confirm within 60s)
        /kill       — graceful shutdown after squareoff (requires /confirm within 60s)
        /confirm    — confirm a pending destructive command
        /cancel     — cancel a pending destructive command
        /help       — list commands
    """

    def __init__(
        self,
        notifier: TelegramNotifier,
        *,
        poll_interval_sec: float = 3.0,
    ):
        self.notifier = notifier
        self.log = get_logger("telegram.commands")
        self._poll_interval = poll_interval_sec
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_update_id = 0

        # Shared state — set by bot_runtime.py via set_bot_context()
        self._bot_stop_event: Optional[threading.Event] = None
        self._runtimes: list = []
        self._risk_mgr: Any = None
        self._session: Any = None
        self._config: dict = {}
        self._paused = threading.Event()  # when SET, bot is paused
        self._squareoff_fn: Optional[Callable] = None

        # Pending confirmation for destructive commands (guarded by _action_lock)
        self._action_lock = threading.Lock()
        self._pending_action: Optional[str] = None  # "squareoff" or "kill"
        self._pending_expires: float = 0.0

    def set_bot_context(
        self,
        *,
        stop_event: threading.Event,
        runtimes: list,
        risk_mgr: Any,
        session: Any,
        config: dict,
        squareoff_fn: Optional[Callable] = None,
    ) -> None:
        """Wire shared bot state. Called from bot_runtime.py after setup."""
        self._bot_stop_event = stop_event
        self._runtimes = runtimes
        self._risk_mgr = risk_mgr
        self._session = session
        self._config = config
        self._squareoff_fn = squareoff_fn

    @property
    def is_paused(self) -> bool:
        """Check if trading is paused via /pause command."""
        return self._paused.is_set()

    def start(self) -> None:
        """Start the command polling thread."""
        if not self.notifier.enabled:
            self.log.info("Telegram commands disabled (notifier not enabled)")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="TelegramCommands",
        )
        self._thread.start()
        self.log.info("Telegram command handler started (poll every %.1fs)", self._poll_interval)

    def stop(self) -> None:
        """Stop the command polling thread."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def _poll_loop(self) -> None:
        """Main polling loop — runs in daemon thread."""
        while not self._stop_event.is_set():
            try:
                updates = self._get_updates()
                for update in updates:
                    self._handle_update(update)
            except Exception as exc:
                self.log.warning("Telegram poll error: %s", exc)
            self._stop_event.wait(timeout=self._poll_interval)

    def _get_updates(self) -> list[dict]:
        """Fetch new messages from Telegram using long polling."""
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{self.notifier.bot_token}/getUpdates",
                params={
                    "offset": self._last_update_id + 1,
                    "timeout": 2,
                    "allowed_updates": '["message"]',
                },
                timeout=self.notifier.timeout_sec + 3,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                return []
            results = data.get("result", [])
            if results:
                self._last_update_id = results[-1]["update_id"]
            return results
        except requests.RequestException:
            return []

    def _handle_update(self, update: dict) -> None:
        """Process a single Telegram update."""
        msg = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = str(msg.get("text", "")).strip()

        if not text or not chat_id:
            return

        # Auth: only process from the configured chat
        if chat_id != self.notifier.chat_id:
            self.log.warning("Ignoring message from unauthorized chat_id=%s", chat_id)
            return

        self.log.info("Received command: %s", text)

        # Route commands
        cmd = text.split()[0].lower()
        handlers = {
            "/status": self._cmd_status,
            "/positions": self._cmd_positions,
            "/trades": self._cmd_trades,
            "/risk": self._cmd_risk,
            "/pause": self._cmd_pause,
            "/resume": self._cmd_resume,
            "/squareoff": self._cmd_squareoff,
            "/kill": self._cmd_kill,
            "/confirm": self._cmd_confirm,
            "/cancel": self._cmd_cancel_pending,
            "/help": self._cmd_help,
        }

        handler = handlers.get(cmd)
        if handler is not None:
            try:
                handler()
            except Exception as exc:
                self.log.error("Command %s failed: %s", cmd, exc)
                self._reply(f"Command failed: {exc}")
        elif text.startswith("/"):
            self._reply(f"Unknown command: `{cmd}`\nUse /help for available commands.")

    def _reply(self, text: str) -> None:
        """Send a reply back to the authorized chat."""
        self.notifier.send(text)

    # ── Read-only commands ────────────────────────────────────────────────────

    def _cmd_help(self) -> None:
        self._reply(
            "*Available Commands*\n"
            "📊 /status — overview\n"
            "📈 /positions — open positions\n"
            "📋 /trades — today's trades\n"
            "🛡 /risk — risk state\n"
            "⏸ /pause — stop new entries\n"
            "▶️ /resume — resume trading\n"
            "🔴 /squareoff — close ALL positions\n"
            "💀 /kill — shutdown bot\n"
            "✅ /confirm — confirm pending action\n"
            "❌ /cancel — cancel pending action"
        )

    def _cmd_status(self) -> None:
        now = datetime.now(IST).strftime("%H:%M:%S IST")
        dry_run = self._config.get("bot", {}).get("dry_run", True)
        mode = "DRY RUN" if dry_run else "LIVE"
        paused = "PAUSED" if self.is_paused else "ACTIVE"

        # Positions summary
        positions = []
        for rt in list(self._runtimes):
            s = rt.strategy
            if s.in_position:
                positions.append(
                    f"  {s.symbol} {s.direction} x{s.entry_qty} @ ₹{s.entry_price:.2f}"
                )

        # Risk summary
        risk = self._risk_mgr.status() if self._risk_mgr else {}

        text = (
            f"*Bot Status* ({now})\n"
            f"Mode: `{mode}` | State: `{paused}`\n"
            f"Strategies: {len(self._runtimes)}\n"
            f"Open positions: {len(positions)}\n"
        )
        if positions:
            text += "\n".join(positions) + "\n"
        text += (
            f"\nDay P&L: ₹{risk.get('daily_pnl', 0):.2f}\n"
            f"Trades: {risk.get('trades_today', 0)}/{self._risk_mgr.max_trades_per_day if self._risk_mgr else '?'}\n"
            f"Equity: ₹{risk.get('current_equity', 0):.0f}"
        )
        if risk.get("halted"):
            text += f"\n⚠️ *HALTED*: {risk.get('halt_reason', '')}"

        self._reply(text)

    def _cmd_positions(self) -> None:
        positions = []
        for rt in list(self._runtimes):
            s = rt.strategy
            if s.in_position:
                state = s.get_state()
                tsl = ""
                if state.get("tsl_sl", 0) > 0:
                    tsl = f" TSL=₹{state['tsl_sl']:.2f}"
                pnl = state.get("unrealised_pnl", 0)
                sign = "+" if pnl >= 0 else ""
                positions.append(
                    f"*{s.symbol}* {s.direction} x{s.entry_qty}\n"
                    f"  Entry: ₹{s.entry_price:.2f} | LTP: ₹{state.get('ltp', 0):.2f}\n"
                    f"  P&L: {sign}₹{pnl:.2f}{tsl}\n"
                    f"  SL order: `{rt.sl_order_id or 'none'}`"
                )

        if positions:
            self._reply("*Open Positions*\n\n" + "\n\n".join(positions))
        else:
            self._reply("No open positions.")

    def _cmd_trades(self) -> None:
        risk = self._risk_mgr.status() if self._risk_mgr else {}
        text = (
            f"*Today's Trading*\n"
            f"Trades: {risk.get('trades_today', 0)}\n"
            f"Day P&L: ₹{risk.get('daily_pnl', 0):.2f}\n"
            f"Consecutive losses: {risk.get('consecutive_losses', 0)}\n"
            f"Cumulative P&L: ₹{risk.get('cumulative_pnl', 0):.2f}\n"
            f"Drawdown: {risk.get('drawdown_pct', 0):.1f}%"
        )
        self._reply(text)

    def _cmd_risk(self) -> None:
        if not self._risk_mgr:
            self._reply("Risk manager not available.")
            return
        risk = self._risk_mgr.status()
        guards = []
        for rt in list(self._runtimes):
            if rt.execution.is_circuit_open():
                guards.append(f"  {rt.strategy.symbol}: {rt.execution.circuit_reason()}")

        text = (
            f"*Risk State*\n"
            f"Capital: ₹{self._risk_mgr.capital:,.0f}\n"
            f"Equity: ₹{risk.get('current_equity', 0):,.0f}\n"
            f"Day P&L: ₹{risk.get('daily_pnl', 0):.2f}\n"
            f"Loss limit: ₹{self._risk_mgr.daily_loss_limit:,.0f} "
            f"(used {risk.get('loss_limit_used_pct', 0):.0f}%)\n"
            f"Max drawdown: {self._risk_mgr.max_drawdown_pct:.1f}%\n"
            f"Drawdown: {risk.get('drawdown_pct', 0):.1f}%\n"
            f"Halted: {'YES — ' + risk.get('halt_reason', '') if risk.get('halted') else 'No'}"
        )
        if guards:
            text += "\n\n*Circuit breakers:*\n" + "\n".join(guards)
        self._reply(text)

    # ── Control commands ──────────────────────────────────────────────────────

    def _cmd_pause(self) -> None:
        if self.is_paused:
            self._reply("Already paused.")
            return
        self._paused.set()
        self.log.warning("Bot PAUSED via Telegram command")
        self._reply("⏸ *Bot paused* — no new entries will be taken.\nExisting positions are kept.\nUse /resume to resume.")

    def _cmd_resume(self) -> None:
        if not self.is_paused:
            self._reply("Already running.")
            return
        self._paused.clear()
        self.log.warning("Bot RESUMED via Telegram command")
        self._reply("▶️ *Bot resumed* — trading is active again.")

    def _cmd_squareoff(self) -> None:
        open_count = sum(1 for rt in list(self._runtimes) if rt.strategy.in_position)
        if open_count == 0:
            self._reply("No open positions to close.")
            return
        with self._action_lock:
            self._pending_action = "squareoff"
            self._pending_expires = time.monotonic() + 60.0
        self._reply(
            f"🔴 *Squareoff {open_count} position(s)?*\n"
            f"This will close ALL open positions immediately.\n\n"
            f"Send /confirm within 60s to proceed.\n"
            f"Send /cancel to abort."
        )

    def _cmd_kill(self) -> None:
        with self._action_lock:
            self._pending_action = "kill"
            self._pending_expires = time.monotonic() + 60.0
        open_count = sum(1 for rt in list(self._runtimes) if rt.strategy.in_position)
        msg = f"💀 *Kill bot?*\n"
        if open_count > 0:
            msg += f"Will squareoff {open_count} position(s) first, then shutdown.\n\n"
        else:
            msg += "No open positions. Bot will shutdown.\n\n"
        msg += "Send /confirm within 60s to proceed.\nSend /cancel to abort."
        self._reply(msg)

    def _cmd_confirm(self) -> None:
        with self._action_lock:
            if self._pending_action is None:
                self._reply("Nothing pending to confirm.")
                return
            if time.monotonic() > self._pending_expires:
                self._pending_action = None
                self._reply("⏰ Confirmation expired. Send the command again.")
                return
            action = self._pending_action
            self._pending_action = None
        self.log.warning("CONFIRMED action: %s via Telegram", action)

        if action == "squareoff":
            self._execute_squareoff()
        elif action == "kill":
            self._execute_kill()

    def _cmd_cancel_pending(self) -> None:
        with self._action_lock:
            if self._pending_action is None:
                self._reply("Nothing pending to cancel.")
                return
            cancelled = self._pending_action
            self._pending_action = None
        self._reply(f"❌ `{cancelled}` cancelled.")

    # ── Execution ─────────────────────────────────────────────────────────────

    def _execute_squareoff(self) -> None:
        if self._squareoff_fn is None:
            self._reply("❌ Squareoff function not wired. Cannot close positions.")
            return
        self._reply("🔴 *Squareoff in progress...*")
        results = []
        for rt in list(self._runtimes):
            if not rt.strategy.in_position:
                continue
            sym = rt.strategy.symbol
            direction = rt.strategy.direction
            try:
                from broker.market_data import get_ltp_single
                ltp = get_ltp_single(
                    self._session,
                    rt.strategy.exchange,
                    rt.strategy.symbol,
                    rt.strategy.token,
                )
                dry_run = self._config.get("bot", {}).get("dry_run", True)
                self._squareoff_fn(self._session, rt, ltp, dry_run=dry_run)
                if not rt.strategy.in_position:
                    results.append(f"✅ {sym} {direction} — closed")
                    rt.sl_order_id = None
                else:
                    results.append(f"⚠️ {sym} {direction} — still open")
            except Exception as exc:
                results.append(f"❌ {sym} {direction} — error: {exc}")

        self._reply("*Squareoff Results*\n" + "\n".join(results))

    def _execute_kill(self) -> None:
        self._reply("💀 *Killing bot...*")
        # Squareoff first
        open_count = sum(1 for rt in list(self._runtimes) if rt.strategy.in_position)
        if open_count > 0:
            self._execute_squareoff()
        # Signal shutdown
        if self._bot_stop_event is not None:
            self._bot_stop_event.set()
            self._reply("Bot shutdown signal sent. Goodbye.")
        else:
            self._reply("⚠️ Cannot signal shutdown — stop_event not wired.")
