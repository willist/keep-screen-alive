import argparse
import re
import sys
import warnings
from pathlib import Path

import dateparser

from keep_alive.backends import get_backend
from keep_alive.config import Config, ConfigError, load_config
from keep_alive.rules import combine, evaluate

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
    if args.dry_run:
        _dry_run(target, now)
        return
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve and print target without engaging the backend",
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
    do = _format_target(rule)
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


# Heuristic: anything that looks like a duration (digits followed by time
# units, possibly compound) formats as "for X"; absolute times format as
# "at X"; "end" formats as "until HH:MM" using the condition's end.
_DURATION_LIKE = re.compile(
    r"^(\d+(\.\d+)?\s*(h|m|s|d|hr|min|sec|hours?|minutes?|seconds?|days?|weeks?|years?)\s*)+$",
    re.IGNORECASE,
)


def _format_target(rule) -> str:
    target = rule.target
    if target == "end":
        if rule.condition and rule.condition.end:
            return f"until {rule.condition.end:%H:%M}"
        return "end"
    if _DURATION_LIKE.match(target):
        return f"for {target}"
    return f"at {target}"


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
        rule = evaluate(alias_rules, now)
        if rule is None:
            rule = evaluate(config.global_rules, now)
        if rule is None:
            if input_value:
                print(f"no rule matched alias '{input_value}'")
            else:
                print("Missing a target")
            sys.exit(1)
        return _resolve_rule_target(rule, now)

    return _parse_target_with_dateparser(input_value, now)


def _resolve_rule_target(rule, now):
    """Resolve a matched rule's target expression to a datetime.

    `target = "end"` binds to `condition.end` on today's date. Anything else
    flows through the same dateparser path as bare CLI input, including the
    bare-time-of-day regression correction from #31.
    """
    if rule.target == "end":
        # Config validation guarantees condition.start + condition.end exist.
        return combine(now, now.date(), rule.condition.end)
    return _parse_target_with_dateparser(rule.target, now)


def _parse_target_with_dateparser(input_value: str, now):
    settings = {**_PARSER_SETTINGS, "RELATIVE_BASE": now}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        later = dateparser.parse(input_value, settings=settings)
    if later is None:
        print("Missing a target")
        sys.exit(1)
    return _correct_future_preference_regression(later, input_value, now)


def _correct_future_preference_regression(later, input_value, now):
    """Pull bare time-of-day inputs back to today when today's slot is upcoming.

    dateparser 1.4.x with PREFER_DATES_FROM="future" pushes bare
    time-of-day inputs (e.g. "4pm") one day forward even when today's
    slot is still ahead. Re-parse without the flag and prefer the
    today result when it lies between now and the flagged result. See
    https://github.com/willist/keep-screen-alive/issues/31.
    """
    if later.date() <= now.date():
        return later
    settings = {k: v for k, v in _PARSER_SETTINGS.items() if k != "PREFER_DATES_FROM"}
    settings["RELATIVE_BASE"] = now
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        today_candidate = dateparser.parse(input_value, settings=settings)
    if today_candidate is None:
        return later
    if today_candidate.date() != later.date() and now < today_candidate < later:
        return today_candidate
    return later


def _validate_target(target, now):
    if now >= target:
        print(f"{target} is in the past. It is currently {now}")
        sys.exit(1)


def _dry_run(target, now):
    """Print what would happen without engaging the backend.

    Calls get_backend() so the dry-run surfaces the same "No suitable
    backend found" exit path a real run would hit, but never calls
    cleanup() or inhibit() on the returned backend.
    """
    duration = target - now
    backend = get_backend()
    print(f"target: {target:%I:%M%p %Z, %b %d, %Y}")
    print(f"duration: {_format_duration(duration)}")
    print(f"backend: {backend.__name__}")


def _run_backend(target, now):
    diff = (target - now).seconds
    backend = get_backend()
    backend.cleanup()
    backend.inhibit(diff)
    print(f"Keeping alive until {target:%I:%M%p %Z, %b %d, %Y}")


if __name__ == "__main__":
    main()
