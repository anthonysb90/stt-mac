"""Watch folders: new recordings transcribe themselves.

Point Aloud at a folder — "Sunday Recordings", a recorder's import folder, a
shared Dropbox folder — and every new audio or video file that appears there
is transcribed, saved to Transcripts, and written back as a document in a
subfolder beside it (``Transcripts/`` by default; Word unless you choose
otherwise). Nothing to open, nothing to click.

The care is all in *when* a file counts as new and finished:

* **Only new files.** Adding a folder records what is already in it, so a
  folder of two hundred old sermons is not suddenly queued — unless you ask
  for that when adding it.
* **Only finished files.** A recording still copying in from a card, or still
  uploading from a phone, grows between looks. A file is taken only once its
  size and date have stayed the same across two looks and it is a few seconds
  old.
* **Each file once.** What has been queued, finished or failed is kept in
  ``watch-state.json``, so a restart does not transcribe everything again, and
  a file that failed is not retried in a loop. Edit or delete that file to
  force a retry.
* **Never its own output.** The export subfolder, hidden files, Office lock
  files and iCloud placeholders are ignored.

Pure Python with its own thread; tested without AppKit.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import media
from .paths import SUPPORT_DIR

log = logging.getLogger(__name__)

STATE_FILE = SUPPORT_DIR / "watch-state.json"
DEFAULT_SUBFOLDER = "Transcripts"
#: A file must be at least this old, as well as unchanged, to be taken.
SETTLE_SECONDS = 5.0
POLL_SECONDS = 15.0


@dataclass
class Settings:
    folders: List[Path]
    export: str = "docx"          # a format key from aloud.export, or "" for none
    layout: str = "manuscript"
    subfolder: str = DEFAULT_SUBFOLDER

    @classmethod
    def from_config(cls, config) -> "Settings":
        return cls(
            folders=[Path(p).expanduser() for p in (config.get("watch.folders", []) or [])],
            export=str(config.get("watch.export", "docx") or ""),
            layout=str(config.get("watch.layout", "manuscript") or "manuscript"),
            subfolder=str(config.get("watch.subfolder", DEFAULT_SUBFOLDER) or DEFAULT_SUBFOLDER),
        )

    def exports_for(self, source: Path) -> List[Tuple[str, str, str]]:
        if not self.export:
            return []
        return [(self.export, self.layout, str(source.parent / self.subfolder))]


def _signature(path: Path) -> Optional[Tuple[int, float]]:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime


def _ignored(path: Path, subfolder: str) -> bool:
    name = path.name
    return (
        name.startswith((".", "~$"))
        or name.endswith(".icloud")
        or not media.is_supported(path)
    )


class Watcher:
    """Looks at the watched folders every so often and queues what is new.

    ``queue(path, exports)`` is called for each finished new file and returns
    whether it was accepted (the controller's ``transcribe_file``). Results
    come back through :meth:`finished`, which the app calls from its
    controller observer.
    """

    def __init__(self, settings: Settings, queue: Callable[[Path, list], bool],
                 state_file: Optional[Path] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.settings = settings
        self._queue = queue
        self._state_file = state_file or STATE_FILE
        self._clock = clock
        self._lock = threading.Lock()
        self._state: Dict[str, dict] = self._load()
        #: path -> signature seen last look, for files not yet settled
        self._pending: Dict[str, Tuple[int, float]] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- state on disk ------------------------------------------------------

    def _load(self) -> Dict[str, dict]:
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._state_file.with_name(self._state_file.name + ".tmp")
            temporary.write_text(json.dumps(self._state, indent=1) + "\n", encoding="utf-8")
            temporary.replace(self._state_file)
        except OSError:
            log.exception("Could not save the watch-folder state")

    def status(self, path: Path) -> str:
        return (self._state.get(str(path)) or {}).get("status", "")

    # -- folders ------------------------------------------------------------

    def files_in(self, folder: Path) -> List[Path]:
        """Candidate recordings directly in ``folder`` (not its subfolders)."""
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            return []
        return [p for p in entries
                if p.is_file() and not _ignored(p, self.settings.subfolder)]

    def adopt(self, folder: Path, include_existing: bool = False) -> int:
        """Start watching ``folder``. Returns how many files are already there.

        Unless ``include_existing``, those files are marked as seen, so only
        recordings added from now on are transcribed.
        """
        existing = self.files_in(folder)
        if not include_existing:
            with self._lock:
                for path in existing:
                    self._state.setdefault(str(path), {"status": "existing"})
                self._save()
        return len(existing)

    # -- looking ----------------------------------------------------------------

    def scan(self) -> List[Path]:
        """One look at every folder. Returns the files queued this time."""
        queued: List[Path] = []
        now = self._clock()
        for folder in self.settings.folders:
            for path in self.files_in(folder):
                key = str(path)
                if key in self._state:
                    continue
                signature = _signature(path)
                if signature is None:
                    continue
                previous = self._pending.get(key)
                self._pending[key] = signature
                if previous != signature or now - signature[1] < SETTLE_SECONDS:
                    continue  # still arriving, or only just arrived
                if signature[0] == 0:
                    continue
                if self._queue(path, self.settings.exports_for(path)):
                    with self._lock:
                        self._state[key] = {"status": "queued", "at": now}
                    self._pending.pop(key, None)
                    queued.append(path)
        if queued:
            with self._lock:
                self._save()
            log.info("Watch folders: queued %s", ", ".join(p.name for p in queued))
        return queued

    def finished(self, path: Path, ok: bool, detail: str = "") -> None:
        """Record how a queued file ended, so it is not taken again."""
        key = str(path)
        with self._lock:
            if key not in self._state:
                return
            self._state[key] = {"status": "done" if ok else "failed",
                                "at": self._clock(), **({"detail": detail} if detail else {})}
            self._save()

    # -- the thread -----------------------------------------------------------

    def start(self, interval: float = POLL_SECONDS) -> None:
        if self._thread is not None or not self.settings.folders:
            return

        def run() -> None:
            while not self._stop.wait(interval):
                try:
                    self.scan()
                except Exception:
                    log.exception("Watch-folder scan failed")

        self._thread = threading.Thread(target=run, name="aloud-watch", daemon=True)
        self._thread.start()
        log.info("Watching %d folder(s)", len(self.settings.folders))

    def stop(self) -> None:
        self._stop.set()
