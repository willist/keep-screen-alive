import argparse
import sys
import warnings
from pathlib import Path

import dateparser

from keep_alive.backends import get_backend
from keep_alive.config import Config, ConfigError, load_config
from keep_alive.rules import evaluate

_PARSER_SETTINGS = {
    "PREFER_DATES_FROM": "future",
    "RETURN_AS_TIMEZONE_AWARE": True,
}


def main():
    args = _parse_args(sys.argv[1:])
    config = _load_config_or_exit(args.config)
    if args.list:
        _list_config(config)
        return
    now = _current_now()
    input_value = " ".join(args.input)
    target = _resolve_target(input_value, config, now)
    _validate_target(target, now)
    _run_backend(target, now)


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="keep-alive",
        description="Keep your screen awake until a target time or alias window.",
    )
    parser.add_argument("input", nargs="*", help="alias name from config, or datetime expression")
    parser.add_argument(
        "--config",
        help="path to config file (default: $XDG_CONFIG_HOME/keep-alive/config.toml)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list configured aliases and exit",
    )
    return parser.parse_args(argv)


def _list_config(config: Config) -> None:
    """Print configured aliases with their rules, then return."""
    for name in sorted(config.aliases):
        print(name)
        for rule in config.aliases[name]:
            print(f"  {_format_rule(rule)}")
    print("global")
    for rule in config.global_rules:
        print(f"  {_format_rule(rule)}")


def _format_rule(rule) -> str:
    when = _format_condition(rule.condition)
    do = _format_action(rule.action, rule.condition)
    return f"{when} → {do}"


def _format_condition(condition) -> str:
    if condition is None:
        return "always"
    parts = []
    if condition.days is not None:
        parts.append(_format_days(condition.days))
    if condition.start is not None and condition.end is not None:
        parts.append(f"{condition.start:%H:%M}-{condition.end:%H:%M}")
    elif condition.start is not None:
        parts.append(f"from {condition.start:%H:%M}")
    elif condition.end is not None:
        parts.append(f"until {condition.end:%H:%M}")
    return " ".join(parts) if parts else "always"


def _format_action(action, condition) -> str:
    if action.kind == "relative_duration":
        return f"for {_format_duration(action.duration)}"
    if action.kind == "absolute_time":
        return f"at {action.time:%H:%M}"
    end_str = f"{condition.end:%H:%M}" if condition and condition.end else "?"
    if action.kind == "until_window_end":
        return f"until {end_str}"
    if action.kind == "extend_window":
        return f"until {end_str} + {_format_duration(action.duration)}"
    return action.kind


def _format_days(days) -> str:
    if len(days) == 7:
        return "daily"
    order = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
    sorted_days = sorted(days, key=lambda d: order[d])
    return ", ".join(sorted_days)


def _format_duration(td) -> str:
    total = int(td.total_seconds())
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return "".join(parts) or "0m"


def _load_config_or_exit(path: str | None) -> Config:
    try:
        return load_config(Path(path) if path else None)
    except ConfigError as e:
        print(f"config error: {e}")
        sys.exit(1)


def _current_now():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return dateparser.parse("now", settings=_PARSER_SETTINGS)


def _resolve_target(input_value: str, config: Config, now) -> object:
    """Resolve the target datetime from an alias or a dateparser expression.

    Empty input is treated as a bare invocation: global rules apply as
    defaults, and the call exits with "Missing a target" only if no global
    rule matches either.
    """
    if not input_value or input_value in config.aliases:
        alias_rules = config.aliases.get(input_value, [])
        target = evaluate(alias_rules, now)
        if target is None:
            target = evaluate(config.global_rules, now)
        if target is None:
            if input_value:
                print(f"no rule matched alias '{input_value}'")
            else:
                print("Missing a target")
            sys.exit(1)
        return target

    return _parse_target_with_dateparser(input_value, now)


def _parse_target_with_dateparser(input_value: str, now):
    settings = {**_PARSER_SETTINGS, "RELATIVE_BASE": now}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        later = dateparser.parse(input_value, settings=settings)
    if later is None:
        print("Missing a target")
        sys.exit(1)
    return later


def _validate_target(target, now):
    if now >= target:
        print(f"{target} is in the past. It is currently {now}")
        sys.exit(1)


def _run_backend(target, now):
    diff = (target - now).seconds
    backend = get_backend()
    backend.cleanup()
    backend.inhibit(diff)
    print(f"Keeping alive until {target:%I:%M%p %Z, %b %d, %Y}")


if __name__ == "__main__":
    main()
