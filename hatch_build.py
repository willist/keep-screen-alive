"""Hatchling build hook that generates man pages during the build.

Generates groff man pages from the click CLI definition using click-man,
then adds them to the build artifacts so they ship in the sdist.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ManPageHook(BuildHookInterface):
    def initialize(self, version, build_data):
        from click_man.core import write_man_pages

        # Make the source tree importable for the build environment.
        root = str(Path(__file__).resolve().parent)
        sys.path.insert(0, root)

        from keep_alive.run import cli

        pkg_version = self.metadata.version
        tmpdir = tempfile.mkdtemp(prefix="keep-alive-man-")
        write_man_pages("keep-alive", cli, version=pkg_version, target_dir=tmpdir)

        man_files = sorted(f for f in Path(tmpdir).iterdir() if f.suffix == ".1")
        for f in man_files:
            dest = f"share/man/man1/{f.name}"
            build_data["force_include"][str(f)] = dest
