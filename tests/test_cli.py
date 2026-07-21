from datetime import UTC, datetime, time, timedelta
from unittest.mock import MagicMock

import pytest

from keep_alive import run
from keep_alive.config import Config, ConfigError
from keep_alive.rules import Action, Condition, Rule

# 2024-01-15 is a Monday. Fixed timestamp = Monday 2024-01-15 12:00 UTC.
PINNED_NOW = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)


def _duration_rule(duration):
    return Rule(
        condition=None,
        action=Action(kind="relative_duration", duration=duration),
    )


@pytest.fixture
def mock_now(monkeypatch):
    monkeypatch.setattr("keep_alive.run._current_now", lambda: PINNED_NOW)


@pytest.fixture
def mock_backend(monkeypatch):
    backend = MagicMock()
    monkeypatch.setattr("keep_alive.run.get_backend", lambda: backend)
    return backend


@pytest.fixture
def mock_config_loader(monkeypatch):
    """Replace _load_config_or_exit with a loader that returns a fixed Config.

    Returns a mutable dict so individual tests can stash a config for the
    loader to return.
    """
    stash = {"config": Config()}

    def fake_loader(path):
        return stash["config"]

    monkeypatch.setattr("keep_alive.run._load_config_or_exit", fake_loader)
    return stash


# ---------------------------------------------------------------------
# _resolve_target: pure-function tests
# ---------------------------------------------------------------------


class TestResolveTarget:
    def test_alias_match_returns_target(self):
        config = Config(
            aliases={"work": [_duration_rule(timedelta(hours=2))]},
        )
        target = run._resolve_target("work", config, PINNED_NOW)
        assert target == PINNED_NOW + timedelta(hours=2)

    def test_alias_miss_falls_through_to_global(self):
        config = Config(
            aliases={
                # Window already ended at 10:00; current time is 12:00
                "work": [
                    Rule(
                        condition=Condition(start=time(9, 0), end=time(10, 0)),
                        action=Action(kind="until_window_end"),
                    )
                ]
            },
            global_rules=[_duration_rule(timedelta(hours=1))],
        )
        target = run._resolve_target("work", config, PINNED_NOW)
        assert target == PINNED_NOW + timedelta(hours=1)

    def test_alias_miss_no_global_exits(self):
        config = Config(
            aliases={
                "work": [
                    Rule(
                        condition=Condition(start=time(9, 0), end=time(10, 0)),
                        action=Action(kind="until_window_end"),
                    )
                ]
            },
        )
        with pytest.raises(SystemExit):
            run._resolve_target("work", config, PINNED_NOW)

    def test_alias_miss_no_global_prints_alias_name(self, capsys):
        config = Config(
            aliases={
                "work": [
                    Rule(
                        condition=Condition(start=time(9, 0), end=time(10, 0)),
                        action=Action(kind="until_window_end"),
                    )
                ]
            },
        )
        with pytest.raises(SystemExit):
            run._resolve_target("work", config, PINNED_NOW)
        assert "no rule matched alias 'work'" in capsys.readouterr().out

    def test_non_alias_input_uses_dateparser(self):
        config = Config()
        target = run._resolve_target("2h", config, PINNED_NOW)
        assert target == PINNED_NOW + timedelta(hours=2)

    def test_non_alias_unparseable_input_exits(self, capsys):
        config = Config()
        with pytest.raises(SystemExit):
            run._resolve_target("banana", config, PINNED_NOW)
        assert "Missing a target" in capsys.readouterr().out

    def test_empty_input_with_no_globals_exits(self, capsys):
        config = Config()
        with pytest.raises(SystemExit):
            run._resolve_target("", config, PINNED_NOW)
        assert "Missing a target" in capsys.readouterr().out

    def test_empty_input_uses_global_rules(self):
        config = Config(
            global_rules=[_duration_rule(timedelta(minutes=30))],
        )
        target = run._resolve_target("", config, PINNED_NOW)
        assert target == PINNED_NOW + timedelta(minutes=30)


# ---------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------


class TestParseArgs:
    def test_parses_positional_input(self):
        args = run._parse_args(["2h"])
        assert args.input == ["2h"]
        assert args.config is None

    def test_joins_multi_word_input(self):
        args = run._parse_args(["12pm", "tomorrow"])
        assert args.input == ["12pm", "tomorrow"]

    def test_parses_config_flag(self):
        args = run._parse_args(["--config", "/foo.toml", "work"])
        assert args.config == "/foo.toml"
        assert args.input == ["work"]

    def test_config_flag_without_input(self):
        args = run._parse_args(["--config", "/foo.toml"])
        assert args.config == "/foo.toml"
        assert args.input == []

    def test_no_args_returns_empty_input(self):
        args = run._parse_args([])
        assert args.input == []


# ---------------------------------------------------------------------
# main: end-to-end with mocked now, backend, and config
# ---------------------------------------------------------------------


class TestMain:
    def _run_main(self, monkeypatch, argv):
        monkeypatch.setattr("sys.argv", ["keep-alive"] + argv)
        run.main()

    def test_alias_runs_backend(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={"work": [_duration_rule(timedelta(hours=2))]},
        )
        self._run_main(monkeypatch, ["work"])

        assert mock_backend.cleanup.called
        mock_backend.inhibit.assert_called_once_with(7200)
        assert "Keeping alive until" in capsys.readouterr().out

    def test_dateparser_input_runs_backend(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        self._run_main(monkeypatch, ["1h"])
        mock_backend.inhibit.assert_called_once_with(3600)

    def test_global_rule_used_when_alias_misses(
        self, monkeypatch, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={
                "work": [
                    Rule(
                        condition=Condition(start=time(9, 0), end=time(10, 0)),
                        action=Action(kind="until_window_end"),
                    )
                ]
            },
            global_rules=[_duration_rule(timedelta(minutes=30))],
        )
        self._run_main(monkeypatch, ["work"])
        # 30 minutes = 1800 seconds
        mock_backend.inhibit.assert_called_once_with(1800)

    def test_alias_no_match_exits_nonzero(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={
                "work": [
                    Rule(
                        condition=Condition(start=time(9, 0), end=time(10, 0)),
                        action=Action(kind="until_window_end"),
                    )
                ]
            },
        )
        with pytest.raises(SystemExit) as exc:
            self._run_main(monkeypatch, ["work"])
        assert exc.value.code != 0
        assert "no rule matched alias 'work'" in capsys.readouterr().out
        assert not mock_backend.inhibit.called

    def test_missing_input_exits(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        with pytest.raises(SystemExit):
            self._run_main(monkeypatch, [])
        assert "Missing a target" in capsys.readouterr().out
        assert not mock_backend.inhibit.called

    def test_config_error_exits_nonzero(self, monkeypatch, capsys, mock_now):
        def raising_load(path):
            raise ConfigError("bad config")

        # Patch the lower-level load_config so _load_config_or_exit's
        # handling is exercised for real.
        monkeypatch.setattr("keep_alive.run.load_config", raising_load)
        with pytest.raises(SystemExit) as exc:
            self._run_main(monkeypatch, ["work"])
        assert exc.value.code != 0
        assert "config error: bad config" in capsys.readouterr().out


# ---------------------------------------------------------------------
# main: --config flag plumbing
# ---------------------------------------------------------------------


class TestConfigFlag:
    def test_config_flag_passed_to_loader(self, monkeypatch, mock_now, mock_backend):
        captured = {"path": "unset"}

        def capturing_loader(path):
            captured["path"] = path
            return Config()

        monkeypatch.setattr("keep_alive.run._load_config_or_exit", capturing_loader)
        # Use an unparseable input so we exit before backend runs - but we
        # need a valid alias path. Easier: pass an alias that doesn't exist
        # and check we got past the loader.
        monkeypatch.setattr("sys.argv", ["keep-alive", "--config", "/foo.toml", "2h"])
        run.main()
        # _load_config_or_exit receives the raw string; it converts to Path
        # internally before calling load_config.
        assert captured["path"] == "/foo.toml"

    def test_no_config_flag_passes_none(self, monkeypatch, mock_now, mock_backend):
        captured = {"path": "unset"}

        def capturing_loader(path):
            captured["path"] = path
            return Config()

        monkeypatch.setattr("keep_alive.run._load_config_or_exit", capturing_loader)
        monkeypatch.setattr("sys.argv", ["keep-alive", "2h"])
        run.main()
        assert captured["path"] is None


# ---------------------------------------------------------------------
# --list command
# ---------------------------------------------------------------------


class TestList:
    def _run_main(self, monkeypatch, argv):
        monkeypatch.setattr("sys.argv", ["keep-alive"] + argv)
        run.main()

    def test_list_with_empty_config(self, monkeypatch, capsys, mock_backend, mock_config_loader):
        mock_config_loader["config"] = Config()
        self._run_main(monkeypatch, ["--list"])
        out = capsys.readouterr().out
        assert out.strip() == "global"
        assert not mock_backend.inhibit.called

    def test_list_with_multiple_aliases(
        self, monkeypatch, capsys, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={
                "work": [
                    Rule(
                        condition=Condition(
                            start=time(5, 0),
                            end=time(16, 0),
                            days={"Mon", "Tue", "Wed", "Thu", "Fri"},
                        ),
                        action=Action(kind="until_window_end"),
                    ),
                    Rule(
                        condition=None,
                        action=Action(kind="relative_duration", duration=timedelta(hours=2)),
                    ),
                ],
                "personal": [
                    Rule(
                        condition=Condition(start=time(5, 0), end=time(19, 0)),
                        action=Action(kind="absolute_time", time=time(16, 0)),
                    ),
                ],
            },
            global_rules=[
                Rule(
                    condition=None,
                    action=Action(kind="relative_duration", duration=timedelta(minutes=30)),
                )
            ],
        )
        self._run_main(monkeypatch, ["--list"])
        out = capsys.readouterr().out
        # Aliases sorted alphabetically, global last. Each rule summarizes
        # its condition and action on one indented line.
        expected = (
            "personal\n"
            "  05:00-19:00 → at 16:00\n"
            "work\n"
            "  Mon, Tue, Wed, Thu, Fri 05:00-16:00 → until 16:00\n"
            "  always → for 2h\n"
            "global\n"
            "  always → for 30m"
        )
        assert out.strip() == expected
        assert not mock_backend.inhibit.called

    def test_list_respects_config_flag(self, monkeypatch, capsys, mock_backend, mock_now, tmp_path):
        config_file = tmp_path / "test.toml"
        config_file.write_text(
            '[[alias]]\nname = "fromfile"\n'
            '[[alias.rule]]\naction = "relative_duration"\nduration = "30m"\n'
        )
        monkeypatch.setattr("sys.argv", ["keep-alive", "--list", "--config", str(config_file)])
        run.main()
        out = capsys.readouterr().out
        assert "fromfile" in out
        assert "for 30m" in out

    def test_list_takes_precedence_over_input(
        self, monkeypatch, capsys, mock_backend, mock_config_loader
    ):
        # --list with a positional input should list, not resolve
        mock_config_loader["config"] = Config(
            aliases={"work": [_duration_rule(timedelta(hours=2))]},
        )
        self._run_main(monkeypatch, ["--list", "work"])
        out = capsys.readouterr().out
        assert "work" in out
        assert "for 2h" in out
        assert not mock_backend.inhibit.called


class TestListFormatting:
    """Unit tests for the rule summarizers behind --list."""

    @pytest.mark.parametrize(
        "condition,expected",
        [
            (None, "always"),
            (Condition(), "always"),
            (Condition(start=time(9, 0), end=time(17, 0)), "09:00-17:00"),
            (Condition(start=time(9, 0)), "from 09:00"),
            (Condition(end=time(17, 0)), "until 17:00"),
            (Condition(days={"Mon", "Tue", "Wed"}), "Mon, Tue, Wed"),
            (
                Condition(
                    start=time(5, 0),
                    end=time(16, 0),
                    days={"Mon", "Tue", "Wed", "Thu", "Fri"},
                ),
                "Mon, Tue, Wed, Thu, Fri 05:00-16:00",
            ),
            (
                Condition(days={"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}),
                "daily",
            ),
        ],
    )
    def test_format_condition(self, condition, expected):
        assert run._format_condition(condition) == expected

    @pytest.mark.parametrize(
        "action,condition,expected",
        [
            (
                Action(kind="relative_duration", duration=timedelta(hours=2)),
                None,
                "for 2h",
            ),
            (
                Action(kind="absolute_time", time=time(16, 0)),
                None,
                "at 16:00",
            ),
            (
                Action(kind="until_window_end"),
                Condition(start=time(9, 0), end=time(17, 0)),
                "until 17:00",
            ),
            (
                Action(kind="extend_window", duration=timedelta(hours=1)),
                Condition(start=time(9, 0), end=time(17, 0)),
                "until 17:00 + 1h",
            ),
        ],
    )
    def test_format_action(self, action, condition, expected):
        assert run._format_action(action, condition) == expected

    @pytest.mark.parametrize(
        "td,expected",
        [
            (timedelta(0), "0m"),
            (timedelta(minutes=30), "30m"),
            (timedelta(hours=2), "2h"),
            (timedelta(hours=1, minutes=30), "1h30m"),
            (timedelta(days=1), "1d"),
            (timedelta(days=1, hours=2, minutes=15), "1d2h15m"),
        ],
    )
    def test_format_duration(self, td, expected):
        assert run._format_duration(td) == expected
