from datetime import UTC, datetime, time

from keep_alive.rules import Condition, Rule, evaluate


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


class TestEvaluate:
    def test_empty_rules_returns_none(self):
        assert evaluate([], _dt(2024, 1, 15, 12, 0)) is None

    def test_no_match_returns_none(self):
        rules = [
            Rule(
                condition=Condition(start=time(9, 0), end=time(10, 0)),
                target="2h",
            ),
        ]
        # 12:00 is outside the 9-10 window
        assert evaluate(rules, _dt(2024, 1, 15, 12, 0)) is None

    def test_first_match_wins(self):
        rules = [
            Rule(
                condition=Condition(start=time(9, 0), end=time(10, 0)),
                target="11am",
            ),
            Rule(
                condition=None,
                target="2h",
            ),
        ]
        # 9:30 matches the first rule
        matched = evaluate(rules, _dt(2024, 1, 15, 9, 30))
        assert matched is rules[0]

    def test_falls_through_to_unconditional(self):
        rules = [
            Rule(
                condition=Condition(start=time(9, 0), end=time(10, 0)),
                target="11am",
            ),
            Rule(
                condition=None,
                target="2h",
            ),
        ]
        # 12:00 misses the first rule, falls to the unconditional one
        matched = evaluate(rules, _dt(2024, 1, 15, 12, 0))
        assert matched is rules[1]

    def test_day_filter_can_block_first_rule(self):
        rules = [
            Rule(
                # Monday-only window
                condition=Condition(start=time(9, 0), end=time(17, 0), days={"Mon"}),
                target="end",
            ),
            Rule(
                condition=None,
                target="1h",
            ),
        ]
        # Tuesday: first rule's window time matches but day filter blocks it
        matched = evaluate(rules, _dt(2024, 1, 16, 12, 0))
        assert matched is rules[1]
