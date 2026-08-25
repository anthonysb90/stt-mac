"""Write Contents/Info.plist for the hand-assembled bundle.

Reads the ``PLIST`` dict straight out of ``setup.py`` -- the single source of
truth for bundle metadata -- rather than duplicating it here, so the two
build paths (this one and the legacy ``setup.py py2app`` path) cannot drift
apart silently.

``NSPrincipalClass`` is dropped: it tells ``NSApplicationMain`` which
Objective-C class to instantiate, and only matters for a process that calls
that C-level bootstrap directly. This bundle's executable links Python in
directly and calls ``Py_BytesMain()``, which sets up ``NSApplication``
itself, in PyObjC, from ``aloud/app.py`` -- ``NSApplicationMain`` is never
called, so the key would be inert at best.

    python3 scripts/write_plist.py <output-path>
"""

from __future__ import annotations

import importlib.util
import plistlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_plist() -> dict:
    spec = importlib.util.spec_from_file_location("aloud_setup", ROOT / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plist = dict(module.PLIST)
    plist.pop("NSPrincipalClass", None)
    plist["CFBundleExecutable"] = "Aloud"
    plist["CFBundleIconFile"] = "Aloud.icns"
    return plist


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: write_plist.py <output-path>", file=sys.stderr)
        return 2
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as handle:
        plistlib.dump(load_plist(), handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
