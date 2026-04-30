"""
risk/trailing_sl.py — Software-side Trailing Stop Loss
=======================================================
AngelOne has no native TSL order type. This class implements the logic
entirely in software, driven by live ticks (update) or OHLC bars (simulate_bar).

Modes
-----
"points"  — trail by a fixed ₹ amount              (value = ₹ distance)
"pct"     — trail by % of the current peak price   (value = e.g. 0.5 for 0.5%)
"atr"     — trail by ATR × multiplier at entry     (value = multiplier; atr passed to arm())

activation_gap
--------------
Minimum ₹ profit from entry before trailing activates.
  0.0  = activate immediately (TSL starts at entry − trail from the first tick).
  3.0  = TSL only kicks in after price moves ₹3 in your favour.
This prevents the SL from being stopped out on normal noise right after entry.

Live usage (on_tick):
    tsl = TrailingSL(mode="points", value=5.0, activation_gap=3.0)
    tsl.arm(entry_price=100.0)         # call once on BUY fill
    if tsl.update(ltp):                # True when SL is hit → place SELL
        emit_sell_signal()
    tsl.reset()                        # call on SELL fill

Backtest usage (bar-by-bar):
    hit, exit_price = tsl.simulate_bar(bar_high, bar_low)
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

_VALID_MODES = ("points", "pct", "atr")


class TrailingSL:

    def __init__(self, mode: str, value: float, activation_gap: float = 0.0):
        if mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode!r}")
        if value <= 0:
            raise ValueError(f"value must be > 0, got {value}")

        self._mode           = mode
        self._value          = float(value)
        self._activation_gap = float(activation_gap)

        self._armed       = False
        self._activated   = False
        self._direction   = "long"
        self._entry_price = 0.0
        self._peak        = 0.0   # high-water mark (longs) or low-water mark (shorts)
        self._atr         = 0.0
        self._current_sl  = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def arm(self, entry_price: float, direction: str = "long", atr: float = 0.0) -> None:
        """
        Arm the TSL after a position is opened.

        Args:
            entry_price : fill price of the entry order (₹)
            direction   : "long" (default) or "short"
            atr         : ATR value at entry — required when mode == "atr"
        """
        if direction not in ("long", "short"):
            raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")
        if entry_price <= 0:
            raise ValueError(f"entry_price must be > 0, got {entry_price}")
        if self._mode == "atr" and atr <= 0:
            raise ValueError("atr must be > 0 when mode == 'atr'")

        self._direction   = direction
        self._entry_price = entry_price
        self._peak        = entry_price
        self._atr         = float(atr)
        self._armed       = True
        self._activated   = (self._activation_gap == 0.0)
        self._current_sl  = self._compute_sl()

        _log.info(
            "TSL armed: direction=%s  entry=₹%.2f  mode=%s  value=%s  "
            "gap=₹%.2f  initial_sl=₹%.2f  activated=%s",
            direction, entry_price, self._mode, self._value,
            self._activation_gap, self._current_sl, self._activated,
        )

    def update(self, ltp: float) -> bool:
        """
        Update with the latest price. Returns True when the SL is hit.
        Call on every tick while in position.
        Thread-safe for CPython (GIL protects simple float assignments).
        """
        if not self._armed:
            return False

        is_long = self._direction == "long"

        if not self._activated:
            if is_long:
                self._activated = ltp >= self._entry_price + self._activation_gap
            else:
                self._activated = ltp <= self._entry_price - self._activation_gap
            if self._activated:
                _log.info(
                    "TSL activated at ₹%.2f  (entry=₹%.2f  gap=₹%.2f)",
                    ltp, self._entry_price, self._activation_gap,
                )

        if not self._activated:
            return False

        if is_long:
            if ltp > self._peak:
                self._peak       = ltp
                self._current_sl = self._compute_sl()
                _log.debug("TSL: new peak=₹%.2f  sl=₹%.2f", self._peak, self._current_sl)
        else:
            if ltp < self._peak:
                self._peak       = ltp
                self._current_sl = self._compute_sl()
                _log.debug("TSL: new trough=₹%.2f  sl=₹%.2f", self._peak, self._current_sl)

        hit = (ltp <= self._current_sl) if is_long else (ltp >= self._current_sl)
        if hit:
            _log.warning(
                "TSL HIT: ltp=₹%.2f  sl=₹%.2f  peak=₹%.2f  direction=%s",
                ltp, self._current_sl, self._peak, self._direction,
            )
        return hit

    def simulate_bar(self, high: float, low: float) -> tuple[bool, float]:
        """
        Simulate TSL for one OHLC bar (backtesting only).

        Convention: price reaches the favourable extreme first (high for longs,
        low for shorts), then the adverse extreme. This is the standard
        backtest convention and is slightly optimistic on peak capture.

        Returns:
            (True,  exit_price)  — SL was hit; exit at the TSL level
            (False, 0.0)         — SL not hit this bar
        """
        if not self._armed:
            return False, 0.0

        is_long = self._direction == "long"
        fav = high if is_long else low   # favourable intrabar extreme
        adv = low  if is_long else high  # adverse   intrabar extreme

        # Step 1 — activation + peak update using the favourable extreme
        if not self._activated:
            if is_long:
                self._activated = fav >= self._entry_price + self._activation_gap
            else:
                self._activated = fav <= self._entry_price - self._activation_gap

        if self._activated:
            if (is_long and fav > self._peak) or (not is_long and fav < self._peak):
                self._peak       = fav
                self._current_sl = self._compute_sl()

        if not self._activated:
            return False, 0.0

        # Step 2 — hit check using the adverse extreme
        hit = (adv <= self._current_sl) if is_long else (adv >= self._current_sl)
        return (True, self._current_sl) if hit else (False, 0.0)

    def reset(self) -> None:
        """Call after the position closes to clear all TSL state."""
        self._armed      = False
        self._activated  = False
        self._peak       = 0.0
        self._current_sl = 0.0

    @property
    def current_sl(self) -> float:
        """Current trailing SL price. 0.0 if not armed or not yet activated."""
        return self._current_sl if (self._armed and self._activated) else 0.0

    @property
    def is_armed(self) -> bool:
        return self._armed

    @property
    def is_activated(self) -> bool:
        return self._activated

    # ── Internal ──────────────────────────────────────────────────────────────

    def _compute_sl(self) -> float:
        trail = self._trail_distance()
        return (self._peak - trail) if self._direction == "long" else (self._peak + trail)

    def _trail_distance(self) -> float:
        if self._mode == "points":
            return self._value
        if self._mode == "pct":
            return self._peak * self._value / 100.0
        return self._atr * self._value   # "atr"
