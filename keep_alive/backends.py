import shutil
import subprocess
import sys
from abc import ABC, abstractmethod


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
        subprocess.run(
            [
                "killall",
                "caffeinate",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @classmethod
    def inhibit(cls, duration_seconds: int) -> subprocess.Popen:
        return subprocess.Popen(
            [
                "caffeinate",
                "-d",
                "-u",
                "-t",
                str(duration_seconds),
            ],
            start_new_session=True,
        )


class SystemdInhibitBackend(InhibitorBackend):
    """Linux systemd-inhibit backend."""

    @classmethod
    def available(cls) -> bool:
        return shutil.which("systemd-inhibit") is not None

    @classmethod
    def cleanup(cls) -> None:
        # Don't kill existing processes - let them expire naturally
        pass

    @classmethod
    def inhibit(cls, duration_seconds: int) -> subprocess.Popen:
        return subprocess.Popen(
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
