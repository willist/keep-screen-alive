import sys
from datetime import UTC, datetime, time, timedelta
from unittest.mock import ANY, MagicMock

import pytest

from keep_alive import run
from keep_alive.config import Config, ConfigError
from keep_alive.rules import Condition, Rule

# 2024-01-15 is a Monday. Fixed timestamp = Monday 2024-01-15 12:00 UTC.
PINNED_NOW = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)


def _target_rule(target):
    return Rule(
        condition=None,
        target=target,
    )


@pytest.fixture
def mock_now(monkeypatch):
    monkeypatch.setattr("keep_alive.run._current_now", lambda: PINNED_NOW)


@pytest.fixture
def mock_backend(monkeypatch):
    backend = MagicMock()
    backend.__name__ = "MockBackend"
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
            aliases={"work": [_target_rule("2h")]},
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
                        target="end",
                    )
                ]
            },
            global_rules=[_target_rule("1h")],
        )
        target = run._resolve_target("work", config, PINNED_NOW)
        assert target == PINNED_NOW + timedelta(hours=1)

    def test_alias_miss_no_global_exits(self):
        config = Config(
            aliases={
                "work": [
                    Rule(
                        condition=Condition(start=time(9, 0), end=time(10, 0)),
                        target="end",
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
                        target="end",
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

    def test_bare_future_time_of_day_stays_on_today(self):
        # Regression for #31: at noon, 4pm resolves to today's 4pm,
        # not tomorrow's. dateparser 1.4.x with PREFER_DATES_FROM="future"
        # pushed bare time-of-day inputs one day forward.
        config = Config()
        target = run._resolve_target("4pm", config, PINNED_NOW)
        assert target.date() == PINNED_NOW.date()
        assert target > PINNED_NOW

    def test_bare_future_time_of_day_with_minutes_stays_on_today(self):
        # Same regression, exercising the 4:30pm input shape.
        config = Config()
        target = run._resolve_target("4:30pm", config, PINNED_NOW)
        assert target.date() == PINNED_NOW.date()
        assert target > PINNED_NOW

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
            global_rules=[_target_rule("30m")],
        )
        target = run._resolve_target("", config, PINNED_NOW)
        assert target == PINNED_NOW + timedelta(minutes=30)

    def test_overnight_window_resolves_end_to_tomorrow(self):
        # At 23:00 with window 22:00-02:00, target="end" resolves to
        # tomorrow 02:00, not today 02:00 (which is past).
        now = datetime(2024, 1, 15, 23, 0, tzinfo=UTC)
        config = Config(
            aliases={
                "night": [
                    Rule(
                        condition=Condition(start=time(22, 0), end=time(2, 0)),
                        target="end",
                    )
                ]
            },
        )
        target = run._resolve_target("night", config, now)
        assert target == datetime(2024, 1, 16, 2, 0, tzinfo=UTC)

    def test_overnight_window_after_midnight_resolves_end_to_today(self):
        # At 01:00 with window 22:00-02:00, target="end" resolves to
        # today 02:00 (still upcoming, no day bump needed).
        now = datetime(2024, 1, 16, 1, 0, tzinfo=UTC)
        config = Config(
            aliases={
                "night": [
                    Rule(
                        condition=Condition(start=time(22, 0), end=time(2, 0)),
                        target="end",
                    )
                ]
            },
        )
        target = run._resolve_target("night", config, now)
        assert target == datetime(2024, 1, 16, 2, 0, tzinfo=UTC)


# ---------------------------------------------------------------------
# _correct_future_preference_regression: unit tests that directly
# simulate the dateparser 1.4.x bug. The integration tests above pin
# end-to-end behavior, but on dateparser 1.1.8 (locked in uv.lock)
# the bug does not manifest, so the helper's branching logic would go
# unexercised without these.
# ---------------------------------------------------------------------


class TestCorrectFuturePreferenceRegression:
    def test_pulls_buggy_future_parse_back_to_today(self, monkeypatch):
        # later simulates dateparser 1.4.x's buggy output for "4pm" at
        # noon: tomorrow 4pm. The patched second parse returns today
        # 4pm (still upcoming). Helper must pick today's.
        now = PINNED_NOW  # 2024-01-15 12:00 UTC
        later = datetime(2024, 1, 16, 16, 0, tzinfo=UTC)  # tomorrow 4pm
        today = datetime(2024, 1, 15, 16, 0, tzinfo=UTC)  # today 4pm
        monkeypatch.setattr("keep_alive.run.dateparser.parse", lambda *a, **kw: today)
        result = run._correct_future_preference_regression(later, "4pm", now)
        assert result == today

    def test_keeps_tomorrow_when_today_slot_is_past(self, monkeypatch):
        # 11am at noon: today's slot already past, so the helper must
        # keep tomorrow's result rather than pulling back to a past time.
        now = PINNED_NOW  # noon
        later = datetime(2024, 1, 16, 11, 0, tzinfo=UTC)  # tomorrow 11am
        past_today = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)  # today 11am
        monkeypatch.setattr("keep_alive.run.dateparser.parse", lambda *a, **kw: past_today)
        result = run._correct_future_preference_regression(later, "11am", now)
        assert result == later

    def test_no_op_when_later_already_on_today(self, monkeypatch):
        # Short-circuit: if later is already on today's date, the helper
        # must not call dateparser a second time.
        now = PINNED_NOW
        later = datetime(2024, 1, 15, 16, 0, tzinfo=UTC)  # today 4pm

        def fail_if_called(*a, **kw):
            raise AssertionError("dateparser.parse should not be called")

        monkeypatch.setattr("keep_alive.run.dateparser.parse", fail_if_called)
        result = run._correct_future_preference_regression(later, "4pm", now)
        assert result == later

    def test_no_op_when_later_before_today(self, monkeypatch):
        # Defensive: an explicitly past input (e.g. "yesterday 4pm")
        # should pass through untouched for _validate_target to reject.
        now = PINNED_NOW
        later = datetime(2024, 1, 14, 16, 0, tzinfo=UTC)  # yesterday 4pm

        def fail_if_called(*a, **kw):
            raise AssertionError("dateparser.parse should not be called")

        monkeypatch.setattr("keep_alive.run.dateparser.parse", fail_if_called)
        result = run._correct_future_preference_regression(later, "yesterday 4pm", now)
        assert result == later


# ---------------------------------------------------------------------
# DefaultGroup: subcommand resolution
# ---------------------------------------------------------------------


class TestDefaultGroup:
    """Tests for the DefaultGroup's command-resolution logic."""

    def test_version_flag_prints_and_exits(self, capsys):
        rv = run.cli.main(args=["--version"], prog_name="keep-alive", standalone_mode=False)
        assert rv == 0
        assert "keep-alive" in capsys.readouterr().out

    def test_double_dash_routes_to_run(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={"list": [_target_rule("2h")]},
        )
        run.cli.main(args=["--", "list"], prog_name="keep-alive", standalone_mode=False)
        mock_backend.inhibit.assert_called_once_with(7200, ANY)

    def test_bare_invocation_routes_to_run(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        with pytest.raises(SystemExit):
            run.cli.main(args=[], prog_name="keep-alive", standalone_mode=False)
        assert "Missing a target" in capsys.readouterr().out

    def test_known_subcommand_not_redirected(
        self, monkeypatch, capsys, tmp_path, mock_config_loader
    ):
        path = tmp_path / "state"
        monkeypatch.setattr("keep_alive.backends._pidfile_path", lambda: path)
        run.cli.main(args=["status"], prog_name="keep-alive", standalone_mode=False)
        assert "no keep-alive running" in capsys.readouterr().out


# ---------------------------------------------------------------------
# Shell completion
# ---------------------------------------------------------------------


class TestShellCompletion:
    """Shell completion via click's built-in shell_completion module."""

    @staticmethod
    def _get_completions(monkeypatch, comp_words, comp_cword):
        from click.shell_completion import BashComplete

        monkeypatch.setenv("COMP_WORDS", comp_words)
        monkeypatch.setenv("COMP_CWORD", str(comp_cword))
        comp = BashComplete(run.cli, {}, "keep-alive", "_KEEP_ALIVE_COMPLETE")
        args, incomplete = comp.get_completion_args()
        return comp.get_completions(args, incomplete)

    @staticmethod
    def _write_config(tmp_path):
        config_dir = tmp_path / "keep-alive"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[[alias]]\nname = "work"\n[[alias.rule]]\ntarget = "5pm"\n'
            '[[alias]]\nname = "personal"\n[[alias.rule]]\ntarget = "30m"\n'
        )

    def test_zsh_source_produces_completion_script(self):
        from click.shell_completion import ZshComplete

        comp = ZshComplete(run.cli, {}, "keep-alive", "_KEEP_ALIVE_COMPLETE")
        script = comp.source()
        assert "_keep_alive_completion" in script
        assert "compdef" in script

    def test_bash_source_produces_completion_script(self):
        from click.shell_completion import BashComplete

        comp = BashComplete(run.cli, {}, "keep-alive", "_KEEP_ALIVE_COMPLETE")
        script = comp.source()
        assert "_keep_alive_completion" in script
        assert "COMP_WORDS" in script

    def test_fish_source_produces_completion_script(self):
        from click.shell_completion import FishComplete

        comp = FishComplete(run.cli, {}, "keep-alive", "_KEEP_ALIVE_COMPLETE")
        script = comp.source()
        assert "keep-alive" in script

    def test_subcommand_completion(self, monkeypatch):
        completions = self._get_completions(monkeypatch, "keep-alive li", 1)
        names = [c.value for c in completions]
        assert "list" in names

    def test_all_subcommands_complete_on_empty(self, monkeypatch):
        completions = self._get_completions(monkeypatch, "keep-alive ", 1)
        names = [c.value for c in completions]
        assert "run" in names
        assert "list" in names
        assert "status" in names
        assert "clear" in names

    def test_alias_completion_at_group_level(self, monkeypatch, tmp_path):
        self._write_config(tmp_path)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        completions = self._get_completions(monkeypatch, "keep-alive ", 1)
        names = [c.value for c in completions]
        assert "work" in names
        assert "personal" in names

    def test_alias_completion_at_run_level(self, monkeypatch, tmp_path):
        self._write_config(tmp_path)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        completions = self._get_completions(monkeypatch, "keep-alive run wo", 2)
        names = [c.value for c in completions]
        assert names == ["work"]

    def test_completion_does_not_crash_with_missing_config(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "/nonexistent/path/xyz")
        completions = self._get_completions(monkeypatch, "keep-alive ", 1)
        names = [c.value for c in completions]
        assert "list" in names

    def test_complete_aliases_returns_matching_names(self, monkeypatch):
        config = Config(
            aliases={
                "work": [_target_rule("2h")],
                "personal": [_target_rule("30m")],
            }
        )
        monkeypatch.setattr("keep_alive.run.load_config", lambda path=None: config)
        ctx = MagicMock()
        ctx.params = {}
        ctx.parent = None
        result = run._complete_aliases(ctx, None, "wo")
        assert [c.value for c in result] == ["work"]

    def test_complete_aliases_returns_empty_on_config_error(self, monkeypatch):
        def raise_error(path=None):
            raise ConfigError("bad config")

        monkeypatch.setattr("keep_alive.run.load_config", raise_error)
        ctx = MagicMock()
        ctx.params = {}
        ctx.parent = None
        assert run._complete_aliases(ctx, None, "anything") == []


# ---------------------------------------------------------------------
# Colored help
# ---------------------------------------------------------------------


class TestColoredHelp:
    """ColoredHelpFormatter applies ANSI codes that click.echo() strips
    when the terminal does not support color.
    """

    def test_group_help_has_bold_headings(self):
        ctx = run.cli.make_context("keep-alive", [])
        ctx.color = True
        help_text = run.cli.get_help(ctx)
        assert "\x1b[1m" in help_text  # bold

    def test_group_help_has_cyan_terms(self):
        ctx = run.cli.make_context("keep-alive", [])
        ctx.color = True
        help_text = run.cli.get_help(ctx)
        assert "\x1b[36m" in help_text  # cyan

    def test_help_strips_color_when_disabled(self):
        import io

        import click as click_mod

        ctx = run.cli.make_context("keep-alive", [])
        buf = io.StringIO()
        click_mod.echo(run.cli.get_help(ctx), file=buf, color=False)
        assert "\x1b" not in buf.getvalue()

    def test_subcommand_help_has_color(self):
        ctx = run.cli.make_context("keep-alive", [])
        ctx.color = True
        cmd = run.cli.get_command(ctx, "run")
        help_text = cmd.get_help(ctx)
        assert "\x1b[1m" in help_text
        assert "\x1b[36m" in help_text


class TestMain:
    def _run_main(self, monkeypatch, argv):
        run.cli.main(args=argv, prog_name="keep-alive", standalone_mode=False)

    def test_alias_runs_backend(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={"work": [_target_rule("2h")]},
        )
        self._run_main(monkeypatch, ["work"])

        assert mock_backend.cleanup.called
        mock_backend.inhibit.assert_called_once_with(7200, ANY)
        assert "Keeping alive until" in capsys.readouterr().out

    def test_dateparser_input_runs_backend(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        self._run_main(monkeypatch, ["1h"])
        mock_backend.inhibit.assert_called_once_with(3600, ANY)

    def test_global_rule_used_when_alias_misses(
        self, monkeypatch, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={
                "work": [
                    Rule(
                        condition=Condition(start=time(9, 0), end=time(10, 0)),
                        target="end",
                    )
                ]
            },
            global_rules=[_target_rule("30m")],
        )
        self._run_main(monkeypatch, ["work"])
        # 30 minutes = 1800 seconds
        mock_backend.inhibit.assert_called_once_with(1800, ANY)

    def test_alias_no_match_exits_nonzero(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={
                "work": [
                    Rule(
                        condition=Condition(start=time(9, 0), end=time(10, 0)),
                        target="end",
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
# main: --dry-run
# ---------------------------------------------------------------------


class TestDryRun:
    def _run_main(self, monkeypatch, argv):
        run.cli.main(args=argv, prog_name="keep-alive", standalone_mode=False)

    def test_dry_run_prints_target_duration_and_backend(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_backend.__name__ = "FakeBackend"
        self._run_main(monkeypatch, ["2h", "--dry-run"])
        out = capsys.readouterr().out
        assert "target:" in out
        assert "duration: 2h" in out
        assert "backend: FakeBackend" in out

    def test_dry_run_does_not_engage_backend(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        self._run_main(monkeypatch, ["1h", "--dry-run"])
        assert not mock_backend.inhibit.called
        assert not mock_backend.cleanup.called

    def test_dry_run_with_alias(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={"work": [_target_rule("2h")]},
        )
        mock_backend.__name__ = "FakeBackend"
        self._run_main(monkeypatch, ["--dry-run", "work"])
        out = capsys.readouterr().out
        assert "duration: 2h" in out
        assert "backend: FakeBackend" in out

    def test_dry_run_with_past_target_still_validates(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        with pytest.raises(SystemExit):
            self._run_main(monkeypatch, ["--dry-run", "yesterday 4pm"])
        assert "in the past" in capsys.readouterr().out
        assert not mock_backend.inhibit.called

    def test_dry_run_surfaces_no_backend_found(
        self, monkeypatch, capsys, mock_now, mock_config_loader
    ):
        def fake_get_backend():
            print(
                "No suitable backend found. "
                "Please install caffeinate (macOS) or systemd-inhibit (Linux)."
            )
            sys.exit(1)

        monkeypatch.setattr("keep_alive.run.get_backend", fake_get_backend)
        with pytest.raises(SystemExit):
            self._run_main(monkeypatch, ["--dry-run", "1h"])
        assert "No suitable backend found" in capsys.readouterr().out

    def test_list_subcommand_dispatches_before_dry_run(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={"work": [_target_rule("2h")]},
        )
        self._run_main(monkeypatch, ["list", "--dry-run"])
        out = capsys.readouterr().out
        assert "work" in out
        assert "for 2h" in out
        assert "target:" not in out  # dry-run output suppressed
        assert not mock_backend.inhibit.called


# ---------------------------------------------------------------------
# main: --status
# ---------------------------------------------------------------------


class TestStatus:
    """status subcommand reads the state file and prints what's known about
    a live keep-alive run. Read-only: never writes, kills, or engages a
    backend.
    """

    def _run_main(self, monkeypatch, argv):
        run.cli.main(args=argv, prog_name="keep-alive", standalone_mode=False)

    def _redirect_state_file(self, monkeypatch, tmp_path):
        """Point backends._pidfile_path at tmp_path so tests can write fixtures."""
        path = tmp_path / "state"
        monkeypatch.setattr("keep_alive.backends._pidfile_path", lambda: path)
        return path

    def _write_state(self, path, state):
        import json

        path.write_text(json.dumps(state))

    def test_no_state_file_prints_not_running(self, monkeypatch, capsys, tmp_path):
        self._redirect_state_file(monkeypatch, tmp_path)
        self._run_main(monkeypatch, ["status"])
        assert "no keep-alive running" in capsys.readouterr().out

    def test_dead_pid_prints_not_running(self, monkeypatch, capsys, tmp_path):
        path = self._redirect_state_file(monkeypatch, tmp_path)
        self._write_state(
            path,
            {
                "input": "2h",
                "start": "2024-01-15T12:00:00+00:00",
                "end": "2024-01-15T14:00:00+00:00",
                "pid": 99999,
                "backend": "CaffeinateBackend",
            },
        )
        # _is_our_process returns False (PID is dead or reused).
        monkeypatch.setattr("keep_alive.run._is_our_process", lambda pid, name: False)
        self._run_main(monkeypatch, ["status"])
        assert "no keep-alive running" in capsys.readouterr().out

    def test_live_prints_six_lines(self, monkeypatch, capsys, tmp_path, mock_now):
        path = self._redirect_state_file(monkeypatch, tmp_path)
        self._write_state(
            path,
            {
                "input": "2h",
                "start": "2024-01-15T12:00:00+00:00",
                "end": "2024-01-15T14:00:00+00:00",
                "pid": 12345,
                "backend": "CaffeinateBackend",
            },
        )
        monkeypatch.setattr("keep_alive.run._is_our_process", lambda pid, name: True)
        self._run_main(monkeypatch, ["status"])
        out = capsys.readouterr().out
        assert "target: 2h" in out
        assert "start:" in out
        assert "end:" in out
        assert "remaining: 2h" in out
        assert "backend: CaffeinateBackend" in out
        assert "pid: 12345" in out

    def test_legacy_pidfile_prints_unknowns(self, monkeypatch, capsys, tmp_path):
        path = self._redirect_state_file(monkeypatch, tmp_path)
        # Old-format pidfile: just a PID string.
        path.write_text("12345")
        # Exercise the real _is_our_process logic: mock os.kill to report
        # the process alive, and ps to report it as caffeinate. Legacy
        # pidfiles have no backend name, so _is_our_process must accept
        # any of the known backend commands.
        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        import subprocess as _subprocess

        fake_result = _subprocess.CompletedProcess(args=[], returncode=0, stdout="caffeinate")
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake_result)
        self._run_main(monkeypatch, ["status"])
        out = capsys.readouterr().out
        assert "pid: 12345" in out
        assert "(unknown" in out  # missing metadata fields noted

    def test_legacy_pidfile_with_dead_pid_prints_not_running(self, monkeypatch, capsys, tmp_path):
        path = self._redirect_state_file(monkeypatch, tmp_path)
        path.write_text("12345")
        # Process is dead — _is_our_process should report not-our-process.

        def raise_lookup(pid, sig):
            raise ProcessLookupError()

        monkeypatch.setattr("os.kill", raise_lookup)
        self._run_main(monkeypatch, ["status"])
        assert "no keep-alive running" in capsys.readouterr().out

    def test_legacy_pidfile_with_reused_pid_prints_not_running(self, monkeypatch, capsys, tmp_path):
        path = self._redirect_state_file(monkeypatch, tmp_path)
        path.write_text("12345")
        # Process exists but isn't one of our backends.
        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        import subprocess as _subprocess

        fake_result = _subprocess.CompletedProcess(args=[], returncode=0, stdout="chrome")
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake_result)
        self._run_main(monkeypatch, ["status"])
        assert "no keep-alive running" in capsys.readouterr().out

    def test_bare_invocation_shows_bare_label(self, monkeypatch, capsys, tmp_path, mock_now):
        path = self._redirect_state_file(monkeypatch, tmp_path)
        self._write_state(
            path,
            {
                "input": "",
                "start": "2024-01-15T12:00:00+00:00",
                "end": "2024-01-15T14:00:00+00:00",
                "pid": 12345,
                "backend": "CaffeinateBackend",
            },
        )
        monkeypatch.setattr("keep_alive.run._is_our_process", lambda pid, name: True)
        self._run_main(monkeypatch, ["status"])
        assert "target: (bare)" in capsys.readouterr().out

    def test_status_does_not_engage_backend(
        self, monkeypatch, capsys, tmp_path, mock_backend, mock_config_loader
    ):
        self._redirect_state_file(monkeypatch, tmp_path)
        self._run_main(monkeypatch, ["status"])
        assert not mock_backend.inhibit.called
        assert not mock_backend.cleanup.called

    def test_status_dispatches_before_dry_run(
        self, monkeypatch, capsys, tmp_path, mock_now, mock_backend, mock_config_loader
    ):
        self._redirect_state_file(monkeypatch, tmp_path)
        self._run_main(monkeypatch, ["status", "--dry-run", "2h"])
        out = capsys.readouterr().out
        assert "no keep-alive running" in out  # status output
        assert "Keeping alive" not in out  # backend never reached


# ---------------------------------------------------------------------
# main: clear subcommand
# ---------------------------------------------------------------------


class TestClear:
    """clear subcommand kills the in-flight keep-alive process group and
    removes the state file. Stale or reused PIDs are detected and left
    alone.
    """

    def _run_main(self, monkeypatch, argv):
        run.cli.main(args=argv, prog_name="keep-alive", standalone_mode=False)

    def _redirect_state_file(self, monkeypatch, tmp_path):
        path = tmp_path / "state"
        monkeypatch.setattr("keep_alive.backends._pidfile_path", lambda: path)
        monkeypatch.setattr("keep_alive.run._pidfile_path", lambda: path)
        return path

    def _write_state(self, path, state):
        import json

        path.write_text(json.dumps(state))

    def test_no_state_file_prints_not_running(self, monkeypatch, capsys, tmp_path):
        self._redirect_state_file(monkeypatch, tmp_path)
        self._run_main(monkeypatch, ["clear"])
        assert "no keep-alive running" in capsys.readouterr().out

    def test_live_process_killed_and_confirmed(
        self, monkeypatch, capsys, tmp_path, mock_config_loader
    ):
        path = self._redirect_state_file(monkeypatch, tmp_path)
        self._write_state(
            path,
            {
                "input": "2h",
                "start": "2024-01-15T12:00:00+00:00",
                "end": "2024-01-15T14:00:00+00:00",
                "pid": 12345,
                "backend": "CaffeinateBackend",
            },
        )
        monkeypatch.setattr("keep_alive.run._is_our_process", lambda pid, name: True)
        killed = {"pgid": None}
        monkeypatch.setattr("os.killpg", lambda pgid, sig: killed.update(pgid=pgid))
        self._run_main(monkeypatch, ["clear"])
        out = capsys.readouterr().out
        assert "cleared keep-alive (pid 12345)" in out
        assert killed["pgid"] == 12345
        assert not path.exists()

    def test_dead_pid_prints_not_running_and_cleans_up(
        self, monkeypatch, capsys, tmp_path, mock_config_loader
    ):
        path = self._redirect_state_file(monkeypatch, tmp_path)
        self._write_state(
            path,
            {
                "input": "2h",
                "start": "2024-01-15T12:00:00+00:00",
                "end": "2024-01-15T14:00:00+00:00",
                "pid": 99999,
                "backend": "CaffeinateBackend",
            },
        )
        monkeypatch.setattr("keep_alive.run._is_our_process", lambda pid, name: False)

        def fail_if_killed(pgid, sig):
            raise AssertionError("should not killpg")

        monkeypatch.setattr("os.killpg", fail_if_killed)
        self._run_main(monkeypatch, ["clear"])
        assert "no keep-alive running" in capsys.readouterr().out
        assert not path.exists()

    def test_reused_pid_not_killed(self, monkeypatch, capsys, tmp_path, mock_config_loader):
        path = self._redirect_state_file(monkeypatch, tmp_path)
        self._write_state(
            path,
            {
                "input": "2h",
                "start": "2024-01-15T12:00:00+00:00",
                "end": "2024-01-15T14:00:00+00:00",
                "pid": 12345,
                "backend": "CaffeinateBackend",
            },
        )
        monkeypatch.setattr("keep_alive.run._is_our_process", lambda pid, name: False)

        def fail_if_killed(pgid, sig):
            raise AssertionError("should not killpg a reused PID")

        monkeypatch.setattr("os.killpg", fail_if_killed)
        self._run_main(monkeypatch, ["clear"])
        assert "no keep-alive running" in capsys.readouterr().out
        assert not path.exists()

    def test_legacy_pidfile_live_process_cleared(
        self, monkeypatch, capsys, tmp_path, mock_config_loader
    ):
        path = self._redirect_state_file(monkeypatch, tmp_path)
        path.write_text("12345")
        monkeypatch.setattr("keep_alive.run._is_our_process", lambda pid, name: True)
        killed = {"pgid": None}
        monkeypatch.setattr("os.killpg", lambda pgid, sig: killed.update(pgid=pgid))
        self._run_main(monkeypatch, ["clear"])
        out = capsys.readouterr().out
        assert "cleared keep-alive (pid 12345)" in out
        assert killed["pgid"] == 12345
        assert not path.exists()

    def test_clear_does_not_engage_backend(
        self, monkeypatch, capsys, tmp_path, mock_backend, mock_config_loader
    ):
        self._redirect_state_file(monkeypatch, tmp_path)
        self._run_main(monkeypatch, ["clear"])
        assert not mock_backend.inhibit.called
        assert not mock_backend.cleanup.called


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
        run.cli.main(
            args=["--config", "/foo.toml", "2h"],
            prog_name="keep-alive",
            standalone_mode=False,
        )
        assert captured["path"] == "/foo.toml"

    def test_no_config_flag_passes_none(self, monkeypatch, mock_now, mock_backend):
        captured = {"path": "unset"}

        def capturing_loader(path):
            captured["path"] = path
            return Config()

        monkeypatch.setattr("keep_alive.run._load_config_or_exit", capturing_loader)
        run.cli.main(args=["2h"], prog_name="keep-alive", standalone_mode=False)
        assert captured["path"] is None


# ---------------------------------------------------------------------
# --list command
# ---------------------------------------------------------------------


class TestList:
    def _run_main(self, monkeypatch, argv):
        run.cli.main(args=argv, prog_name="keep-alive", standalone_mode=False)

    def test_list_subcommand_with_empty_config(
        self, monkeypatch, capsys, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config()
        self._run_main(monkeypatch, ["list"])
        out = capsys.readouterr().out
        assert out.strip() == "global"
        assert not mock_backend.inhibit.called

    def test_list_subcommand_with_multiple_aliases(
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
                        target="end",
                    ),
                    Rule(
                        condition=None,
                        target="2h",
                    ),
                ],
                "personal": [
                    Rule(
                        condition=Condition(start=time(5, 0), end=time(19, 0)),
                        target="16:00",
                    ),
                ],
            },
            global_rules=[
                Rule(
                    condition=None,
                    target="30m",
                )
            ],
        )
        self._run_main(monkeypatch, ["list"])
        out = capsys.readouterr().out
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

    def test_list_subcommand_respects_config_flag(
        self, monkeypatch, capsys, mock_backend, mock_now, tmp_path
    ):
        config_file = tmp_path / "test.toml"
        config_file.write_text('[[alias]]\nname = "fromfile"\n[[alias.rule]]\ntarget = "30m"\n')
        run.cli.main(
            args=["list", "--config", str(config_file)],
            prog_name="keep-alive",
            standalone_mode=False,
        )
        out = capsys.readouterr().out
        assert "fromfile" in out
        assert "for 30m" in out

    def test_list_subcommand_ignores_positional_input(
        self, monkeypatch, capsys, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={"work": [_target_rule("2h")]},
        )
        self._run_main(monkeypatch, ["list", "work"])
        out = capsys.readouterr().out
        assert "work" in out
        assert "for 2h" in out
        assert not mock_backend.inhibit.called

    def test_deprecated_list_flag_still_works(
        self, monkeypatch, capsys, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config()
        self._run_main(monkeypatch, ["--list"])
        out = capsys.readouterr().out
        assert out.strip() == "global"
        assert not mock_backend.inhibit.called

    def test_deprecated_list_flag_prints_warning_to_stderr(
        self, monkeypatch, capsys, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config()
        self._run_main(monkeypatch, ["--list"])
        err = capsys.readouterr().err
        assert "deprecated" in err
        assert "keep-alive list" in err

    def test_list_subcommand_no_deprecation_warning(
        self, monkeypatch, capsys, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config()
        self._run_main(monkeypatch, ["list"])
        err = capsys.readouterr().err
        assert err == ""


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
        "target,condition,expected",
        [
            ("2h", None, "for 2h"),
            ("1h30m", None, "for 1h30m"),
            ("16:00", None, "at 16:00"),
            ("4pm", None, "at 4pm"),
            ("end", Condition(start=time(9, 0), end=time(17, 0)), "until 17:00"),
        ],
    )
    def test_format_target(self, target, condition, expected):
        rule = Rule(condition=condition, target=target)
        assert run._format_target(rule) == expected

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


# ---------------------------------------------------------------------
# --verbose / -v
# ---------------------------------------------------------------------


class TestVerboseLogging:
    """-v emits INFO (rule eval, target resolution, backend selection) to
    stderr; -vv adds DEBUG (dateparser details, state file I/O). Default
    invocation is silent on stderr.
    """

    def _run_main(self, monkeypatch, argv):
        run.cli.main(args=argv, prog_name="keep-alive", standalone_mode=False)

    def test_default_is_silent_on_stderr(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(aliases={"work": [_target_rule("2h")]})
        self._run_main(monkeypatch, ["work"])
        err = capsys.readouterr().err
        assert err == ""

    def test_v_prints_rule_evaluation_to_stderr(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(aliases={"work": [_target_rule("2h")]})
        self._run_main(monkeypatch, ["-v", "work"])
        captured = capsys.readouterr()
        # Rule evaluation goes to stderr, not stdout
        assert "evaluating alias 'work'" in captured.err
        assert "matched rule" in captured.err

    def test_v_prints_selected_backend(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_backend.__name__ = "FakeBackend"
        self._run_main(monkeypatch, ["-v", "1h"])
        err = capsys.readouterr().err
        assert "selected backend: FakeBackend" in err

    def test_v_prints_global_fallback(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        mock_config_loader["config"] = Config(
            aliases={
                "work": [
                    Rule(
                        condition=Condition(start=time(9, 0), end=time(10, 0)),
                        target="end",
                    )
                ]
            },
            global_rules=[_target_rule("30m")],
        )
        self._run_main(monkeypatch, ["-v", "work"])
        err = capsys.readouterr().err
        assert "no alias rule matched" in err
        assert "falling back to global rules" in err

    def test_vv_shows_dateparser_details(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        self._run_main(monkeypatch, ["-vv", "2h"])
        err = capsys.readouterr().err
        assert "dateparser parse" in err

    def test_vv_shows_state_file_write(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader, tmp_path
    ):
        from keep_alive.backends import _write_state

        run._configure_logging(2)
        monkeypatch.setattr("keep_alive.backends._pidfile_path", lambda: tmp_path / "state")
        # _write_state is called by the real backend; call it directly since
        # the backend is mocked in CLI tests.
        _write_state({"pid": 123, "backend": "MockBackend"})
        err = capsys.readouterr().err
        assert "wrote state file" in err

    def test_vv_shows_state_file_read(self, monkeypatch, capsys, mock_config_loader, tmp_path):
        import json

        path = tmp_path / "state"
        path.write_text(json.dumps({"pid": 999, "backend": "MockBackend"}))
        monkeypatch.setattr("keep_alive.backends._pidfile_path", lambda: path)
        self._run_main(monkeypatch, ["-vv", "status"])
        err = capsys.readouterr().err
        assert "read state file" in err

    def test_no_verbose_keeps_stdout_clean(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        """Logging never leaks into stdout regardless of verbosity."""
        mock_config_loader["config"] = Config(aliases={"work": [_target_rule("2h")]})
        self._run_main(monkeypatch, ["-vv", "work"])
        out = capsys.readouterr().out
        assert "evaluating alias" not in out
        assert "dateparser parse" not in out

    def test_v_after_dry_run_option(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        """-v works after a subcommand option: --dry-run -v personal"""
        mock_config_loader["config"] = Config(aliases={"work": [_target_rule("2h")]})
        self._run_main(monkeypatch, ["--dry-run", "-v", "work"])
        err = capsys.readouterr().err
        assert "evaluating alias 'work'" in err

    def test_v_after_positional(
        self, monkeypatch, capsys, mock_now, mock_backend, mock_config_loader
    ):
        """-v works at the end: --dry-run personal -v (run-level -v)"""
        mock_config_loader["config"] = Config(aliases={"work": [_target_rule("2h")]})
        self._run_main(monkeypatch, ["--dry-run", "work", "-v"])
        err = capsys.readouterr().err
        assert "evaluating alias 'work'" in err
