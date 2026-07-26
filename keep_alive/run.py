import argparse
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path

import dateparser

from keep_alive.backends import _read_state, get_backend
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
    if args.status:
        _status()
        return
    now = _current_now()
    input_value = " ".join(args.input)
    target = _resolve_target(input_value, config, now)
    _validate_target(target, now)
    if args.dry_run:
        _dry_run(target, now)
        return
    _run_backend(target, now, input_value)


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
    parser.add_argument(
        "--status",
        action="store_true",
        help="show the in-flight keep-alive target, timing, backend, and pid",
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


def _run_backend(target, now, input_value):
    diff = (target - now).seconds
    backend = get_backend()
    backend.cleanup()
    metadata = {
        "input": input_value,
        "start": now.isoformat(),
        "end": target.isoformat(),
    }
    backend.inhibit(diff, metadata)
    print(f"Keeping alive until {target:%I:%M%p %Z, %b %d, %Y}")


# Maps backend class name -> the command ps will report for its inhibit
# process. Used by --status to verify a live PID is still our backend and
# not a reused PID belonging to some other process.
_BACKEND_COMMANDS = {
    "CaffeinateBackend": "caffeinate",
    "SystemdInhibitBackend": "systemd-inhibit",
    "DBusScreenSaverBackend": "dbus-inhibit",
}


def _is_our_process(pid: int, backend_name: str) -> bool:
    """Verify the PID is still our backend process. Returns False if the
    process is dead, reused by another binary, or can't be checked.

    Legacy pidfiles don't carry a backend name, so we accept a match
    against any of the known backend commands in that case.
    """
    import os

    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    candidate_backends = [backend_name] if backend_name else list(_BACKEND_COMMANDS)
    expected_commands = [_BACKEND_COMMANDS[n] for n in candidate_backends if n in _BACKEND_COMMANDS]
    if not expected_commands:
        return False
    import subprocess

    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    actual = result.stdout.strip()
    return any(actual.endswith(cmd) for cmd in expected_commands)


def _status():
    """Print the in-flight keep-alive state, if any. Read-only: does not
    call cleanup(), inhibit(), or modify the state file.

    A live keep-alive run prints six labeled lines covering the original
    input, start time, end time, time remaining, backend class, and pid.
    Anything else (no state file, dead process, PID reused by a non-keep-
    alive binary, or a legacy plain-PID pidfile) prints a clear "no
    keep-alive running" or surfaces what's available with missing fields
    noted.
    """
    state = _read_state()
    if state is None:
        print("no keep-alive running")
        return
    pid = state.get("pid")
    backend_name = state.get("backend", "")
    if not isinstance(pid, int) or not _is_our_process(pid, backend_name):
        print("no keep-alive running")
        return
    if state.get("_legacy"):
        # Old plain-PID pidfile - we only know the PID.
        print("target: (unknown - legacy pidfile)")
        print("start: (unknown)")
        print("end: (unknown)")
        print("remaining: (unknown)")
        print("backend: (unknown)")
        print(f"pid: {pid}")
        return
    try:
        start = datetime.fromisoformat(state["start"]).astimezone()
        end = datetime.fromisoformat(state["end"]).astimezone()
    except (KeyError, ValueError):
        print("no keep-alive running")
        return
    now = _current_now()
    remaining = end - now
    input_value = state.get("input") or "(bare)"
    print(f"target: {input_value}")
    print(f"start: {start:%I:%M%p %Z, %b %d, %Y}")
    print(f"end: {end:%I:%M%p %Z, %b %d, %Y}")
    print(f"remaining: {_format_duration(remaining)}")
    print(f"backend: {backend_name}")
    print(f"pid: {pid}")


if __name__ == "__main__":
    main()
