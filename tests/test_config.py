from datetime import time, timedelta

import pytest

from keep_alive.config import (
    Config,
    ConfigError,
    default_config_path,
    load_config,
)


def _write(tmp_path, content):
    p = tmp_path / "config.toml"
    p.write_text(content)
    return p


VALID_CONFIG = """\
[[rule]]
action = "relative_duration"
duration = "30m"

[[alias]]
name = "work"

    [[alias.rule]]
    start = "05:00"
    end = "16:00"
    days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    action = "until_window_end"

    [[alias.rule]]
    action = "relative_duration"
    duration = "2h"

[[alias]]
name = "personal"

    [[alias.rule]]
    start = "05:00"
    end = "19:00"
    action = "absolute_time"
    time = "16:00"

    [[alias.rule]]
    action = "relative_duration"
    duration = "1h"
"""


class TestLoadValidConfig:
    def test_loads_full_config(self, tmp_path):
        p = _write(tmp_path, VALID_CONFIG)
        cfg = load_config(p)
        assert isinstance(cfg, Config)
        assert set(cfg.aliases) == {"work", "personal"}
        assert len(cfg.global_rules) == 1

    def test_global_rule_parsed(self, tmp_path):
        p = _write(tmp_path, VALID_CONFIG)
        cfg = load_config(p)
        rule = cfg.global_rules[0]
        assert rule.condition is None
        assert rule.action.kind == "relative_duration"
        assert rule.action.duration == timedelta(minutes=30)

    def test_alias_with_condition_parsed(self, tmp_path):
        p = _write(tmp_path, VALID_CONFIG)
        cfg = load_config(p)
        rule = cfg.aliases["work"][0]
        assert rule.condition is not None
        assert rule.condition.start == time(5, 0)
        assert rule.condition.end == time(16, 0)
        assert rule.condition.days == {"Mon", "Tue", "Wed", "Thu", "Fri"}
        assert rule.action.kind == "until_window_end"

    def test_alias_unconditional_rule_parsed(self, tmp_path):
        p = _write(tmp_path, VALID_CONFIG)
        cfg = load_config(p)
        rule = cfg.aliases["work"][1]
        assert rule.condition is None
        assert rule.action.kind == "relative_duration"
        assert rule.action.duration == timedelta(hours=2)

    def test_absolute_time_action_parsed(self, tmp_path):
        p = _write(tmp_path, VALID_CONFIG)
        cfg = load_config(p)
        rule = cfg.aliases["personal"][0]
        assert rule.action.kind == "absolute_time"
        assert rule.action.time == time(16, 0)


class TestMissingAndEmpty:
    def test_missing_file_returns_empty_config(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.toml")
        assert cfg.aliases == {}
        assert cfg.global_rules == []

    def test_empty_file_returns_empty_config(self, tmp_path):
        p = _write(tmp_path, "")
        cfg = load_config(p)
        assert cfg.aliases == {}
        assert cfg.global_rules == []


class TestInvalidToml:
    def test_malformed_toml_raises(self, tmp_path):
        p = _write(tmp_path, "this is = = not toml [[")
        with pytest.raises(ConfigError, match="invalid TOML"):
            load_config(p)


class TestAliasErrors:
    def test_alias_missing_name(self, tmp_path):
        p = _write(tmp_path, "[[alias]]\n")
        with pytest.raises(ConfigError, match="missing 'name'"):
            load_config(p)

    def test_duplicate_alias_names(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias]]\nname = "x"\n',
        )
        with pytest.raises(ConfigError, match="duplicate alias name 'x'"):
            load_config(p)


class TestRuleErrors:
    def test_rule_missing_action(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\nstart = "09:00"\n',
        )
        with pytest.raises(ConfigError, match="missing 'action'"):
            load_config(p)

    def test_invalid_action_kind(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\naction = "fly"\n',
        )
        with pytest.raises(ConfigError, match="invalid action 'fly'"):
            load_config(p)

    def test_global_rule_missing_action(self, tmp_path):
        p = _write(tmp_path, '[[rule]]\nstart = "09:00"\n')
        with pytest.raises(ConfigError, match="global rule 1.*missing 'action'"):
            load_config(p)


class TestActionParameterErrors:
    def test_until_window_end_requires_end(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\naction = "until_window_end"\n',
        )
        with pytest.raises(ConfigError, match="requires 'end'"):
            load_config(p)

    def test_extend_window_requires_end(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\naction = "extend_window"\nduration = "1h"\n',
        )
        with pytest.raises(ConfigError, match="requires 'end'"):
            load_config(p)

    def test_extend_window_requires_duration(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\naction = "extend_window"\nend = "17:00"\n',
        )
        with pytest.raises(ConfigError, match="requires 'duration'"):
            load_config(p)

    def test_relative_duration_requires_duration(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\naction = "relative_duration"\n',
        )
        with pytest.raises(ConfigError, match="requires 'duration'"):
            load_config(p)

    def test_absolute_time_requires_time(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\naction = "absolute_time"\n',
        )
        with pytest.raises(ConfigError, match="requires 'time'"):
            load_config(p)


class TestFieldValidation:
    def test_invalid_time_format(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\nstart = "25:00"\nend = "17:00"\n'
            'action = "until_window_end"\n',
        )
        with pytest.raises(ConfigError, match="invalid time '25:00'"):
            load_config(p)

    def test_invalid_time_format_non_numeric(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\nstart = "noon"\nend = "17:00"\n'
            'action = "until_window_end"\n',
        )
        with pytest.raises(ConfigError, match="invalid time 'noon'"):
            load_config(p)

    def test_invalid_day_name(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\nstart = "09:00"\nend = "17:00"\n'
            'days = ["Mon", "Funday"]\naction = "until_window_end"\n',
        )
        with pytest.raises(ConfigError, match="invalid day name"):
            load_config(p)

    def test_invalid_duration(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\naction = "relative_duration"\nduration = "banana"\n',
        )
        with pytest.raises(ConfigError, match="invalid duration 'banana'"):
            load_config(p)

    def test_duration_accepts_compound_form(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\naction = "relative_duration"\nduration = "1h30m"\n',
        )
        cfg = load_config(p)
        assert cfg.aliases["x"][0].action.duration == timedelta(hours=1, minutes=30)


class TestPathResolution:
    def test_xdg_config_home_honored(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("HOME", raising=False)
        # Path.home() falls back to pwd on POSIX without HOME; set it to tmp_path too
        monkeypatch.setenv("HOME", str(tmp_path))
        result = default_config_path()
        assert result == tmp_path / "keep-alive" / "config.toml"

    def test_falls_back_to_home_config(self, monkeypatch, tmp_path):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        result = default_config_path()
        assert result == tmp_path / ".config" / "keep-alive" / "config.toml"

    def test_explicit_path_overrides_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "/should/not/be/used")
        p = _write(tmp_path, VALID_CONFIG)
        cfg = load_config(p)
        assert "work" in cfg.aliases


class TestActionKindCoverage:
    """Verify all four action kinds round-trip through the loader."""

    @pytest.mark.parametrize(
        "rule_toml,kind,attr,expected",
        [
            (
                'action = "until_window_end"\nstart = "09:00"\nend = "17:00"\n',
                "until_window_end",
                "kind",
                "until_window_end",
            ),
            (
                'action = "extend_window"\nstart = "09:00"\nend = "17:00"\nduration = "1h"\n',
                "extend_window",
                "duration",
                timedelta(hours=1),
            ),
            (
                'action = "relative_duration"\nduration = "30m"\n',
                "relative_duration",
                "duration",
                timedelta(minutes=30),
            ),
            (
                'action = "absolute_time"\ntime = "16:00"\n',
                "absolute_time",
                "time",
                time(16, 0),
            ),
        ],
    )
    def test_action_kind_round_trips(self, tmp_path, rule_toml, kind, attr, expected):
        p = _write(
            tmp_path,
            f'[[alias]]\nname = "x"\n[[alias.rule]]\n{rule_toml}',
        )
        cfg = load_config(p)
        action = cfg.aliases["x"][0].action
        assert action.kind == kind
        assert getattr(action, attr) == expected
