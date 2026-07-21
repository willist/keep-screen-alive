## Summary
A cross-platform command line tool that keeps your screen awake using a forward looking relative datetime interface.

On macOS, it uses [caffeinate](https://ss64.com/osx/caffeinate.html). On Linux, it uses systemd-inhibit.

## Install

```bash
$ pip install keep-screen-alive
```

## Examples

```bash
$ date
Wed Jun  1 08:00:00 CDT 2023

$ keep-alive 2h
Keeping alive until 10:00AM CDT, Jun 01, 2023

$ keep-alive 12pm
Keeping alive until 12:00PM CDT, Jun 01, 2023

$ keep-alive 7am
Keeping alive until 07:00AM CDT, Jun 02, 2023
```

## Configuration

Optional TOML config at `$XDG_CONFIG_HOME/keep-alive/config.toml` (defaults to `~/.config/keep-alive/config.toml`). Override with `--config PATH`.

Define named aliases that resolve based on time-of-day and weekday. Each alias is an ordered list of rules; the first matching rule wins. Top-level `[[rule]]` entries are global rules, used as defaults when an alias has no matching rule, and when `keep-alive` is invoked without arguments.

```toml
# global rules: defaults for bare invocation and unmatched aliases
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
    start = "09:00"
    end = "21:00"
    action = "until_window_end"

    [[alias.rule]]
    action = "relative_duration"
    duration = "1h"

[[alias]]
name = "project"

    [[alias.rule]]
    action = "relative_duration"
    duration = "4h"
```

`keep-alive work` on a weekday between 5am and 4pm keeps awake until 4pm; otherwise for 2h. `keep-alive personal` between 9am and 9pm keeps awake until 9pm; otherwise for 1h. `keep-alive project` keeps awake for 4h unconditionally. Bare `keep-alive` uses global rules.

`keep-alive --list` summarizes the loaded config:

```
personal
  09:00-21:00 → until 21:00
  always → for 1h
project
  always → for 4h
work
  Mon, Tue, Wed, Thu, Fri 05:00-16:00 → until 16:00
  always → for 2h
global
  always → for 30m
```

### Actions

| Kind | Required fields | Target |
| --- | --- | --- |
| `relative_duration` | `duration` | now plus duration |
| `absolute_time` | `time` | today at HH:MM |
| `until_window_end` | condition's `end` | today at the window's end |
| `extend_window` | condition's `end`, `duration` | window end plus duration |

Durations are parsed by [dateparser](https://pypi.org/project/dateparser/): `2h`, `30m`, `1h30m`, `1d`, `45 minutes`, etc.

### Conditions

Optional. Omit all fields for an unconditional rule (always matches).

- `start`, `end` - time-of-day window as `HH:MM`. Both must be set to form a range. Start inclusive, end exclusive.
- `days` - list of weekday abbreviations: `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun`. Omit for daily.

### Limitations

- Overnight windows (e.g. `start = "22:00"`, `end = "02:00"`) aren't supported.

## Development

Install [Poetry](https://python-poetry.org/) and [pre-commit](https://pre-commit.com/), then:

```bash
poetry install
pre-commit install
```

Run tests with `poetry run pytest`. Pre-commit hooks run ruff (lint and format) on commit; pytest runs as a pre-push hook. CI runs the same checks on pull requests and on push to `main`.
