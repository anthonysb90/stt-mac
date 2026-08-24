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
