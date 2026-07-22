"""Helper process that holds D-Bus inhibit cookies for a fixed duration.

Spawned by DBusScreenSaverBackend as a background subprocess. Connects to
the session bus, acquires inhibit cookies from org.freedesktop.ScreenSaver
(kscreenlocker) and, when available, org.freedesktop.PowerManagement.Inhibit
(PowerDevil). Then blocks for the requested duration and releases both.

SIGTERM triggers a clean UnInhibit on both services. If the process is
killed forcefully (SIGKILL), KDE releases the cookies automatically when
the bus connection drops, so no leak occurs.
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
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <duration_seconds>", file=sys.stderr)
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
