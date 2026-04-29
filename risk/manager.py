"""
risk/manager.py — Risk Manager
================================
Single source of truth for all pre-trade risk checks and position sizing.
Called by main.py before every order. Stateful across the trading day.

Guards:
  1. Daily loss limit     — halt trading if cumulative P&L hits the limit
  2. Max trades per day   — avoid overtrading / runaway loops
  3. Consecutive losses   — pause after N losses in a row (cool-down)
  4. Position sizing      — risk-based qty capped at max_qty

Config keys consumed (from config.json "risk" section):
  capital               : total trading capital (₹)
  max_risk_pct          : max % of capital to risk per trade (e.g. 1.0 = 1%)
  sl_points             : stop-loss distance in ₹ (used for position sizing)
  max_qty               : hard cap on shares per trade
  daily_loss_limit      : halt if day's P&L drops below -this value (₹)
  max_trades_per_day    : stop taking new entries after this many trades
  max_consecutive_losses: pause after this many losses in a row
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from utils import get_logger

_log = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


class RiskManager:
    """
    Stateful per-day risk controller.

    Usage in main.py:
        risk = RiskManager(config)
        risk.sync_from_portfolio(session)   # optional: load today's P&L on restart

        # Before every BUY
        ok, reason = risk.check_can_trade()
        if not ok:
            log.warning("Trade blocked: %s", reason)
            continue
        qty = risk.position_size(ltp)

        # After every trade closes (BUY → SELL round-trip)
        risk.record_trade(pnl)
    """

    def __init__(self, config: dict):
        r = config["risk"]

        self.capital               = float(r["capital"])
        self.max_risk_pct          = float(r["max_risk_pct"])
        self.sl_points             = float(r["sl_points"])
        self.max_qty               = int(r["max_qty"])
        self.daily_loss_limit      = float(r["daily_loss_limit"])
        self.max_trades_per_day    = int(r.get("max_trades_per_day", 10))
        self.max_consecutive_losses= int(r.get("max_consecutive_losses", 3))

        self._lock = threading.Lock()
        self._reset_daily_state()

    # ── Public API ────────────────────────────────────────────────────────────

    def check_can_trade(self) -> tuple[bool, str]:
        """
        Run all pre-trade risk checks.

        Returns:
            (True,  "")          — trade is allowed
            (False, reason_str)  — trade blocked; reason_str explains why

        Call this before every BUY signal execution in main.py.
        """
        with self._lock:
            self._maybe_reset_daily()

            if self._halted:
                return False, f"Bot halted — {self._halt_reason}"

            if self.daily_loss_limit > 0 and self._daily_pnl <= -self.daily_loss_limit:
                self._halt(f"daily loss limit ₹{self.daily_loss_limit:.0f} breached")
                return False, self._halt_reason

            if self.max_trades_per_day > 0 and self._trades_today >= self.max_trades_per_day:
                return False, (
                    f"max trades/day reached ({self._trades_today}/{self.max_trades_per_day})"
                )

            if (
                self.max_consecutive_losses > 0 and
                self._consecutive_losses >= self.max_consecutive_losses
            ):
                return False, (
                    f"{self._consecutive_losses} consecutive losses — "
                    f"cool-down until next session"
                )

            return True, ""

    def position_size(self, ltp: float) -> int:
        """
        Calculate order quantity from risk parameters.

        qty = floor(capital × max_risk_pct% / sl_points), capped at max_qty.

        Example:
            capital=₹50,000  max_risk_pct=1%  sl_points=₹5
            → risk_amount = ₹500
            → qty = 500 / 5 = 100 → capped at max_qty=10
            → returns 10

        Args:
            ltp : last traded price (used for logging context only)

        Returns:
            Number of shares to trade (always ≥ 1)
        """
        risk_amount = self.capital * self.max_risk_pct / 100.0
        qty         = int(risk_amount / self.sl_points)
        qty         = max(1, min(qty, self.max_qty))
        _log.debug(
            "Position size: capital=₹%.0f  risk=%.1f%%  sl=₹%.0f  "
            "→ raw_qty=%d  capped=%d  ltp=₹%.2f",
            self.capital, self.max_risk_pct, self.sl_points,
            int(risk_amount / self.sl_points), qty, ltp,
        )
        return qty

    def record_trade(self, pnl: float) -> None:
        self.record_realized_pnl(pnl, close_round_trip=True)

    def record_realized_pnl(self, pnl: float, close_round_trip: bool = False) -> None:
        """
        Record realised P&L from one or more fills.

        `close_round_trip=True` should be used only when the position is fully
        closed and the trade count / consecutive-loss streak should advance.
        Partial exits can book realised P&L without consuming a full trade.

        Args:
            pnl : net P&L of the trade in ₹ (negative = loss)
            close_round_trip : whether this fill closed the position
        """
        with self._lock:
            self._maybe_reset_daily()
            self._daily_pnl    += pnl

            if close_round_trip:
                self._trades_today += 1
                if pnl < 0:
                    self._consecutive_losses += 1
                else:
                    self._consecutive_losses = 0

            _log.info(
                "Realised P&L recorded: P&L=₹%.2f  |  "
                "day_pnl=₹%.2f  trades=%d/%d  consec_losses=%d/%d",
                pnl,
                self._daily_pnl,
                self._trades_today,  self.max_trades_per_day,
                self._consecutive_losses, self.max_consecutive_losses,
            )

            if self.daily_loss_limit > 0 and self._daily_pnl <= -self.daily_loss_limit:
                self._halt(f"daily loss limit ₹{self.daily_loss_limit:.0f} breached")

    def sync_from_portfolio(self, session) -> None:
        """
        Seed today's P&L from the live portfolio at startup.

        Prevents the daily loss limit from resetting to zero on bot restart
        mid-session. Uses realised P&L only (open positions aren't counted
        until they close).

        Safe to call even if the portfolio API is unavailable — logs a
        warning and continues with P&L = 0.
        """
        try:
            from broker.portfolio import get_position_pnl
            pnl_data    = get_position_pnl(session)
            realised    = float(pnl_data.get("realised", 0.0))
            with self._lock:
                self._daily_pnl = realised
            _log.info(
                "Risk manager synced from portfolio: day_realised_pnl=₹%.2f", realised
            )
        except Exception as e:
            _log.warning(
                "Could not sync P&L from portfolio (%s) — starting from ₹0", e
            )

    def status(self) -> dict:
        """
        Return a snapshot of current risk state for logging/display.

        Keys:
            daily_pnl           : cumulative P&L today (₹)
            trades_today        : completed round-trips today
            consecutive_losses  : current loss streak
            halted              : True if bot has been halted
            halt_reason         : reason string if halted
            loss_limit_used_pct : how much of the daily loss limit has been consumed
        """
        with self._lock:
            used_pct = (
                abs(self._daily_pnl) / self.daily_loss_limit * 100
                if self.daily_loss_limit > 0 and self._daily_pnl < 0 else 0.0
            )
            return {
                "daily_pnl":          round(self._daily_pnl, 2),
                "trades_today":       self._trades_today,
                "consecutive_losses": self._consecutive_losses,
                "halted":             self._halted,
                "halt_reason":        self._halt_reason,
                "loss_limit_used_pct":round(used_pct, 1),
            }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _reset_daily_state(self) -> None:
        self._daily_pnl          = 0.0
        self._trades_today       = 0
        self._consecutive_losses = 0
        self._halted             = False
        self._halt_reason        = ""
        self._trade_date         = datetime.now(IST).date()

    def _maybe_reset_daily(self) -> None:
        """Reset all daily counters if the calendar date has changed (IST)."""
        today = datetime.now(IST).date()
        if today != self._trade_date:
            _log.info("New trading day (%s) — resetting daily risk state", today)
            self._reset_daily_state()

    def _halt(self, reason: str) -> None:
        self._halted      = True
        self._halt_reason = reason
        _log.warning("*** BOT HALTED *** reason: %s", reason)
