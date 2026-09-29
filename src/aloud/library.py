"""The transcript library: every file you transcribe, kept and findable.

A transcription used to live exactly as long as its window. Close it and the
only trace was a line in the dictation history — no timings, no speakers, no
way to export it again. Now every finished file is saved here, and the
Transcripts pane in the main window lists, searches and reopens them.

On disk it is deliberately plain::

    ~/Library/Application Support/Aloud/Transcripts/
        2026-09-29 Sunday Sermon/
            transcript.json   everything: text, the engine's raw text,
                              timed segments, speaker names, where it came from
            transcript.txt    the text, so Finder, Quick Look and Spotlight
                              can read it without Aloud

``transcript.json`` is the source of truth; ``transcript.txt`` is rewritten
from it on every save. The raw engine output is kept alongside the corrected
text, so a Dictionary entry that went wrong can always be checked against what
was actually heard.

No AppKit here. Moving a transcript to the Trash is the view's job (it needs
NSFileManager); :func:`delete` takes the function that does it.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .engines.base import Segment
from .paths import SUPPORT_DIR

log = logging.getLogger(__name__)

LIBRARY_DIR = SUPPORT_DIR / "Transcripts"
DATA_FILE = "transcript.json"
TEXT_FILE = "transcript.txt"
FORMAT_VERSION = 1

#: Characters Finder or other tools choke on in a folder name.
_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f]+')

_lock = threading.RLock()


@dataclass
class Record:
    """One saved transcript."""

    title: str
    text: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: float = field(default_factory=time.time)
    #: What the engine produced, before the Dictionary and cleanup touched it.
    raw_text: str = ""
    segments: List[Segment] = field(default_factory=list)
    #: The engine's speaker label ("A", "0", "speaker_1") -> the name to show.
    speakers: Dict[str, str] = field(default_factory=dict)
    source_name: str = ""
    source_path: str = ""
    audio_seconds: float = 0.0
    engine: str = ""
    engine_label: str = ""
    language: str = ""
    transcribe_seconds: float = 0.0
    corrections: List[Dict[str, Any]] = field(default_factory=list)
    #: Where it is stored. Set by save() and load(); never written to disk.
    folder: Optional[Path] = None

    # -- speakers ------------------------------------------------------------

    @property
    def speaker_labels(self) -> List[str]:
        """Every distinct speaker label, in order of first appearance."""
        seen: List[str] = []
        for segment in self.segments:
            if segment.speaker and segment.speaker not in seen:
                seen.append(segment.speaker)
        return seen

    def speaker_name(self, label: str) -> str:
        """The name to show for ``label``: the one given, or a default."""
        chosen = (self.speakers.get(label) or "").strip()
        return chosen or default_speaker_name(label, self.speaker_labels)

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": FORMAT_VERSION,
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.created_at)),
            "source_name": self.source_name,
            "source_path": self.source_path,
            "audio_seconds": round(self.audio_seconds, 3),
            "engine": self.engine,
            "engine_label": self.engine_label,
            "language": self.language,
            "transcribe_seconds": round(self.transcribe_seconds, 3),
            "speakers": dict(self.speakers),
            "text": self.text,
            "raw_text": self.raw_text,
            "corrections": list(self.corrections),
            "segments": [
                {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text,
                 **({"speaker": s.speaker} if s.speaker else {})}
                for s in self.segments
            ],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], folder: Optional[Path] = None) -> "Record":
        segments = []
        for item in data.get("segments") or []:
            try:
                segments.append(Segment(float(item["start"]), float(item["end"]),
                                        str(item.get("text", "")), str(item.get("speaker") or "")))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            title=str(data.get("title") or "Untitled"),
            created_at=float(data.get("created_at") or 0.0),
            text=str(data.get("text") or ""),
            raw_text=str(data.get("raw_text") or ""),
            segments=segments,
            speakers={str(k): str(v) for k, v in (data.get("speakers") or {}).items()},
            source_name=str(data.get("source_name") or ""),
            source_path=str(data.get("source_path") or ""),
            audio_seconds=float(data.get("audio_seconds") or 0.0),
            engine=str(data.get("engine") or ""),
            engine_label=str(data.get("engine_label") or ""),
            language=str(data.get("language") or ""),
            transcribe_seconds=float(data.get("transcribe_seconds") or 0.0),
            corrections=list(data.get("corrections") or []),
            folder=folder,
        )


def default_speaker_name(label: str, order: Optional[List[str]] = None) -> str:
    """"Speaker 1", "Speaker 2"… — numbered by first appearance when known.

    Engines number speakers differently (AssemblyAI "A", Deepgram "0",
    ElevenLabs "speaker_0"), and none of those reads well in a document.
    """
    if order and label in order:
        return f"Speaker {order.index(label) + 1}"
    raw = str(label or "").strip()
    tail = raw.rsplit("_", 1)[-1]
    if tail.isdigit():
        return f"Speaker {int(tail) + 1}"
    return f"Speaker {raw}" if len(raw) <= 2 else raw


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def title_from(source_name: str) -> str:
    """"sunday_sermon-final.mp3" -> "sunday sermon-final"."""
    stem = Path(source_name).stem if source_name else ""
    stem = stem.replace("_", " ").strip()
    return stem or "Untitled transcript"


def _folder_name(record: Record) -> str:
    date = time.strftime("%Y-%m-%d", time.localtime(record.created_at))
    title = _UNSAFE.sub(" ", record.title).strip(" .") or "Untitled"
    return f"{date} {title[:80]}".strip()


def _unique(root: Path, name: str) -> Path:
    candidate = root / name
    counter = 2
    while candidate.exists():
        candidate = root / f"{name} ({counter})"
        counter += 1
    return candidate


def save(record: Record, root: Optional[Path] = None) -> Path:
    """Write ``record``, creating its folder the first time. Returns the folder.

    Written to a temporary file and renamed over the old one, so a crash or a
    full disk mid-save leaves the previous version intact rather than half a
    JSON file.
    """
    root = root or LIBRARY_DIR
    with _lock:
        root.mkdir(parents=True, exist_ok=True)
        folder = record.folder
        if folder is None or not folder.is_dir():
            folder = _unique(root, _folder_name(record))
            folder.mkdir(parents=True)
        record.folder = folder
        _atomic_write(folder / DATA_FILE,
                      json.dumps(record.to_dict(), ensure_ascii=False, indent=1) + "\n")
        from . import export

        _atomic_write(folder / TEXT_FILE, export.readable_text(record))
    return folder


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def load(folder: Path) -> Optional[Record]:
    """Read one transcript folder. None when it is not one, or is unreadable."""
    try:
        data = json.loads((folder / DATA_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return Record.from_dict(data, folder=folder)


def all_records(root: Optional[Path] = None) -> List[Record]:
    """Every transcript, newest first. Unreadable folders are skipped, not fatal."""
    root = root or LIBRARY_DIR
    records = []
    try:
        folders = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return []
    for folder in folders:
        record = load(folder)
        if record is not None:
            records.append(record)
        elif (folder / DATA_FILE).exists():
            log.warning("Skipping unreadable transcript in %s", folder)
    records.sort(key=lambda r: r.created_at, reverse=True)
    return records


def find_by_id(record_id: str, root: Optional[Path] = None) -> Optional[Record]:
    return next((r for r in all_records(root) if r.id == record_id), None)


def rename(record: Record, title: str, root: Optional[Path] = None) -> Record:
    """Change the title, and the folder name with it, so Finder agrees."""
    title = title.strip() or record.title
    with _lock:
        record.title = title
        root = root or (record.folder.parent if record.folder else LIBRARY_DIR)
        if record.folder is not None and record.folder.is_dir():
            wanted = root / _folder_name(record)
            if wanted != record.folder:
                target = _unique(root, _folder_name(record))
                record.folder.rename(target)
                record.folder = target
        save(record, root)
    return record


def delete(record: Record, trash: Optional[Callable[[Path], bool]] = None) -> bool:
    """Remove a transcript. ``trash(path)`` moves it to the Trash when given."""
    folder = record.folder
    if folder is None or not folder.exists():
        return False
    with _lock:
        if trash is not None:
            return bool(trash(folder))
        import shutil

        shutil.rmtree(folder, ignore_errors=True)
        return not folder.exists()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@dataclass
class Hit:
    record: Record
    count: int
    snippet: str


def search(query: str, records: List[Record]) -> List[Hit]:
    """Transcripts containing ``query`` in the title, text or speaker names.

    Case-insensitive, and every word must appear somewhere — "sermon grace"
    finds a transcript titled "Sunday sermon" that mentions grace. Ordered by
    how often the words occur, then by date.
    """
    words = [w for w in query.lower().split() if w]
    if not words:
        return [Hit(r, 0, _opening(r.text)) for r in records]
    hits = []
    for record in records:
        haystack = " ".join([record.title, record.text, " ".join(record.speakers.values())]).lower()
        if not all(w in haystack for w in words):
            continue
        count = sum(record.text.lower().count(w) for w in words)
        hits.append(Hit(record, count, snippet(record.text, words[0])))
    hits.sort(key=lambda h: (h.count, h.record.created_at), reverse=True)
    return hits


def snippet(text: str, word: str, width: int = 70) -> str:
    """A little text around the first match, for the search results list."""
    index = text.lower().find(word.lower())
    if index < 0:
        return _opening(text, width * 2)
    start = max(0, index - width)
    end = min(len(text), index + len(word) + width)
    piece = " ".join(text[start:end].split())
    return ("…" if start > 0 else "") + piece + ("…" if end < len(text) else "")


def _opening(text: str, width: int = 140) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[:width].rstrip() + "…"


def find_all(text: str, query: str) -> List[Tuple[int, int]]:
    """``(start, length)`` of every case-insensitive match, for in-window search."""
    if not query:
        return []
    lowered, needle = text.lower(), query.lower()
    found, index = [], lowered.find(needle)
    while index >= 0:
        found.append((index, len(needle)))
        index = lowered.find(needle, index + len(needle))
    return found


class Index:
    """The library, kept in memory, re-reading only what changed on disk.

    Reading every transcript.json each time the Transcripts pane opens is fine
    for ten transcripts and slow for three hundred hour-long ones. Keyed by
    folder and modification time, so a transcript renamed or edited (in Aloud
    or by hand) is picked up and nothing else is re-parsed.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = root
        self._cache: Dict[Path, Tuple[float, Record]] = {}

    @property
    def root(self) -> Path:
        return self._root or LIBRARY_DIR

    def records(self) -> List[Record]:
        seen: Dict[Path, Tuple[float, Record]] = {}
        try:
            folders = [p for p in self.root.iterdir() if p.is_dir()]
        except OSError:
            folders = []
        for folder in folders:
            try:
                stamp = (folder / DATA_FILE).stat().st_mtime
            except OSError:
                continue
            cached = self._cache.get(folder)
            if cached is not None and cached[0] == stamp:
                seen[folder] = cached
                continue
            record = load(folder)
            if record is not None:
                seen[folder] = (stamp, record)
        self._cache = seen
        records = [record for _stamp, record in seen.values()]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records
