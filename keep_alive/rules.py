"""Domain primitives for config-driven alias rules.

A rule is an ordered (condition, action) pair. The first rule whose condition
matches the current time wins; its action is applied to produce a target
datetime. This module owns the schema - config loading and validation lives
elsewhere.

Known limitation: overnight windows (start > end) are not supported. A window
of 22:00-02:00 will never match.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
WEEKDAY_SET = frozenset(WEEKDAY_NAMES)

ActionKind = Literal[
    "until_window_end",
    "extend_window",
    "relative_duration",
    "absolute_time",
]


@dataclass
class Condition:
    """Optional time-of-day window and optional day-of-week filter.

    All fields are optional; an empty condition always matches. If `start` and
    `end` are both set, the current local time must fall in [start, end). If
    `days` is set, the current weekday must be in the set.
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
            if not (self.start <= current < self.end):
                return False
        return True


@dataclass
class Action:
    """What target datetime to produce when a rule matches.

    `until_window_end` and `extend_window` require a condition with `end` set.
    `relative_duration` requires `duration`. `absolute_time` requires `time`.
    """

    kind: ActionKind
    duration: timedelta | None = None
    time: time | None = None

    def apply(self, now: datetime, condition: Condition | None) -> datetime:
        if self.kind == "relative_duration":
            if self.duration is None:
                raise ValueError("relative_duration action requires duration")
            return now + self.duration
        if self.kind == "absolute_time":
            if self.time is None:
                raise ValueError("absolute_time action requires time")
            return _combine(now, now.date(), self.time)
        # until_window_end and extend_window both need condition.end
        if condition is None or condition.end is None:
            raise ValueError(f"{self.kind} action requires a condition with end")
        target = _combine(now, now.date(), condition.end)
        if self.kind == "extend_window":
            if self.duration is None:
                raise ValueError("extend_window action requires duration")
            target = target + self.duration
        return target


@dataclass
class Rule:
    condition: Condition | None
    action: Action


def evaluate(rules: list[Rule], now: datetime) -> datetime | None:
    """Return the target datetime from the first matching rule, or None."""
    for rule in rules:
        if rule.condition is None or rule.condition.matches(now):
            return rule.action.apply(now, rule.condition)
    return None


def _combine(now: datetime, d: date, t: time) -> datetime:
    """Combine a date and time, preserving the source datetime's tzinfo."""
    return datetime.combine(d, t).replace(tzinfo=now.tzinfo)
