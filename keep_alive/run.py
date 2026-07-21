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
        "--config", help="path to config file (default: $XDG_CONFIG_HOME/keep-alive/config.toml)"
    )
    return parser.parse_args(argv)


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
    """Resolve the target datetime from an alias or a dateparser expression."""
    if not input_value:
        print("Missing a target")
        sys.exit(1)

    if input_value in config.aliases:
        target = evaluate(config.aliases[input_value], now)
        if target is None:
            target = evaluate(config.global_rules, now)
        if target is None:
            print(f"no rule matched alias '{input_value}'")
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
