"""Domain primitives for config-driven alias rules.

A rule is an ordered (condition, target) pair. The first rule whose condition
matches the current time wins; its target expression is resolved against the
current time to produce a target datetime. Target expressions are dateparser
strings ("2h", "4pm", "16:00") or the literal token "end" which resolves to
condition.end. This module owns the rule schema - config loading, validation,
and target resolution live elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAY_SET = frozenset(WEEKDAY_NAMES)


@dataclass
class Condition:
    """Optional time-of-day window and optional day-of-week filter.

    All fields are optional; an empty condition always matches. If `start` and
    `end` are both set, the current local time must fall in [start, end).
    Overnight windows (start > end) wrap past midnight: 22:00-02:00 matches
    23:00 and 01:00. If `days` is set, the current weekday must be in the set.
    """

    start: time | None = None
    end: time | None = None
    days: set[str] | None = None

    def matches(self, now: datetime) -> bool:
        if self.days is not None:
            today = WEEKDAY_NAMES[now.weekday()]
            if today not in self.days:
                return False
        if self.start is not None and self.end is not None:
            current = now.time()
            if self.start <= self.end:
                in_window = self.start <= current < self.end
            else:
                in_window = current >= self.start or current < self.end
            if not in_window:
                return False
        return True


@dataclass
class Rule:
    condition: Condition | None
    target: str


def evaluate(rules: list[Rule], now: datetime) -> Rule | None:
    """Return the first matching rule, or None."""
    for rule in rules:
        if rule.condition is None or rule.condition.matches(now):
            return rule
    return None


def combine(now: datetime, d: date, t: time) -> datetime:
    """Combine a date and time, preserving the source datetime's tzinfo."""
    return datetime.combine(d, t).replace(tzinfo=now.tzinfo)
