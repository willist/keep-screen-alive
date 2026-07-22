import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from keep_alive.backends import (
    CaffeinateBackend,
    DBusScreenSaverBackend,
    SystemdInhibitBackend,
    _pidfile_path,
    get_backend,
)


def test_get_backend_prefers_caffeinate():
    with patch("keep_alive.backends.shutil.which", return_value="/usr/bin/caffeinate"):
        result = get_backend()
    assert result is CaffeinateBackend


def test_get_backend_prefers_dbus_over_systemd():
    def fake_which(cmd):
        return "/usr/bin/systemd-inhibit" if cmd == "systemd-inhibit" else None

    with (
        patch("keep_alive.backends.shutil.which", side_effect=fake_which),
        patch.object(DBusScreenSaverBackend, "available", return_value=True),
    ):
        result = get_backend()
    assert result is DBusScreenSaverBackend


def test_get_backend_falls_back_to_systemd():
    def fake_which(cmd):
        return "/usr/bin/systemd-inhibit" if cmd == "systemd-inhibit" else None

    with patch("keep_alive.backends.shutil.which", side_effect=fake_which):
        result = get_backend()
    assert result is SystemdInhibitBackend


class TestDBusScreenSaverAvailable:
    def test_false_when_not_kde(self, monkeypatch):
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
        assert DBusScreenSaverBackend.available() is False

    def test_false_when_xdg_unset(self, monkeypatch):
        monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
        assert DBusScreenSaverBackend.available() is False

    def test_false_when_gi_missing(self, monkeypatch):
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
        monkeypatch.setitem(sys.modules, "gi", None)
        assert DBusScreenSaverBackend.available() is False


@pytest.fixture
def tmp_pidfile(tmp_path, monkeypatch):
    """Redirect _pidfile_path() to a temp file under tmp_path."""
    fake_path = tmp_path / "pid"
    monkeypatch.setattr("keep_alive.backends._pidfile_path", lambda: fake_path)
    return fake_path


@pytest.mark.parametrize(
    "backend",
    [CaffeinateBackend, SystemdInhibitBackend, DBusScreenSaverBackend],
)
class TestPidfileTracking:
    """All backends share the same pidfile semantics - exercise them identically."""

    def test_inhibit_writes_pidfile(self, tmp_pidfile, backend):
        mock_proc = MagicMock(pid=12345)
        with patch("keep_alive.backends.subprocess.Popen", return_value=mock_proc):
            backend.inhibit(300)
        assert tmp_pidfile.exists()
        assert tmp_pidfile.read_text() == "12345"

    def test_cleanup_kills_stored_process_group(self, tmp_pidfile, backend):
        tmp_pidfile.write_text("12345")
        with patch("keep_alive.backends.os.killpg") as mock_killpg:
            backend.cleanup()
        mock_killpg.assert_called_once_with(12345, signal.SIGTERM)
        assert not tmp_pidfile.exists()

    def test_cleanup_with_missing_pidfile_is_noop(self, tmp_pidfile, backend):
        with patch("keep_alive.backends.os.killpg") as mock_killpg:
            backend.cleanup()
        mock_killpg.assert_not_called()

    def test_cleanup_with_dead_pid_unlinks_quietly(self, tmp_pidfile, backend):
        tmp_pidfile.write_text("12345")
        with patch("keep_alive.backends.os.killpg", side_effect=ProcessLookupError):
            backend.cleanup()
        assert not tmp_pidfile.exists()

    def test_cleanup_with_corrupt_pidfile_unlinks_and_skips(self, tmp_pidfile, backend):
        tmp_pidfile.write_text("not-a-number")
        with patch("keep_alive.backends.os.killpg") as mock_killpg:
            backend.cleanup()
        mock_killpg.assert_not_called()
        assert not tmp_pidfile.exists()

    def test_cleanup_with_empty_pidfile_unlinks_and_skips(self, tmp_pidfile, backend):
        tmp_pidfile.write_text("")
        with patch("keep_alive.backends.os.killpg") as mock_killpg:
            backend.cleanup()
        mock_killpg.assert_not_called()
        assert not tmp_pidfile.exists()


class TestPidfilePath:
    def test_linux_uses_xdg_runtime_dir(self, monkeypatch, tmp_path):
        monkeypatch.setattr("keep_alive.backends.sys.platform", "linux")
        monkeypatch.setattr(
            "keep_alive.backends.os.environ",
            {"XDG_RUNTIME_DIR": str(tmp_path)},
        )
        assert _pidfile_path() == tmp_path / "keep-alive.pid"

    def test_linux_falls_back_to_tmp_when_xdg_unset(self, monkeypatch):
        monkeypatch.setattr("keep_alive.backends.sys.platform", "linux")
        monkeypatch.setattr("keep_alive.backends.os.environ", {})
        assert _pidfile_path() == Path("/tmp/keep-alive.pid")

    def test_macos_uses_library_caches(self, monkeypatch, tmp_path):
        monkeypatch.setattr("keep_alive.backends.sys.platform", "darwin")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        assert _pidfile_path() == tmp_path / "Library/Caches/keep-alive/pid"
