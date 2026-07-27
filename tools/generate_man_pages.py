"""Generate man pages for keep-alive using click-man's core API.

Writes a man page for the top-level group and one per subcommand to
``share/man/man1/``. Run via ``uv run python tools/generate_man_pages.py``
during development or from the hatchling build hook.
"""

from __future__ import annotations

import sys
from datetime import datetime
from importlib.metadata import version as _pkg_version
from pathlib import Path

# Ensure the package is importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from click_man.core import write_man_pages

from keep_alive.run import cli


def main(target_dir: str | None = None) -> Path:
    pkg_version = _pkg_version("keep-screen-alive")
    date_str = datetime.now().strftime("%Y-%m-%d")
    out_dir = (
        Path(target_dir)
        if target_dir
        else Path(__file__).resolve().parent.parent / "share" / "man" / "man1"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    write_man_pages("keep-alive", cli, version=pkg_version, target_dir=str(out_dir), date=date_str)
    return out_dir


if __name__ == "__main__":
    out = main()
    files = sorted(f for f in out.iterdir() if f.suffix == ".1")
    print(f"Generated {len(files)} man page(s) in {out}:")
    for f in files:
        print(f"  {f.name}")
