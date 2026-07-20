from unittest.mock import patch

from keep_alive.backends import CaffeinateBackend, SystemdInhibitBackend, get_backend


def test_get_backend_prefers_caffeinate():
    with patch("keep_alive.backends.shutil.which", return_value="/usr/bin/caffeinate"):
        result = get_backend()
    assert result is CaffeinateBackend


def test_get_backend_falls_back_to_systemd():
    def fake_which(cmd):
        return "/usr/bin/systemd-inhibit" if cmd == "systemd-inhibit" else None

    with patch("keep_alive.backends.shutil.which", side_effect=fake_which):
        result = get_backend()
    assert result is SystemdInhibitBackend
