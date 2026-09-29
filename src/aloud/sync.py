"""Keeping the Dictionary in iCloud Drive, so every Mac shares it.

The Dictionary is one JSON file that Aloud already re-reads whenever it changes
on disk. Put that file in iCloud Drive and every Mac signed in to the same
account reads and writes the same list: a correction taught on the office iMac
works on the laptop by the next dictation.

Three things need care, and are handled here rather than trusted to luck:

* **Turning sync on merges, never replaces.** If the other Mac already put a
  Dictionary in iCloud, your entries and its entries are combined.
* **Conflicted copies.** When two Macs save at nearly the same moment, iCloud
  keeps both and names one "dictionary 2.json". Those are merged back into the
  main file and removed, so nothing either Mac added is lost.
* **Files iCloud has not downloaded.** With "Optimise Mac Storage", iCloud can
  replace a file with a placeholder. A missing Dictionary must never be taken
  as an empty one — that would save an empty list over everyone's entries — so
  the download is requested and waited for, and if it does not arrive the
  local copy is used instead.

The one thing a merge cannot know is a deletion: an entry deleted on one Mac
while the other saved a conflicting copy comes back. Delete it again.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Iterable, List, Optional

from . import dictionary as dictionary_module
from .dictionary import Dictionary, Entry

log = logging.getLogger(__name__)

ICLOUD_ROOT = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
FOLDER_NAME = "Aloud"
FILE_NAME = "dictionary.json"


def icloud_available(root: Optional[Path] = None) -> bool:
    """Whether iCloud Drive is switched on for this Mac."""
    return (root or ICLOUD_ROOT).is_dir()


def icloud_path(root: Optional[Path] = None) -> Path:
    return (root or ICLOUD_ROOT) / FOLDER_NAME / FILE_NAME


def dictionary_path(config, root: Optional[Path] = None) -> Path:
    """Where the Dictionary lives now: iCloud when chosen and possible."""
    if config.get("sync.dictionary", "local") == "icloud" and icloud_available(root):
        return icloud_path(root)
    return dictionary_module.DICTIONARY_FILE


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


def _key(entry: Entry):
    return entry.kind, entry.pattern.strip().lower(), entry.replacement.strip()


def merge_entries(*lists: Iterable[Entry]) -> List[Entry]:
    """Every entry from every list, once.

    Two entries are the same when they are the same kind, listen for the same
    words and write the same thing. Of duplicates, the one used more often is
    kept (with the hit counts added), and it stays enabled if either copy was.
    """
    merged: dict = {}
    order: list = []
    for entries in lists:
        for entry in entries:
            key = _key(entry)
            kept = merged.get(key)
            if kept is None:
                merged[key] = entry
                order.append(key)
                continue
            winner, other = (entry, kept) if entry.hits > kept.hits else (kept, entry)
            winner.hits = kept.hits + entry.hits
            winner.enabled = kept.enabled or entry.enabled
            winner.created_at = min(kept.created_at, entry.created_at)
            merged[key] = winner
    return [merged[key] for key in order]


def conflict_copies(path: Path) -> List[Path]:
    """iCloud's copies of ``path`` from simultaneous saves: "dictionary 2.json"."""
    try:
        return sorted(p for p in path.parent.glob(f"{path.stem} *{path.suffix}")
                      if p != path and p.is_file())
    except OSError:
        return []


def fold_in_conflicts(dictionary: Dictionary) -> int:
    """Merge any conflicted copies into ``dictionary``, save, remove them."""
    copies = conflict_copies(dictionary.path)
    if not copies:
        return 0
    lists = [dictionary.entries]
    for copy in copies:
        other = Dictionary(path=copy)
        other._read()
        lists.append(other.entries)
    dictionary._entries = merge_entries(*lists)
    dictionary.save()
    for copy in copies:
        copy.unlink(missing_ok=True)
    log.info("Merged %d conflicted Dictionary cop%s from iCloud", len(copies),
             "y" if len(copies) == 1 else "ies")
    return len(copies)


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------


def placeholder(path: Path) -> Path:
    """The stand-in iCloud leaves when it has evicted a file to save space."""
    return path.with_name(f".{path.name}.icloud")


def ensure_downloaded(path: Path, timeout: float = 15.0) -> bool:
    """True once ``path`` is really on disk. Asks iCloud for it if evicted."""
    if path.exists():
        return True
    if not placeholder(path).exists():
        return False  # simply does not exist yet
    try:
        subprocess.run(["brctl", "download", str(path)], capture_output=True, timeout=10,
                       check=False)
    except (OSError, subprocess.SubprocessError):
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------
# Switching
# ---------------------------------------------------------------------------


def move_to(dictionary: Dictionary, target: Path) -> Dictionary:
    """Point ``dictionary`` at ``target``, merging with what is there.

    The same object is kept -- the Dictionary pane holds a reference to it --
    with its path and entries changed underneath.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    lists = [dictionary.entries]
    if ensure_downloaded(target):
        other = Dictionary(path=target)
        other._read()
        lists.insert(0, other.entries)
    elif placeholder(target).exists():
        raise OSError("iCloud has not downloaded the shared Dictionary yet. "
                      "Open the Aloud folder in iCloud Drive, then try again.")
    dictionary._entries = merge_entries(*lists)
    dictionary.path = target
    dictionary.save()
    fold_in_conflicts(dictionary)
    return dictionary


#: How long launch waits for an evicted Dictionary. Short: this runs while the
#: app starts, and the local copy is a fine stand-in until the next launch.
LAUNCH_WAIT = 3.0


def load(config, root: Optional[Path] = None, timeout: float = LAUNCH_WAIT) -> Dictionary:
    """The Dictionary from wherever the config says it lives.

    If iCloud is chosen but its copy is evicted and does not download, the
    local copy is used for now -- never an empty list saved over the shared
    one.
    """
    path = dictionary_path(config, root)
    if path != dictionary_module.DICTIONARY_FILE and not ensure_downloaded(path, timeout):
        if placeholder(path).exists():
            log.warning("The iCloud Dictionary is not downloaded; using the local copy")
            return Dictionary.load()
    dictionary = Dictionary.load(path)
    if path != dictionary_module.DICTIONARY_FILE:
        fold_in_conflicts(dictionary)
    return dictionary
