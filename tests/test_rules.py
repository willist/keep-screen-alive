from datetime import UTC, datetime, time, timedelta, timezone

import pytest

from keep_alive.rules import Action, Condition, Rule, evaluate


def _dt(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


class TestConditionMatches:
    def test_unconditional_always_matches(self):
        cond = Condition()
        assert cond.matches(_dt(2024, 1, 15, 12, 0))

    def test_days_filter_allowed(self):
        # 2024-01-15 is Monday
        cond = Condition(days={"Mon", "Tue"})
        assert cond.matches(_dt(2024, 1, 15, 12, 0))

    def test_days_filter_not_allowed(self):
        # 2024-01-15 is Monday; rule only allows Tuesday
        cond = Condition(days={"Tue"})
        assert not cond.matches(_dt(2024, 1, 15, 12, 0))

    def test_time_window_in_range(self):
        cond = Condition(start=time(9, 0), end=time(17, 0))
        assert cond.matches(_dt(2024, 1, 15, 12, 0))

    def test_time_window_at_start_inclusive(self):
        cond = Condition(start=time(9, 0), end=time(17, 0))
        assert cond.matches(_dt(2024, 1, 15, 9, 0))

    def test_time_window_at_end_exclusive(self):
        cond = Condition(start=time(9, 0), end=time(17, 0))
        assert not cond.matches(_dt(2024, 1, 15, 17, 0))

    def test_time_window_before_range(self):
        cond = Condition(start=time(9, 0), end=time(17, 0))
        assert not cond.matches(_dt(2024, 1, 15, 8, 59))

    def test_days_and_time_match(self):
        # 2024-01-15 12:00 is Monday midday
        cond = Condition(start=time(9, 0), end=time(17, 0), days={"Mon"})
        assert cond.matches(_dt(2024, 1, 15, 12, 0))

    def test_days_and_time_day_mismatch(self):
        cond = Condition(start=time(9, 0), end=time(17, 0), days={"Tue"})
        assert not cond.matches(_dt(2024, 1, 15, 12, 0))

    def test_days_and_time_window_mismatch(self):
        # Right day, outside window
        cond = Condition(start=time(9, 0), end=time(17, 0), days={"Mon"})
        assert not cond.matches(_dt(2024, 1, 15, 8, 0))


class TestActionApply:
    def test_relative_duration(self):
        now = _dt(2024, 1, 15, 12, 0)
        action = Action(kind="relative_duration", duration=timedelta(hours=2))
        assert action.apply(now, None) == _dt(2024, 1, 15, 14, 0)

    def test_absolute_time(self):
        now = _dt(2024, 1, 15, 12, 0)
        action = Action(kind="absolute_time", time=time(16, 0))
        assert action.apply(now, None) == _dt(2024, 1, 15, 16, 0)

    def test_absolute_time_preserves_timezone(self):
        tz = timezone(timedelta(hours=-5))
        now = datetime(2024, 1, 15, 12, 0, tzinfo=tz)
        action = Action(kind="absolute_time", time=time(16, 0))
        result = action.apply(now, None)
        assert result.utcoffset() == timedelta(hours=-5)
        assert result.time() == time(16, 0)

    def test_until_window_end(self):
        now = _dt(2024, 1, 15, 12, 0)
        cond = Condition(start=time(9, 0), end=time(17, 0))
        action = Action(kind="until_window_end")
        assert action.apply(now, cond) == _dt(2024, 1, 15, 17, 0)

    def test_extend_window(self):
        now = _dt(2024, 1, 15, 12, 0)
        cond = Condition(start=time(9, 0), end=time(17, 0))
        action = Action(kind="extend_window", duration=timedelta(hours=1))
        assert action.apply(now, cond) == _dt(2024, 1, 15, 18, 0)

    def test_until_window_end_without_condition_raises(self):
        now = _dt(2024, 1, 15, 12, 0)
        action = Action(kind="until_window_end")
        with pytest.raises(ValueError, match="condition"):
            action.apply(now, None)

    def test_until_window_end_with_condition_missing_end_raises(self):
        now = _dt(2024, 1, 15, 12, 0)
        cond = Condition(days={"Mon"})
        action = Action(kind="until_window_end")
        with pytest.raises(ValueError, match="condition"):
            action.apply(now, cond)

    def test_relative_duration_without_duration_raises(self):
        now = _dt(2024, 1, 15, 12, 0)
        action = Action(kind="relative_duration")
        with pytest.raises(ValueError, match="duration"):
            action.apply(now, None)

    def test_absolute_time_without_time_raises(self):
        now = _dt(2024, 1, 15, 12, 0)
        action = Action(kind="absolute_time")
        with pytest.raises(ValueError, match="time"):
            action.apply(now, None)

    def test_extend_window_without_duration_raises(self):
        now = _dt(2024, 1, 15, 12, 0)
        cond = Condition(start=time(9, 0), end=time(17, 0))
        action = Action(kind="extend_window")
        with pytest.raises(ValueError, match="duration"):
            action.apply(now, cond)


class TestEvaluate:
    def test_empty_rules_returns_none(self):
        assert evaluate([], _dt(2024, 1, 15, 12, 0)) is None

    def test_no_match_returns_none(self):
        rules = [
            Rule(
                condition=Condition(start=time(9, 0), end=time(10, 0)),
                action=Action(kind="relative_duration", duration=timedelta(hours=1)),
            ),
        ]
        # 12:00 is outside the 9-10 window
        assert evaluate(rules, _dt(2024, 1, 15, 12, 0)) is None

    def test_first_match_wins(self):
        rules = [
            Rule(
                condition=Condition(start=time(9, 0), end=time(10, 0)),
                action=Action(kind="absolute_time", time=time(11, 0)),
            ),
            Rule(
                condition=None,
                action=Action(kind="relative_duration", duration=timedelta(hours=2)),
            ),
        ]
        # 9:30 matches the first rule
        assert evaluate(rules, _dt(2024, 1, 15, 9, 30)) == _dt(2024, 1, 15, 11, 0)

    def test_falls_through_to_unconditional(self):
        rules = [
            Rule(
                condition=Condition(start=time(9, 0), end=time(10, 0)),
                action=Action(kind="absolute_time", time=time(11, 0)),
            ),
            Rule(
                condition=None,
                action=Action(kind="relative_duration", duration=timedelta(hours=2)),
            ),
        ]
        # 12:00 misses the first rule, falls to the unconditional one
        assert evaluate(rules, _dt(2024, 1, 15, 12, 0)) == _dt(2024, 1, 15, 14, 0)

    def test_day_filter_can_block_first_rule(self):
        rules = [
            Rule(
                # Monday-only window
                condition=Condition(start=time(9, 0), end=time(17, 0), days={"Mon"}),
                action=Action(kind="until_window_end"),
            ),
            Rule(
                condition=None,
                action=Action(kind="relative_duration", duration=timedelta(hours=1)),
            ),
        ]
        # Tuesday: first rule's window time matches but day filter blocks it
        assert evaluate(rules, _dt(2024, 1, 16, 12, 0)) == _dt(2024, 1, 16, 13, 0)
