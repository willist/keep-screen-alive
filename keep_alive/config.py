"""Config loading and validation for alias rules.

Reads a TOML config file from $XDG_CONFIG_HOME/keep-alive/config.toml (or
~/.config/keep-alive/config.toml if XDG_CONFIG_HOME is unset), validates it,
and returns a Config with parsed Rule objects from keep_alive.rules.

TOML shape:

    [[rule]]                       # global rules
    target = "30m"

    [[alias]]
    name = "work"

        [[alias.rule]]
        start = "05:00"
        end   = "16:00"
        days  = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        target = "end"

        [[alias.rule]]
        target = "2h"

Missing config file returns an empty Config. Invalid config raises
ConfigError with a message naming the offending field.
"""

from __future__ import annotations

import os
import tomllib
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import dateparser

from keep_alive.rules import WEEKDAY_SET, Condition, Rule


@dataclass
class Config:
    aliases: dict[str, list[Rule]] = field(default_factory=dict)
    global_rules: list[Rule] = field(default_factory=list)


class ConfigError(Exception):
    """Raised when config loading or validation fails."""


RESERVED_NAMES = frozenset({"list", "status", "clear"})


def default_config_path() -> Path:
    """Resolve the default config path from $XDG_CONFIG_HOME or ~/.config."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "keep-alive" / "config.toml"


def load_config(path: Path | str | None = None) -> Config:
    """Load and validate config from the given path or the default location.

    Missing file returns an empty Config. Invalid TOML or failed validation
    raises ConfigError.
    """
    config_path = Path(path) if path is not None else default_config_path()
    if not config_path.exists():
        return Config()
    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {config_path}: {e}") from None
    return _parse_config(data, config_path)


def _parse_config(data: dict[str, Any], config_path: Path | None = None) -> Config:
    raw_aliases = data.get("alias", [])
    if not isinstance(raw_aliases, list):
        raise ConfigError("'alias' must be an array of tables (use [[alias]])")

    aliases: dict[str, list[Rule]] = {}
    for i, alias_dict in enumerate(raw_aliases):
        if not isinstance(alias_dict, dict):
            raise ConfigError(f"alias #{i + 1}: must be a table")
        name = alias_dict.get("name")
        if not name:
            raise ConfigError(f"alias #{i + 1}: missing 'name'")
        if name in aliases:
            raise ConfigError(f"duplicate alias name '{name}'")
        if name in RESERVED_NAMES:
            raise ConfigError(
                f"alias name '{name}' is reserved (used by a subcommand); choose a different name"
            )

        raw_rules = alias_dict.get("rule", [])
        if not isinstance(raw_rules, list):
            raise ConfigError(f"alias '{name}': 'rule' must be an array of tables")
        rules = [
            _parse_rule(r, context=f"alias '{name}' rule {j + 1}", config_path=config_path)
            for j, r in enumerate(raw_rules)
        ]
        aliases[name] = rules

    raw_globals = data.get("rule", [])
    if not isinstance(raw_globals, list):
        raise ConfigError("'rule' must be an array of tables (use [[rule]])")
    global_rules = [
        _parse_rule(r, context=f"global rule {j + 1}", config_path=config_path)
        for j, r in enumerate(raw_globals)
    ]

    return Config(aliases=aliases, global_rules=global_rules)


def _parse_rule(d: dict[str, Any], context: str, config_path: Path | None = None) -> Rule:
    if not isinstance(d, dict):
        raise ConfigError(f"{context}: must be a table")
    if "action" in d:
        raise _migration_error(d, context, config_path)
    if "target" not in d:
        raise ConfigError(f"{context}: missing 'target'")
    target = d["target"]
    if not isinstance(target, str) or not target.strip():
        raise ConfigError(f"{context}: 'target' must be a non-empty string")

    condition = _parse_condition(d, context)

    if target == "end":
        if condition is None or condition.start is None or condition.end is None:
            raise ConfigError(
                f"{context}: target='end' requires condition with both 'start' and 'end'"
            )
    else:
        _validate_target_expression(target, context)

    return Rule(condition=condition, target=target)


def _parse_condition(d: dict[str, Any], context: str) -> Condition | None:
    has_start, has_end, has_days = "start" in d, "end" in d, "days" in d
    if not (has_start or has_end or has_days):
        return None

    start = _parse_time(d["start"], f"{context}: 'start'") if has_start else None
    end = _parse_time(d["end"], f"{context}: 'end'") if has_end else None

    days = None
    if has_days:
        raw_days = d["days"]
        if not isinstance(raw_days, list):
            raise ConfigError(f"{context}: 'days' must be a list")
        days = set(raw_days)
        invalid = days - WEEKDAY_SET
        if invalid:
            raise ConfigError(
                f"{context}: invalid day name(s) {sorted(invalid)}; valid: {sorted(WEEKDAY_SET)}"
            )

    return Condition(start=start, end=end, days=days)


def _parse_time(s: str, context: str):
    if not isinstance(s, str):
        raise ConfigError(f"{context}: must be a string, got {type(s).__name__}")
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        raise ConfigError(f"{context}: invalid time '{s}', must be HH:MM") from None


def _validate_target_expression(s: str, context: str) -> None:
    """Smoke-test the target expression with dateparser so config load fails
    on obviously broken inputs like `target = "banana"`.

    Syntax check only — actual resolution happens at runtime against a real
    `now`. Inputs dateparser can't make sense of are rejected here.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        probe = dateparser.parse(
            s,
            settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": True},
        )
    if probe is None:
        raise ConfigError(f"{context}: target '{s}' could not be parsed as a time expression")


def _migration_error(d: dict[str, Any], context: str, config_path: Path | None) -> ConfigError:
    """Build an actionable error showing the exact TOML rewrite for the
    failing rule. Called when an old-schema `action` field is detected.
    """
    kind = d.get("action", "<unknown>")
    rewrite_lines = []

    # Preserve condition fields (start/end/days) in their original form.
    for key in ("start", "end", "days"):
        if key in d:
            rewrite_lines.append(_toml_field(key, d[key]))

    # Suggest the new `target` line based on the old action kind.
    if kind == "relative_duration":
        duration = d.get("duration", "<duration>")
        rewrite_lines.append(f'target = "{duration}"')
    elif kind == "absolute_time":
        time_val = d.get("time", "<time>")
        rewrite_lines.append(f'target = "{time_val}"')
    elif kind == "until_window_end":
        rewrite_lines.append('target = "end"')
    elif kind == "extend_window":
        end_val = d.get("end", "<end>")
        duration = d.get("duration", "<duration>")
        rewrite_lines.append(
            f'# "extend_window" has no direct equivalent. Try: target = "{end_val} + {duration}"'
        )
    else:
        rewrite_lines.append(f"# unknown action kind {kind!r}; see README migration table")

    suggestion = "\n".join(f"    {line}" for line in rewrite_lines)
    path_line = f"\nConfig file: {config_path}\n" if config_path else "\n"
    message = (
        f"{context}: 'action' field was removed in favor of 'target'.\n"
        f"\n"
        f"Rewrite this rule as:\n"
        f"\n"
        f"{suggestion}\n"
        f"{path_line}"
        f"See the README migration table for the full set of translations."
    )
    return ConfigError(message.rstrip())


def _toml_field(key: str, value: Any) -> str:
    """Render a key/value pair as a single TOML line."""
    if isinstance(value, str):
        return f'{key} = "{value}"'
    if isinstance(value, list):
        inner = ", ".join(f'"{v}"' for v in value)
        return f"{key} = [{inner}]"
    return f"{key} = {value}"
