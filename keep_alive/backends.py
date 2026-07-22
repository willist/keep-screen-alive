import os
import shutil
import signal
import subprocess
import sys
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
    """Linux systemd-inhibit backend."""

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


def get_backend() -> type[InhibitorBackend]:
    """Detect and return the best available backend."""
    backends = [
        CaffeinateBackend,
        SystemdInhibitBackend,
    ]

    for backend in backends:
        if backend.available():
            return backend

    print(
        "No suitable backend found. Please install caffeinate (macOS) or systemd-inhibit (Linux)."
    )
    sys.exit(1)
