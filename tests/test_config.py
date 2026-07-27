import re
from pathlib import Path

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
target = "30m"

[[alias]]
name = "work"

    [[alias.rule]]
    start = "05:00"
    end = "16:00"
    days = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    target = "end"

    [[alias.rule]]
    target = "2h"

[[alias]]
name = "personal"

    [[alias.rule]]
    start = "05:00"
    end = "19:00"
    target = "16:00"

    [[alias.rule]]
    target = "1h"
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
        assert rule.target == "30m"

    def test_alias_with_condition_parsed(self, tmp_path):
        p = _write(tmp_path, VALID_CONFIG)
        cfg = load_config(p)
        rule = cfg.aliases["work"][0]
        assert rule.condition is not None
        assert rule.condition.start == __import__("datetime").time(5, 0)
        assert rule.condition.end == __import__("datetime").time(16, 0)
        assert rule.condition.days == {"Mon", "Tue", "Wed", "Thu", "Fri"}
        assert rule.target == "end"

    def test_alias_unconditional_rule_parsed(self, tmp_path):
        p = _write(tmp_path, VALID_CONFIG)
        cfg = load_config(p)
        rule = cfg.aliases["work"][1]
        assert rule.condition is None
        assert rule.target == "2h"

    def test_absolute_time_target_parsed(self, tmp_path):
        p = _write(tmp_path, VALID_CONFIG)
        cfg = load_config(p)
        rule = cfg.aliases["personal"][0]
        assert rule.target == "16:00"


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

    def test_reserved_name_list_rejected(self, tmp_path):
        p = _write(tmp_path, '[[alias]]\nname = "list"\n[[alias.rule]]\ntarget = "2h"\n')
        with pytest.raises(ConfigError, match="reserved"):
            load_config(p)

    def test_reserved_name_status_rejected(self, tmp_path):
        p = _write(tmp_path, '[[alias]]\nname = "status"\n[[alias.rule]]\ntarget = "2h"\n')
        with pytest.raises(ConfigError, match="reserved"):
            load_config(p)

    def test_reserved_name_clear_rejected(self, tmp_path):
        p = _write(tmp_path, '[[alias]]\nname = "clear"\n[[alias.rule]]\ntarget = "2h"\n')
        with pytest.raises(ConfigError, match="reserved"):
            load_config(p)


class TestRuleErrors:
    def test_rule_missing_target(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\nstart = "09:00"\n',
        )
        with pytest.raises(ConfigError, match="missing 'target'"):
            load_config(p)

    def test_old_action_field_rejected_with_migration_pointer(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\nstart = "09:00"\nend = "17:00"\n'
            'action = "until_window_end"\n',
        )
        with pytest.raises(ConfigError) as exc_info:
            load_config(p)
        msg = str(exc_info.value)
        # Names the failing rule.
        assert "alias 'x' rule 1" in msg
        # Names the file so the user knows what to edit.
        assert str(p) in msg
        # Shows the exact rewrite, not just a vague pointer.
        assert 'target = "end"' in msg
        assert 'start = "09:00"' in msg
        assert 'end = "17:00"' in msg

    def test_migration_error_for_relative_duration(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\naction = "relative_duration"\nduration = "2h"\n',
        )
        with pytest.raises(ConfigError) as exc_info:
            load_config(p)
        assert 'target = "2h"' in str(exc_info.value)

    def test_migration_error_for_absolute_time(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\naction = "absolute_time"\ntime = "16:00"\n',
        )
        with pytest.raises(ConfigError) as exc_info:
            load_config(p)
        assert 'target = "16:00"' in str(exc_info.value)

    def test_migration_error_for_extend_window_notes_no_equivalent(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\nstart = "09:00"\nend = "17:00"\n'
            'action = "extend_window"\nduration = "1h"\n',
        )
        with pytest.raises(ConfigError) as exc_info:
            load_config(p)
        msg = str(exc_info.value)
        assert "no direct equivalent" in msg
        assert 'target = "17:00 + 1h"' in msg

    def test_global_rule_missing_target(self, tmp_path):
        p = _write(tmp_path, '[[rule]]\nstart = "09:00"\n')
        with pytest.raises(ConfigError, match="global rule 1.*missing 'target'"):
            load_config(p)

    def test_target_end_requires_start_and_end(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\ntarget = "end"\n',
        )
        with pytest.raises(ConfigError, match="target='end' requires"):
            load_config(p)

    def test_target_end_with_only_start_rejected(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\nstart = "09:00"\ntarget = "end"\n',
        )
        with pytest.raises(ConfigError, match="target='end' requires"):
            load_config(p)

    def test_unparseable_target_rejected(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\ntarget = "banana"\n',
        )
        with pytest.raises(ConfigError, match="could not be parsed"):
            load_config(p)

    def test_empty_target_rejected(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n[[alias.rule]]\ntarget = ""\n',
        )
        with pytest.raises(ConfigError, match="must be a non-empty string"):
            load_config(p)


class TestFieldValidation:
    def test_invalid_time_format(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\nstart = "25:00"\nend = "17:00"\n'
            'target = "end"\n',
        )
        with pytest.raises(ConfigError, match="invalid time '25:00'"):
            load_config(p)

    def test_invalid_time_format_non_numeric(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\nstart = "noon"\nend = "17:00"\n'
            'target = "end"\n',
        )
        with pytest.raises(ConfigError, match="invalid time 'noon'"):
            load_config(p)

    def test_invalid_day_name(self, tmp_path):
        p = _write(
            tmp_path,
            '[[alias]]\nname = "x"\n'
            '[[alias.rule]]\nstart = "09:00"\nend = "17:00"\n'
            'days = ["Mon", "Funday"]\ntarget = "end"\n',
        )
        with pytest.raises(ConfigError, match="invalid day name"):
            load_config(p)


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


class TestReadmeExample:
    """The TOML example in README.md must load successfully."""

    def _readme_toml(self):
        readme = Path(__file__).parent.parent / "README.md"
        text = readme.read_text()
        # Grab the first ```toml ... ``` fenced block.
        match = re.search(r"```toml\n(.*?)```", text, re.DOTALL)
        assert match, "no TOML block found in README"
        return match.group(1)

    def test_readme_example_loads(self, tmp_path):
        p = tmp_path / "readme.toml"
        p.write_text(self._readme_toml())
        cfg = load_config(p)
        assert set(cfg.aliases) == {"work", "personal", "project"}
        assert len(cfg.global_rules) == 1

    def test_readme_example_work_alias_semantics(self, tmp_path):
        p = tmp_path / "readme.toml"
        p.write_text(self._readme_toml())
        cfg = load_config(p)
        # Work: first rule has window + target=end, second is duration fallback.
        r1, r2 = cfg.aliases["work"]
        assert r1.condition.start == __import__("datetime").time(5, 0)
        assert r1.condition.end == __import__("datetime").time(16, 0)
        assert r1.condition.days == {"Mon", "Tue", "Wed", "Thu", "Fri"}
        assert r1.target == "end"
        assert r2.condition is None
        assert r2.target == "2h"
