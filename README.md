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

## Development

Install [Poetry](https://python-poetry.org/) and [pre-commit](https://pre-commit.com/), then:

```bash
poetry install
pre-commit install
```

Run tests with `poetry run pytest`. Pre-commit hooks run ruff (lint and format) on commit; pytest runs as a pre-push hook. CI runs the same checks on pull requests and on push to `main`.
