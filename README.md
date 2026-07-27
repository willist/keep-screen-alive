## Summary
A cross-platform command line tool that keeps your screen awake using a forward looking relative datetime interface.

On macOS, it uses [caffeinate](https://ss64.com/osx/caffeinate.html). On Linux KDE, it uses D-Bus ScreenSaver inhibition (via the `dbus-inhibit` binary). On other Linux desktops and headless systems, it falls back to systemd-inhibit.

## Install

```bash
# uv (recommended)
uv tool install keep-screen-alive

# pipx
pipx install keep-screen-alive
```

For KDE Plasma support (prevents screen lock via D-Bus instead of systemd-inhibit):

```bash
uv tool install 'keep-screen-alive[dbus]'
# or
pipx install 'keep-screen-alive[dbus]'
```

This installs [PyGObject](https://pygobject.readthedocs.io/), which requires `gobject-introspection` and `cairo` system libraries. On Fedora/Bazzite these are pre-installed; on Ubuntu install `libgirepository1.0-dev` and `libcairo2-dev` first.

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

$ keep-alive clear
cleared keep-alive (pid 12345)
```

## Configuration

Optional TOML config at `$XDG_CONFIG_HOME/keep-alive/config.toml` (defaults to `~/.config/keep-alive/config.toml`). Override with `--config PATH`.

Define named aliases that resolve based on time-of-day and weekday. Each alias is an ordered list of rules; the first matching rule wins. Top-level `[[rule]]` entries are global rules, used as defaults when an alias has no matching rule, and when `keep-alive` is invoked without arguments.

```toml
# global rules: defaults for bare invocation and unmatched aliases
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
    start = "09:00"
    end = "21:00"
    target = "end"

    [[alias.rule]]
    target = "1h"

[[alias]]
name = "project"

    [[alias.rule]]
    target = "4h"
```

`keep-alive work` on a weekday between 5am and 4pm keeps awake until 4pm; otherwise for 2h. `keep-alive personal` between 9am and 9pm keeps awake until 9pm; otherwise for 1h. `keep-alive project` keeps awake for 4h unconditionally. Bare `keep-alive` uses global rules.

`keep-alive list` summarizes the loaded config:

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

### Targets

Each rule has a `target` — a dateparser expression that resolves to a datetime. Targets flow through the same resolver as bare CLI input, so anything you can type at the prompt works as a target.

| Target | Resolves to |
| --- | --- |
| `"2h"`, `"30m"`, `"1h30m"` | now plus duration |
| `"4pm"`, `"16:00"` | today at the given time |
| `"end"` (requires `start` + `end`) | today at the condition's window end |

Durations and time-of-day formats are parsed by [dateparser](https://pypi.org/project/dateparser/): `2h`, `30m`, `1h30m`, `1d`, `45 minutes`, `4pm`, `16:00`, etc.

### Conditions

Optional. Omit all fields for an unconditional rule (always matches).

- `start`, `end` - time-of-day window as `HH:MM`. Both must be set to form a range. Start inclusive, end exclusive. Overnight windows (`start > end`) wrap past midnight: `22:00`-`02:00` matches from 10pm to 2am.
- `days` - list of weekday abbreviations: `Mon`, `Tue`, `Wed`, `Thu`, `Fri`, `Sat`, `Sun`. Omit for daily.

### Migration from `action = "..."`

Versions before 0.4 used an `action` field with kinds like `relative_duration` and `until_window_end`. That schema was removed in favor of `target`. To migrate, replace each rule's `action` (and accompanying fields) with a single `target`:

| Old | New |
| --- | --- |
| `action = "relative_duration"`<br>`duration = "2h"` | `target = "2h"` |
| `action = "absolute_time"`<br>`time = "16:00"` | `target = "16:00"` |
| `action = "until_window_end"`<br>(with `start` + `end`) | `target = "end"` |
| `action = "extend_window"`<br>(with `end` + `duration`) | No direct equivalent — use an explicit target expression like `"17:00 + 1h"` |

## Development

Install [Poetry](https://python-poetry.org/) and [pre-commit](https://pre-commit.com/), then:

```bash
poetry install
pre-commit install
pre-commit install --hook-type commit-msg
pre-commit install --hook-type pre-push
```

Run tests with `poetry run pytest`. Pre-commit hooks run ruff (lint and format), pip-audit, and hygiene checks on commit; the commit-msg hook verifies conventional commit format; mypy and pytest run as pre-push hooks. CI runs the same checks on pull requests and on push to `main`.

### Commit format

Commits follow [Conventional Commits](https://www.conventionalcommits.org/). PR titles must match - they become the squash-merge commit subject. Example: `feat: add systemd-inhibit backend`. Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`. Subject max 72 chars. Scope optional: `fix(backends): handle missing caffeinate`.
