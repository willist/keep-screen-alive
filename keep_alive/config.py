"""Config loading and validation for alias rules.

Reads a TOML config file from $XDG_CONFIG_HOME/keep-alive/config.toml (or
~/.config/keep-alive/config.toml if XDG_CONFIG_HOME is unset), validates it,
and returns a Config with parsed Rule objects from keep_alive.rules.

TOML shape:

    [[rule]]                       # global rules
    action = "relative_duration"
    duration = "30m"

    [[alias]]
    name = "work"

        [[alias.rule]]
        start = "05:00"
        end   = "16:00"
        days  = ["Mon", "Tue", "Wed", "Thu", "Fri"]
        action = "until_window_end"

        [[alias.rule]]
        action = "relative_duration"
        duration = "2h"

Missing config file returns an empty Config. Invalid config raises
ConfigError with a message naming the offending field.
"""

from __future__ import annotations

import os
import tomllib
import warnings
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import dateparser

from keep_alive.rules import WEEKDAY_SET, Action, Condition, Rule

VALID_ACTION_KINDS = frozenset(
    {"until_window_end", "extend_window", "relative_duration", "absolute_time"}
)


@dataclass
class Config:
    aliases: dict[str, list[Rule]] = field(default_factory=dict)
    global_rules: list[Rule] = field(default_factory=list)


class ConfigError(Exception):
    """Raised when config loading or validation fails."""


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
        raise ConfigError(f"invalid TOML in {config_path}: {e}") from e
    return _parse_config(data)


def _parse_config(data: dict[str, Any]) -> Config:
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

        raw_rules = alias_dict.get("rule", [])
        if not isinstance(raw_rules, list):
            raise ConfigError(f"alias '{name}': 'rule' must be an array of tables")
        rules = [
            _parse_rule(r, context=f"alias '{name}' rule {j + 1}") for j, r in enumerate(raw_rules)
        ]
        aliases[name] = rules

    raw_globals = data.get("rule", [])
    if not isinstance(raw_globals, list):
        raise ConfigError("'rule' must be an array of tables (use [[rule]])")
    global_rules = [
        _parse_rule(r, context=f"global rule {j + 1}") for j, r in enumerate(raw_globals)
    ]

    return Config(aliases=aliases, global_rules=global_rules)


def _parse_rule(d: dict[str, Any], context: str) -> Rule:
    if not isinstance(d, dict):
        raise ConfigError(f"{context}: must be a table")
    if "action" not in d:
        raise ConfigError(f"{context}: missing 'action'")
    condition = _parse_condition(d, context)
    action = _parse_action(d, context)
    return Rule(condition=condition, action=action)


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


def _parse_action(d: dict[str, Any], context: str) -> Action:
    kind = d["action"]
    if kind not in VALID_ACTION_KINDS:
        raise ConfigError(
            f"{context}: invalid action '{kind}'; must be one of {sorted(VALID_ACTION_KINDS)}"
        )

    duration = _parse_duration(d["duration"], f"{context}: 'duration'") if "duration" in d else None
    abs_time = _parse_time(d["time"], f"{context}: 'time'") if "time" in d else None

    if kind in ("until_window_end", "extend_window") and "end" not in d:
        raise ConfigError(f"{context}: action '{kind}' requires 'end'")
    if kind == "extend_window" and duration is None:
        raise ConfigError(f"{context}: action 'extend_window' requires 'duration'")
    if kind == "relative_duration" and duration is None:
        raise ConfigError(f"{context}: action 'relative_duration' requires 'duration'")
    if kind == "absolute_time" and abs_time is None:
        raise ConfigError(f"{context}: action 'absolute_time' requires 'time'")

    return Action(kind=kind, duration=duration, time=abs_time)


def _parse_time(s: str, context: str) -> time:
    if not isinstance(s, str):
        raise ConfigError(f"{context}: must be a string, got {type(s).__name__}")
    try:
        return datetime.strptime(s, "%H:%M").time()
    except ValueError:
        raise ConfigError(f"{context}: invalid time '{s}', must be HH:MM")


def _parse_duration(s: str, context: str) -> timedelta:
    if not isinstance(s, str):
        raise ConfigError(f"{context}: must be a string, got {type(s).__name__}")
    base = datetime(2000, 1, 1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        future = dateparser.parse(
            s, settings={"RELATIVE_BASE": base, "PREFER_DATES_FROM": "future"}
        )
    if future is None:
        raise ConfigError(f"{context}: invalid duration '{s}'")
    delta = future - base
    if delta.total_seconds() <= 0:
        raise ConfigError(f"{context}: duration '{s}' must be positive")
    return delta
