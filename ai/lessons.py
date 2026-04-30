"""
ai/lessons.py — Persistent Learning Store
============================================
Saves daily AI lessons to JSON files. When a pattern appears 3+ times,
it gets promoted to a permanent rule that is injected into future prompts.

Storage layout:
  data/ai/lessons/YYYY-MM-DD.json  — daily lessons
  data/ai/rules.json               — accumulated rules (auto-extracted)
  data/ai/day_plans/YYYY-MM-DD.json — day plans for mid-day/post reference
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from utils import get_logger

IST = timezone(timedelta(hours=5, minutes=30))
_log = get_logger("ai.lessons")

_MIN_OCCURRENCES_FOR_RULE = 3


class LessonStore:
    """Manages daily lessons and accumulated rules."""

    def __init__(self, lookback_days: int = 7):
        self.lookback_days = lookback_days
        self._base_dir = Path("data/ai")
        self._lessons_dir = self._base_dir / "lessons"
        self._plans_dir = self._base_dir / "day_plans"
        self._rules_file = self._base_dir / "rules.json"

        for d in (self._lessons_dir, self._plans_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── Daily lessons ────────────────────────────────────────────

    def save_lessons(self, date_str: str, lessons: dict) -> None:
        """Save post-market lessons for a given day."""
        path = self._lessons_dir / f"{date_str}.json"
        path.write_text(json.dumps(lessons, indent=2, default=str), encoding="utf-8")
        _log.info("Saved lessons for %s", date_str)

        # Auto-extract rules from accumulated lessons
        self._extract_rules()

    def get_recent_lessons(self, n_days: Optional[int] = None) -> list[dict]:
        """Load lessons from the last N days (most recent first)."""
        n = n_days or self.lookback_days
        today = datetime.now(IST).date()
        lessons = []

        for i in range(n):
            day = today - timedelta(days=i + 1)
            path = self._lessons_dir / f"{day.isoformat()}.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    data["_date"] = day.isoformat()
                    lessons.append(data)
                except (json.JSONDecodeError, OSError):
                    continue

        return lessons

    def format_recent_for_prompt(self, n_days: Optional[int] = None) -> str:
        """Format recent lessons as prompt context."""
        lessons = self.get_recent_lessons(n_days)
        if not lessons:
            return "(No historical lessons available — this is the first run)"

        parts = []
        for lesson in lessons[:7]:  # Cap at 7 days
            date = lesson.get("_date", "?")
            day_pnl = lesson.get("day_pnl", 0)
            win_rate = lesson.get("win_rate", 0)
            lesson_items = lesson.get("lessons", [])

            parts.append(f"[{date}] P&L=₹{day_pnl:.0f} WR={win_rate:.0f}%")
            for item in lesson_items[:3]:  # Max 3 lessons per day
                parts.append(f"  • {item}")

        return "\n".join(parts)

    # ── Day plans ────────────────────────────────────────────────

    def save_day_plan(self, date_str: str, plan: dict) -> None:
        """Save the pre-market day plan for reference."""
        path = self._plans_dir / f"{date_str}.json"
        path.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")

    def get_day_plan(self, date_str: str) -> Optional[dict]:
        """Load today's day plan (for mid-day/post-market reference)."""
        path = self._plans_dir / f"{date_str}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    # ── Accumulated rules ────────────────────────────────────────

    def get_rules(self) -> list[dict]:
        """Load accumulated rules."""
        if not self._rules_file.exists():
            return []
        try:
            data = json.loads(self._rules_file.read_text(encoding="utf-8"))
            return data.get("rules", [])
        except (json.JSONDecodeError, OSError):
            return []

    def format_rules_for_prompt(self) -> str:
        """Format accumulated rules as prompt context."""
        rules = self.get_rules()
        if not rules:
            return ""

        parts = ["LEARNED RULES (from repeated patterns):"]
        for r in rules:
            occurrences = r.get("occurrences", 0)
            parts.append(f"  • {r['rule']} (seen {occurrences} times)")

        return "\n".join(parts)

    def _extract_rules(self) -> None:
        """
        Scan all lessons for repeated patterns and promote to rules.
        A "rule" from post-market AI is considered repeated if similar
        text appears in lessons from 3+ different days.
        """
        all_lessons = self.get_recent_lessons(n_days=30)
        if len(all_lessons) < _MIN_OCCURRENCES_FOR_RULE:
            return

        # Collect all suggested rules from lessons
        rule_candidates: dict[str, list[str]] = {}  # rule_text → [dates]
        for lesson in all_lessons:
            date = lesson.get("_date", "")
            for rule_text in lesson.get("rules_to_add", []):
                # Normalize for comparison
                key = rule_text.strip().lower()[:100]
                if key not in rule_candidates:
                    rule_candidates[key] = []
                if date not in rule_candidates[key]:
                    rule_candidates[key].append(date)

        # Promote candidates with enough occurrences
        existing_rules = self.get_rules()
        existing_keys = {r["rule"].strip().lower()[:100] for r in existing_rules}

        new_rules = list(existing_rules)
        for key, dates in rule_candidates.items():
            if len(dates) >= _MIN_OCCURRENCES_FOR_RULE and key not in existing_keys:
                # Find original (non-lowered) text from most recent lesson
                original_text = key
                for lesson in all_lessons:
                    for rule_text in lesson.get("rules_to_add", []):
                        if rule_text.strip().lower()[:100] == key:
                            original_text = rule_text
                            break

                new_rules.append({
                    "rule": original_text,
                    "first_seen": min(dates),
                    "occurrences": len(dates),
                    "source_lessons": sorted(dates),
                })
                _log.info(
                    "New rule promoted (%d occurrences): %s",
                    len(dates), original_text[:80],
                )

        # Update existing rules' occurrence counts
        for rule in new_rules:
            key = rule["rule"].strip().lower()[:100]
            if key in rule_candidates:
                rule["occurrences"] = len(rule_candidates[key])
                rule["source_lessons"] = sorted(rule_candidates[key])

        self._rules_file.write_text(
            json.dumps({"rules": new_rules}, indent=2, default=str),
            encoding="utf-8",
        )
