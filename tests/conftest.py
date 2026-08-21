"""Make the macOS-only modules importable off a Mac."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import stubs  # noqa: E402  (tests/ is on sys.path via rootdir)

STUBBED = stubs.install()
