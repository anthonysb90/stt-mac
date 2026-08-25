"""Making command-line tools findable in an app launched from the Dock.

A GUI process does not inherit your shell. Its ``PATH`` is whatever launchd
hands it — ``/usr/bin:/bin:/usr/sbin:/sbin`` — so Homebrew's ``/opt/homebrew/bin``
is simply not on it. The symptom is one of the most confusing kinds there is:
``aloud doctor`` in Terminal reports ffmpeg found and the engine ready, and the
same machine, running the same code from ``/Applications``, says "ffmpeg not
found" and refuses to transcribe.

The same trap as API keys in :mod:`aloud.secrets`, in a different disguise.

Repairing ``PATH`` rather than resolving one absolute path is the point.
:func:`aloud.media.ffmpeg_path` already looks in the Homebrew prefixes and can
be handed an absolute path — but we are not the only caller. ``parakeet-mlx``
runs ffmpeg itself, from inside the library, with no argument for us to fill
in. The only way to reach a subprocess we do not spawn is the environment it
inherits.

Called once at startup, from both the app and the CLI, before any engine loads.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Iterable, List, Sequence

log = logging.getLogger(__name__)

#: Where third-party command-line tools live on macOS, in the order a shell
#: would normally find them. Absent directories are skipped, so listing all of
#: them costs nothing on a machine that uses only one.
TOOL_DIRS: Sequence[str] = (
    "/opt/homebrew/bin",   # Homebrew on Apple Silicon
    "/opt/homebrew/sbin",
    "/usr/local/bin",      # Homebrew on Intel, and most manual installs
    "/usr/local/sbin",
    "/opt/local/bin",      # MacPorts
    "/opt/local/sbin",
    "/usr/bin",
    "/bin",
)


def repair(extra: Iterable[str] = ()) -> List[str]:
    """Add the usual tool directories to ``PATH``. Returns what was added.

    Appends rather than prepends: a directory the user put on ``PATH``
    deliberately should keep winning over one we guessed at.
    """
    current = os.environ.get("PATH", "")
    present = [part for part in current.split(os.pathsep) if part]
    known = set(present)

    added = []
    for directory in list(extra) + list(TOOL_DIRS):
        directory = str(directory).strip()
        if not directory or directory in known:
            continue
        if not Path(directory).is_dir():
            continue
        present.append(directory)
        known.add(directory)
        added.append(directory)

    if added:
        os.environ["PATH"] = os.pathsep.join(present)
        log.info("Added to PATH: %s", ", ".join(added))
    return added


def describe() -> str:
    """``PATH`` as one line, for ``aloud doctor``."""
    return os.environ.get("PATH", "") or "(empty)"


def repair_locale() -> str:
    """Make UTF-8 the default text encoding, as it is in any terminal.

    The third face of the same trap. A Dock launch has no ``LANG`` or
    ``LC_CTYPE``, so Python's locale encoding can come up ASCII -- and then
    every ``open()`` that does not name an encoding dies on the first
    non-ASCII byte. Our own file I/O names utf-8 explicitly, but libraries do
    not: parakeet-mlx reads its model config with a bare ``open()``, which is
    how a Mac that transcribed happily from the terminal produced

        'ascii' codec can't decode byte 0xe2 in position 8352

    from the app in /Applications. The bundle also sets PYTHONUTF8=1 via
    LSEnvironment in its Info.plist, which is the complete fix when launching
    through LaunchServices; this covers every other way in (make run before
    the plist existed, a LaunchAgent, an SSH session with LANG stripped).

    Returns the encoding in effect afterwards, for the doctor report.
    """
    import locale

    # Exported regardless of our own mode: child processes (ffmpeg,
    # whisper-cli) run their own locale lookup and inherit only the
    # environment, not the interpreter's UTF-8 flag.
    os.environ.setdefault("LC_CTYPE", "en_US.UTF-8")
    os.environ.setdefault("LANG", "en_US.UTF-8")

    if sys.flags.utf8_mode:
        return "utf-8 (PYTHONUTF8)"

    try:
        current = (locale.getpreferredencoding(False) or "").lower()
    except Exception:
        current = ""
    if current.replace("-", "") != "utf8":
        # macOS accepts the bare "UTF-8" locale name; Linux spells it C.UTF-8.
        for name in ("en_US.UTF-8", "UTF-8", "C.UTF-8"):
            try:
                locale.setlocale(locale.LC_CTYPE, name)
                log.info("Locale encoding was %r; set LC_CTYPE to %s", current, name)
                break
            except locale.Error:
                continue

    try:
        return locale.getpreferredencoding(False)
    except Exception:
        return "unknown"
