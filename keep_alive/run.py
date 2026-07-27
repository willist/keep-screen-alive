import contextlib
import os
import re
import signal
import sys
import warnings
from datetime import datetime, timedelta
from importlib.metadata import version as _pkg_version
from pathlib import Path

import click
import dateparser
from click.shell_completion import CompletionItem

from keep_alive.backends import _pidfile_path, _read_state, get_backend
from keep_alive.config import Config, ConfigError, load_config
from keep_alive.rules import combine, evaluate

_PARSER_SETTINGS = {
    "PREFER_DATES_FROM": "future",
    "RETURN_AS_TIMEZONE_AWARE": True,
}


def _load_config_safe(ctx):
    """Load config during shell completion without exiting on error."""
    try:
        config_path = ctx.params.get("config_path")
        if config_path is None and ctx.parent:
            config_path = ctx.parent.params.get("config_path")
        return load_config(Path(config_path) if config_path else None)
    except Exception:
        return None


def _complete_aliases(ctx, param, incomplete):
    """Return alias names from config as completion suggestions."""
    config = _load_config_safe(ctx)
    if config is None:
        return []
    return [CompletionItem(name) for name in sorted(config.aliases) if name.startswith(incomplete)]


class DefaultGroup(click.Group):
    """A click Group that falls back to a default subcommand when the
    first positional argument isn't a known subcommand name.

    This preserves the existing UX where ``keep-alive 2h`` is shorthand
    for ``keep-alive run 2h``.
    """

    def __init__(self, *args, **kwargs):
        self.default_cmd_name = kwargs.pop("default", "")
        super().__init__(*args, **kwargs)

    def parse_args(self, ctx, args):
        if ctx.resilient_parsing:
            return super().parse_args(ctx, args)
        args = list(args)
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--":
                args.insert(0, self.default_cmd_name)
                break
            if arg.startswith("-"):
                if arg == "--config" and i + 1 < len(args):
                    i += 2
                else:
                    i += 1
                continue
            if arg not in self.commands:
                args.insert(0, self.default_cmd_name)
            break
        else:
            if not args or not any(a in ("--version", "--help", "-h") for a in args):
                args.insert(0, self.default_cmd_name)
        return super().parse_args(ctx, args)

    def shell_complete(self, ctx, incomplete):
        results = super().shell_complete(ctx, incomplete)
        config = _load_config_safe(ctx)
        if config:
            results.extend(
                CompletionItem(name)
                for name in sorted(config.aliases)
                if name.startswith(incomplete)
            )
        return results


@click.group(
    cls=DefaultGroup,
    default="run",
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--config",
    "config_path",
    default=None,
    help="path to config file (default: $XDG_CONFIG_HOME/keep-alive/config.toml)",
)
@click.version_option(
    version=_pkg_version("keep-screen-alive"),
    prog_name="keep-alive",
)
@click.pass_context
def cli(ctx, config_path):
    ctx.ensure_object(dict)
    ctx.obj["config"] = config_path


@cli.command("run")
@click.option(
    "--config",
    "config_path",
    default=None,
    help="path to config file (default: $XDG_CONFIG_HOME/keep-alive/config.toml)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="resolve and print target without engaging the backend",
)
@click.option(
    "--list",
    "list_flag",
    is_flag=True,
    hidden=True,
    help="list configured aliases and exit (deprecated, use 'keep-alive list')",
)
@click.argument("input", nargs=-1, shell_complete=_complete_aliases)
@click.pass_context
def run_cmd(ctx, config_path, dry_run, list_flag, input):
    config = _load_config_or_exit(config_path if config_path is not None else ctx.obj.get("config"))
    if list_flag:
        click.echo(
            "warning: --list is deprecated; use 'keep-alive list' instead "
            "(will be removed in v1.0)",
            err=True,
        )
        _list_config(config)
        return
    now = _current_now()
    input_value = " ".join(input)
    target = _resolve_target(input_value, config, now)
    _validate_target(target, now)
    if dry_run:
        _dry_run(target, now)
        return
    _run_backend(target, now, input_value)


@cli.command("list", context_settings={"ignore_unknown_options": True})
@click.option(
    "--config",
    "config_path",
    default=None,
    help="path to config file (default: $XDG_CONFIG_HOME/keep-alive/config.toml)",
)
@click.argument("input", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def list_cmd(ctx, config_path, input):
    config = _load_config_or_exit(config_path if config_path is not None else ctx.obj.get("config"))
    _list_config(config)


@cli.command("status", context_settings={"ignore_unknown_options": True})
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def status_cmd(args):
    _status()


@cli.command("clear")
def clear_cmd():
    _clear()


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

    `target = "end"` binds to `condition.end`. For overnight windows the
    end time may fall on tomorrow (e.g., 22:00-02:00 at 23:00 resolves to
    tomorrow 02:00). Anything else flows through the same dateparser path
    as bare CLI input, including the bare-time-of-day regression
    correction from #31.
    """
    if rule.target == "end":
        # Config validation guarantees condition.start + condition.end exist.
        candidate = combine(now, now.date(), rule.condition.end)
        if candidate <= now:
            candidate = combine(now, now.date() + timedelta(days=1), rule.condition.end)
        return candidate
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


def _clear():
    """Kill the in-flight keep-alive process and remove the state file.

    Uses the same liveness check as _is_our_process so a PID reused by
    another process is never killed. Stale state files (dead PID, reused
    PID) are cleaned up. Prints a confirmation when a process was killed.
    """
    state = _read_state()
    if state is None:
        print("no keep-alive running")
        return
    pid = state.get("pid")
    backend_name = state.get("backend", "")
    if not isinstance(pid, int) or not _is_our_process(pid, backend_name):
        _pidfile_path().unlink(missing_ok=True)
        print("no keep-alive running")
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pid, signal.SIGTERM)
    _pidfile_path().unlink(missing_ok=True)
    print(f"cleared keep-alive (pid {pid})")


def main():
    cli(prog_name="keep-alive")


if __name__ == "__main__":
    main()
