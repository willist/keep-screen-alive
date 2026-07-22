import os
import shutil
import signal
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path


def _pidfile_path() -> Path:
    """Resolve the pidfile path for the current OS.

    Linux: $XDG_RUNTIME_DIR/keep-alive.pid (fallback /tmp). macOS:
    ~/Library/Caches/keep-alive/pid. Both are per-user and avoid colliding
    with inhibit processes spawned by other invocations or other tools.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/keep-alive/pid"
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(xdg_runtime) if xdg_runtime else Path("/tmp")
    return base / "keep-alive.pid"


def _write_pidfile(pid: int) -> None:
    """Record the spawned PID so the next cleanup() can find it."""
    pidfile = _pidfile_path()
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(pid))


def _kill_spawned() -> None:
    """Kill the process group of the previously spawned inhibit process.

    Both backends use ``start_new_session=True``, so the spawned PID is
    also the process-group leader and ``os.killpg(pid, ...)`` reaches the
    whole tree (matters for systemd-inhibit, which spawns ``sleep`` as a
    child). Missing, corrupt, or stale pidfiles are handled quietly so a
    broken prior run does not block a new one.
    """
    pidfile = _pidfile_path()
    if not pidfile.exists():
        return
    try:
        pid = int(pidfile.read_text().strip())
    except (ValueError, OSError):
        pidfile.unlink(missing_ok=True)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Already dead - nothing to kill.
        pass
    finally:
        pidfile.unlink(missing_ok=True)


class InhibitorBackend(ABC):
    """Abstract base class for screen wake inhibition backends."""

    @classmethod
    @abstractmethod
    def available(cls) -> bool:
        """Check if this backend is available on the current system."""
        pass

    @classmethod
    @abstractmethod
    def cleanup(cls) -> None:
        """Clean up any existing inhibit processes."""
        pass

    @classmethod
    @abstractmethod
    def inhibit(cls, duration_seconds: int) -> subprocess.Popen:
        """Start inhibiting screen sleep for the given duration."""
        pass


class CaffeinateBackend(InhibitorBackend):
    """macOS caffeinate backend."""

    @classmethod
    def available(cls) -> bool:
        return shutil.which("caffeinate") is not None

    @classmethod
    def cleanup(cls) -> None:
        _kill_spawned()

    @classmethod
    def inhibit(cls, duration_seconds: int) -> subprocess.Popen:
        proc = subprocess.Popen(
            [
                "caffeinate",
                "-d",
                "-u",
                "-t",
                str(duration_seconds),
            ],
            start_new_session=True,
        )
        _write_pidfile(proc.pid)
        return proc


class SystemdInhibitBackend(InhibitorBackend):
    """Linux systemd-inhibit backend.

    Does not prevent KDE screen lock (kscreenlocker ignores logind idle
    inhibitors), but remains useful as a headless fallback where no
    graphical session exists.
    """

    @classmethod
    def available(cls) -> bool:
        return shutil.which("systemd-inhibit") is not None

    @classmethod
    def cleanup(cls) -> None:
        _kill_spawned()

    @classmethod
    def inhibit(cls, duration_seconds: int) -> subprocess.Popen:
        proc = subprocess.Popen(
            [
                "systemd-inhibit",
                "--who=keep-alive",
                "--why=Prevent screen sleep",
                "--what=idle",
                "sleep",
                str(duration_seconds),
            ],
            start_new_session=True,
        )
        _write_pidfile(proc.pid)
        return proc


class DBusScreenSaverBackend(InhibitorBackend):
    """KDE Plasma D-Bus backend using org.freedesktop.ScreenSaver.Inhibit.

    Talks directly to kscreenlocker via the freedesktop ScreenSaver D-Bus
    interface, which KDE respects (unlike systemd-logind idle inhibitors).
    Also holds a PowerManagement.Inhibit cookie when available to cover
    suspend / DPMS. Only activates under KDE (XDG_CURRENT_DESKTOP=KDE) so
    it does not silently engage on GNOME, which has its own inhibit
    mechanism.

    Availability is checked via the gdbus CLI tool so it works even when
    the running Python lacks PyGObject (e.g. poetry venv, pipx). The
    helper script runs under whatever Python has gi installed, found at
    runtime by trying sys.executable then /usr/bin/python3.
    """

    @classmethod
    def available(cls) -> bool:
        if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":"):
            return False
        gdbus = shutil.which("gdbus")
        if gdbus is None:
            return False
        try:
            result = subprocess.run(
                [
                    gdbus,
                    "call",
                    "--session",
                    "--dest",
                    "org.freedesktop.DBus",
                    "--object-path",
                    "/org/freedesktop/DBus",
                    "--method",
                    "org.freedesktop.DBus.ListNames",
                ],
                capture_output=True,
                timeout=5,
            )
            return b"org.freedesktop.ScreenSaver" in result.stdout
        except (OSError, subprocess.SubprocessError):
            return False

    @classmethod
    def cleanup(cls) -> None:
        _kill_spawned()

    @classmethod
    def inhibit(cls, duration_seconds: int) -> subprocess.Popen:
        helper = Path(__file__).parent / "_dbus_inhibit.py"
        proc = subprocess.Popen(
            [cls._find_gi_python(), str(helper), str(duration_seconds)],
            start_new_session=True,
            stderr=subprocess.PIPE,
        )
        _write_pidfile(proc.pid)
        cls._warn_if_failed(proc)
        return proc

    @staticmethod
    def _warn_if_failed(proc: subprocess.Popen) -> None:
        """Detect immediate helper exit and surface its error message.

        The helper is fire-and-forget (new session, parent exits right
        away), so a startup failure would otherwise go unnoticed. A brief
        poll distinguishes a process that died from one still initializing.
        """
        time.sleep(0.5)
        if proc.poll() is None:
            return
        error = ""
        if proc.stderr:
            error = proc.stderr.read().decode(errors="replace").strip()
        if error:
            print(f"keep-alive: {error}", file=sys.stderr)
        else:
            print("keep-alive: D-Bus inhibitor failed to start", file=sys.stderr)

    @staticmethod
    def _find_gi_python() -> str:
        """Find a Python executable with PyGObject, preferring sys.executable."""
        for candidate in (sys.executable, "/usr/bin/python3"):
            if not candidate or not os.path.isfile(candidate):
                continue
            try:
                proc = subprocess.Popen(
                    [candidate, "-c", "import gi"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.wait(timeout=3)
                if proc.returncode == 0:
                    return candidate
            except (OSError, subprocess.SubprocessError):
                continue
        return sys.executable


def get_backend() -> type[InhibitorBackend]:
    """Detect and return the best available backend."""
    backends = [
        CaffeinateBackend,
        DBusScreenSaverBackend,
        SystemdInhibitBackend,
    ]

    for backend in backends:
        if backend.available():
            return backend

    print(
        "No suitable backend found. Please install caffeinate (macOS) or systemd-inhibit (Linux)."
    )
    sys.exit(1)
