import shutil
import subprocess
import sys
import warnings
from abc import ABC, abstractmethod

import dateparser


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


def main():
    input_value = " ".join(sys.argv[1:])
    parser_settings = {
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": True,
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        now = dateparser.parse("now", settings=parser_settings)
        parser_settings["RELATIVE_BASE"] = now
        later = dateparser.parse(input_value, settings=parser_settings)

    if later is None:
        print("Missing a target")
        sys.exit(1)

    if now >= later:
        print(f"{later} is in the past. It is currently {now}")
        sys.exit(1)

    diff = (later - now).seconds

    backend = get_backend()
    backend.cleanup()
    backend.inhibit(diff)

    print(f"Keeping alive until {later:%I:%M%p %Z, %b %d, %Y}")


if __name__ == "__main__":
    main()
