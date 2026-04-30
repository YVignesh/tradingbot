"""
ai/guardrails.py — AI Output Validation & Audit
=================================================
Hard limits that the AI can never override. Every AI recommendation
passes through GuardRail.validate() before being applied.

Responsibilities:
- Clamp numeric parameters to safe ranges
- Cap daily parameter deltas (no wild swings)
- Block structural violations (e.g. disabling risk manager)
- Audit log every recommendation vs what was actually applied
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from utils import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
_log = get_logger("ai.guardrails")


# ──────────────────────────────────────────────────────────────
# Hard limits — coded in Python, never overridable by AI
# ──────────────────────────────────────────────────────────────

_PARAM_BOUNDS = {
    "sl_atr_multiplier":   (0.5, 3.0),
    "tp_atr_multiplier":   (1.0, 5.0),
    "max_risk_pct":        (0.5, 3.0),
    "daily_loss_limit":    (500, None),     # max computed dynamically from capital
    "max_trades_per_day":  (1, 20),
    "max_consecutive_losses": (2, 10),
    "tsl_activation_gap":  (1.0, 20.0),
    "tsl_value":           (0.5, 5.0),
}

# Max allowed change per day — prevents wild AI swings
_MAX_DELTA_PER_DAY = {
    "sl_atr_multiplier":   0.5,
    "tp_atr_multiplier":   1.0,
    "max_risk_pct":        0.5,
    "tsl_activation_gap":  3.0,
    "tsl_value":           1.0,
    "daily_loss_limit":    1000,
    "max_trades_per_day":  5,
    "max_consecutive_losses": 2,
}

# Fields the AI is never allowed to touch
_FORBIDDEN_FIELDS = frozenset({
    "dry_run", "capital", "api_key", "bot_token", "chat_id",
    "ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_MPIN", "ANGEL_TOTP_SECRET",
})

# Allowlist: only these risk params can be modified by AI (#3)
_ALLOWED_RISK_PARAMS = frozenset({
    "sl_atr_multiplier", "tp_atr_multiplier", "max_risk_pct",
    "daily_loss_limit", "max_trades_per_day", "max_consecutive_losses",
    "tsl_activation_gap", "tsl_value",
})


class GuardRail:
    """Validates, clamps, and audits every AI recommendation."""

    def __init__(self, config: dict):
        guard_cfg = config.get("ai", {}).get("guardrails", {})
        self.capital = float(config.get("risk", {}).get("capital", 100_000))
        self.audit_enabled = bool(guard_cfg.get("require_audit_log", True))

        # Allow config overrides for bounds
        self._bounds = dict(_PARAM_BOUNDS)
        for key in ("sl_atr_min", "sl_atr_max", "tp_atr_min", "tp_atr_max",
                     "risk_pct_min", "risk_pct_max"):
            pass  # bounds from config used below

        if guard_cfg.get("sl_atr_min"):
            self._bounds["sl_atr_multiplier"] = (
                float(guard_cfg["sl_atr_min"]),
                float(guard_cfg.get("sl_atr_max", 3.0)),
            )
        if guard_cfg.get("tp_atr_min"):
            self._bounds["tp_atr_multiplier"] = (
                float(guard_cfg["tp_atr_min"]),
                float(guard_cfg.get("tp_atr_max", 5.0)),
            )
        if guard_cfg.get("risk_pct_min"):
            self._bounds["max_risk_pct"] = (
                float(guard_cfg["risk_pct_min"]),
                float(guard_cfg.get("risk_pct_max", 3.0)),
            )

        max_delta_override = float(guard_cfg.get("max_param_delta_per_day", 0))
        self._max_delta = dict(_MAX_DELTA_PER_DAY)  # Copy, don't mutate module-level (#3)
        if max_delta_override > 0:
            for k in self._max_delta:
                if k in ("sl_atr_multiplier", "tp_atr_multiplier", "max_risk_pct"):
                    self._max_delta[k] = max_delta_override

        # Dynamic daily loss ceiling: 5% of capital
        lo, _ = self._bounds["daily_loss_limit"]
        self._bounds["daily_loss_limit"] = (lo, self.capital * 0.05)

        self._audit_dir = Path("data/ai/audit")
        self._audit_dir.mkdir(parents=True, exist_ok=True)

    def validate_risk_params(
        self,
        suggested: dict,
        current: dict,
        window: str = "pre_market",
    ) -> dict:
        """
        Validate and clamp suggested risk parameter changes.

        Args:
            suggested: dict of param_name → value from AI
            current: dict of current param values
            window: "pre_market" | "mid_day" | "post_market"

        Returns:
            Sanitized dict with only valid, clamped values.
        """
        applied = {}
        audit_entries = []

        for key, new_val in suggested.items():
            entry = {"param": key, "suggested": new_val, "action": "applied", "reason": ""}

            # Block forbidden fields
            if key in _FORBIDDEN_FIELDS:
                entry["action"] = "BLOCKED"
                entry["reason"] = "forbidden field"
                audit_entries.append(entry)
                _log.warning("AI tried to modify forbidden field '%s' — BLOCKED", key)
                continue

            # Only allow known risk params (#3)
            if key not in _ALLOWED_RISK_PARAMS:
                entry["action"] = "BLOCKED"
                entry["reason"] = "not in allowed risk params"
                audit_entries.append(entry)
                _log.warning("AI tried to set unknown param '%s' — BLOCKED", key)
                continue

            # Must be numeric
            try:
                new_val = float(new_val)
            except (TypeError, ValueError):
                entry["action"] = "BLOCKED"
                entry["reason"] = "non-numeric value"
                audit_entries.append(entry)
                continue

            original = new_val

            # Clamp to hard bounds
            if key in self._bounds:
                lo, hi = self._bounds[key]
                if lo is not None and new_val < lo:
                    new_val = lo
                if hi is not None and new_val > hi:
                    new_val = hi
                if new_val != original:
                    entry["action"] = "CLAMPED"
                    entry["reason"] = f"bounds [{lo}, {hi}]"

            # Cap daily delta
            if key in self._max_delta and key in current:
                max_delta = self._max_delta[key]
                cur_val = float(current[key])
                delta = new_val - cur_val
                if abs(delta) > max_delta:
                    new_val = cur_val + max_delta * (1 if delta > 0 else -1)
                    entry["action"] = "DELTA_CAPPED"
                    entry["reason"] = f"max delta ±{max_delta}, cur={cur_val:.2f}"

            entry["applied_value"] = round(new_val, 4)
            applied[key] = round(new_val, 4)
            audit_entries.append(entry)

        self._write_audit(window, audit_entries)
        return applied

    def validate_symbol_list(
        self,
        suggested: list[str],
        universe: list[str],
        window: str = "pre_market",
    ) -> list[str]:
        """
        Filter suggested symbols to only those in the screener universe.

        AI may return real-world tickers (e.g. "RIL", "COALINDIA") that differ
        from AngelOne's naming convention ("RELIANCE-EQ", "COALINDIA-EQ"). Each
        symbol is resolved via InstrumentMaster before comparing to the universe.

        Args:
            suggested: AI's preferred symbols (may be bare tickers)
            universe: valid AngelOne symbols from screener

        Returns:
            Filtered list in canonical AngelOne format.
        """
        from broker.instruments import InstrumentMaster
        master = InstrumentMaster()
        master.load()

        universe_set = {s.upper() for s in universe}
        valid = []
        rejected = []

        for sym in suggested:
            sym_upper = sym.upper()

            # Try exact match first
            if sym_upper in universe_set:
                valid.append(sym_upper)
                continue

            # Try resolving to canonical AngelOne symbol (e.g. RIL → RELIANCE-EQ)
            resolved = master.resolve_symbol("NSE", sym_upper)
            if resolved and resolved.upper() in universe_set:
                _log.debug("AI symbol %s resolved to %s", sym_upper, resolved)
                valid.append(resolved.upper())
                continue

            rejected.append(sym_upper)

        if rejected:
            _log.warning(
                "AI suggested symbols not in universe: %s — removed", rejected,
            )
            self._write_audit(window, [{
                "param": "symbols_rejected",
                "suggested": rejected,
                "action": "BLOCKED",
                "reason": "not in screener universe",
            }])

        return valid

    def validate_strategy_name(
        self,
        suggested: str,
        available: list[str],
    ) -> Optional[str]:
        """Validate AI-suggested strategy is a registered strategy."""
        if suggested in available:
            return suggested
        _log.warning(
            "AI suggested unknown strategy '%s' — ignoring (available: %s)",
            suggested, available,
        )
        return None

    def _write_audit(self, window: str, entries: list[dict]) -> None:
        """Append audit entries to today's audit log."""
        if not self.audit_enabled or not entries:
            return

        today = datetime.now(IST).strftime("%Y-%m-%d")
        audit_file = self._audit_dir / f"{today}.json"

        existing = []
        if audit_file.exists():
            try:
                existing = json.loads(audit_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                existing = []

        record = {
            "timestamp": datetime.now(IST).isoformat(),
            "window": window,
            "entries": entries,
        }
        existing.append(record)
        audit_file.write_text(
            json.dumps(existing, indent=2, default=str),
            encoding="utf-8",
        )
