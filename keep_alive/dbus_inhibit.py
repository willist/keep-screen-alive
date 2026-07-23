"""D-Bus screen-lock and power-management inhibitor.

Holds org.freedesktop.ScreenSaver.Inhibit and (when available)
org.freedesktop.PowerManagement.Inhibit cookies for a fixed duration.
SIGTERM triggers clean UnInhibit on both. If killed forcefully, KDE
releases the cookies when the bus connection drops.

Usage:
    dbus-inhibit <duration_seconds>    Hold inhibitors for N seconds
    dbus-inhibit --check               Exit 0 if inhibitors can be acquired
"""

from __future__ import annotations

import signal
import sys
import time

_APP_NAME = "keep-alive"
_REASON = "Prevent screen sleep"

_TARGETS = [
    (
        "org.freedesktop.ScreenSaver",
        "/org/freedesktop/ScreenSaver",
        "org.freedesktop.ScreenSaver",
    ),
    (
        "org.freedesktop.PowerManagement",
        "/org/freedesktop/PowerManagement/Inhibit",
        "org.freedesktop.PowerManagement.Inhibit",
    ),
]


def _check() -> int:
    """Verify that D-Bus inhibitors can be acquired. Returns exit code."""
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
            None,
            GLib.VariantType("(as)"),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        names = reply.unpack()[0]
        return 0 if "org.freedesktop.ScreenSaver" in names else 1
    except Exception:
        return 1


def _hold(duration: int) -> None:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    cookies: list[tuple[str, str, str, int]] = []
    for bus_name, path, iface in _TARGETS:
        try:
            reply = bus.call_sync(
                bus_name,
                path,
                iface,
                "Inhibit",
                GLib.Variant("(ss)", (_APP_NAME, _REASON)),
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                -1,
                None,
            )
            cookies.append((bus_name, path, iface, reply.unpack()[0]))
        except GLib.Error:
            pass

    if not cookies:
        print("failed to acquire any D-Bus inhibitors", file=sys.stderr)
        sys.exit(1)

    def _release(*_args):
        for bus_name, path, iface, cookie in cookies:
            try:
                bus.call_sync(
                    bus_name,
                    path,
                    iface,
                    "UnInhibit",
                    GLib.Variant("(u)", (cookie,)),
                    None,
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None,
                )
            except GLib.Error:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _release)

    time.sleep(duration)

    _release()


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--check":
        sys.exit(_check())

    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <duration_seconds> | --check", file=sys.stderr)
        sys.exit(2)
    try:
        duration = int(sys.argv[1])
    except ValueError:
        print(f"invalid duration: {sys.argv[1]}", file=sys.stderr)
        sys.exit(2)

    try:
        _hold(duration)
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
