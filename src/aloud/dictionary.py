"""The Dictionary: words you teach Aloud, and corrections you want applied.

Two entry types, because they do different jobs:

``term``
    A word or phrase you want the model to know — "Anthropic", "Supabase".
    It biases the engine *before* transcription, and afterwards it canonicalises
    its own form, so "supabase" and "Supa base" both become "Supabase".

``correction``
    An explicit mapping: when you hear X, write Y. "cloud code" → "Claude Code".
    Biasing is a nudge and will not catch everything; this is the guaranteed
    path.

Storage is a single JSON file at
``~/Library/Application Support/Aloud/dictionary.json``. It is meant to be
edited by hand as readily as through the UI, so:

* the schema is flat and obvious, with no ids you have to invent — omit ``id``
  and one is generated on load;
* unknown keys are preserved rather than dropped;
* :meth:`Dictionary.reload_if_changed` picks up outside edits, so editing the
  file while the app is running works.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .paths import SUPPORT_DIR, ensure_dirs

log = logging.getLogger(__name__)

DICTIONARY_FILE = SUPPORT_DIR / "dictionary.json"

TERM = "term"
CORRECTION = "correction"
KINDS = (TERM, CORRECTION)

SCHEMA_VERSION = 1

_SPLIT_RE = re.compile(r"[\s\-_]+")


class DictionaryError(ValueError):
    """Raised for an entry that cannot be stored or matched."""


@dataclass
class Entry:
    """One dictionary entry.

    ``pattern`` and ``replacement`` are what the correction pass actually uses;
    the two kinds differ only in how those are derived, which is what keeps the
    matching code from having to care about the distinction.
    """

    kind: str = TERM
    term: str = ""
    heard: str = ""
    write: str = ""
    enabled: bool = True
    notes: str = ""
    id: str = ""
    created_at: float = 0.0
    hits: int = 0
    #: Anything the file contained that this version does not understand.
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:12]
        if not self.created_at:
            self.created_at = time.time()
        self.validate()

    # -- derived ----------------------------------------------------------

    @property
    def pattern(self) -> str:
        """The phrase to look for in a transcript."""
        return self.term if self.kind == TERM else self.heard

    @property
    def replacement(self) -> str:
        """The text to write in its place."""
        return self.term if self.kind == TERM else self.write

    @property
    def words(self) -> List[str]:
        return [word for word in _SPLIT_RE.split(self.pattern.strip()) if word]

    @property
    def label(self) -> str:
        """How the entry reads in a list."""
        if self.kind == TERM:
            return self.term
        return f"{self.heard} → {self.write}"

    # -- validation --------------------------------------------------------

    def validate(self) -> None:
        if self.kind not in KINDS:
            raise DictionaryError(f"Unknown entry kind {self.kind!r}; expected one of {KINDS}")
        if self.kind == TERM:
            if not self.term.strip():
                raise DictionaryError("A term entry needs a word or phrase.")
        else:
            if not self.heard.strip():
                raise DictionaryError("A correction needs something to listen for.")
            if not self.write.strip():
                raise DictionaryError("A correction needs replacement text.")

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = dict(self.extra)
        payload.update(
            {
                "id": self.id,
                "kind": self.kind,
                "enabled": self.enabled,
                "created_at": round(self.created_at, 3),
            }
        )
        if self.kind == TERM:
            payload["term"] = self.term
        else:
            payload["heard"] = self.heard
            payload["write"] = self.write
        if self.notes:
            payload["notes"] = self.notes
        if self.hits:
            payload["hits"] = self.hits
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Entry":
        known = {"id", "kind", "term", "heard", "write", "enabled", "notes", "created_at", "hits"}
        # A hand-written entry may omit `kind`; infer it from which keys are present.
        kind = payload.get("kind")
        if kind not in KINDS:
            kind = CORRECTION if ("heard" in payload or "write" in payload) else TERM
        return cls(
            kind=kind,
            term=str(payload.get("term", "")),
            heard=str(payload.get("heard", "")),
            write=str(payload.get("write", "")),
            enabled=bool(payload.get("enabled", True)),
            notes=str(payload.get("notes", "")),
            id=str(payload.get("id", "")),
            created_at=float(payload.get("created_at", 0) or 0),
            hits=int(payload.get("hits", 0) or 0),
            extra={k: v for k, v in payload.items() if k not in known},
        )


class Dictionary:
    """An ordered collection of entries, backed by an editable JSON file."""

    def __init__(self, entries: Optional[Iterable[Entry]] = None, path: Optional[Path] = None):
        self._entries: List[Entry] = list(entries or [])
        self.path = path or DICTIONARY_FILE
        self._mtime: float = 0.0

    # -- container ---------------------------------------------------------

    def __iter__(self) -> Iterator[Entry]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> List[Entry]:
        return list(self._entries)

    def get(self, entry_id: str) -> Optional[Entry]:
        return next((entry for entry in self._entries if entry.id == entry_id), None)

    def enabled(self) -> List[Entry]:
        return [entry for entry in self._entries if entry.enabled]

    # -- editing -----------------------------------------------------------

    def add(self, entry: Entry) -> Entry:
        if self.get(entry.id):
            raise DictionaryError(f"An entry with id {entry.id} already exists.")
        self._entries.append(entry)
        return entry

    def add_term(self, term: str, notes: str = "") -> Entry:
        return self.add(Entry(kind=TERM, term=term.strip(), notes=notes))

    def add_correction(self, heard: str, write: str, notes: str = "") -> Entry:
        return self.add(Entry(kind=CORRECTION, heard=heard.strip(), write=write.strip(), notes=notes))

    def update(self, entry_id: str, **changes: Any) -> Entry:
        entry = self.get(entry_id)
        if entry is None:
            raise DictionaryError(f"No entry with id {entry_id}.")
        for key, value in changes.items():
            if not hasattr(entry, key):
                raise DictionaryError(f"Entries have no field {key!r}.")
            setattr(entry, key, value)
        entry.validate()
        return entry

    def remove(self, entry_id: str) -> bool:
        before = len(self._entries)
        self._entries = [entry for entry in self._entries if entry.id != entry_id]
        return len(self._entries) != before

    def record_hit(self, entry_id: str, count: int = 1) -> None:
        """Note that an entry fired, so the UI can show what is earning its keep."""
        entry = self.get(entry_id)
        if entry is not None:
            entry.hits += count

    # -- search ------------------------------------------------------------

    def search(self, query: str) -> List[Entry]:
        """Case-insensitive substring match over every text field of an entry."""
        needle = query.strip().lower()
        if not needle:
            return self.entries
        return [
            entry
            for entry in self._entries
            if needle in " ".join((entry.term, entry.heard, entry.write, entry.notes)).lower()
        ]

    # -- engine biasing ----------------------------------------------------

    def bias_terms(self, limit: int = 12) -> List[str]:
        """The short vocabulary list handed to the engine before transcription.

        Deliberately capped. A long prompt makes these models drift and invent
        text over quiet audio, which costs more than the biasing gains. Newest
        entries win the limited slots: the thing you just taught it is the thing
        you are about to say.
        """
        seen: set[str] = set()
        chosen: List[str] = []
        for entry in sorted(self.enabled(), key=lambda e: e.created_at, reverse=True):
            phrase = entry.replacement.strip()
            key = phrase.lower()
            if not phrase or key in seen:
                continue
            seen.add(key)
            chosen.append(phrase)
            if len(chosen) >= limit:
                break
        return chosen

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "entries": [entry.to_dict() for entry in self._entries],
        }

    def save(self) -> None:
        ensure_dirs()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a crash mid-save cannot truncate the file the
        # user may also have open in an editor.
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
        self._mtime = self._current_mtime()

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Dictionary":
        path = path or DICTIONARY_FILE
        dictionary = cls(path=path)
        if not path.exists():
            dictionary.save()
            return dictionary
        dictionary._read()
        return dictionary

    def reload_if_changed(self) -> bool:
        """Re-read the file if it changed on disk. Returns True when it did.

        The file is advertised as hand-editable, so an edit made outside the app
        has to take effect without a restart.
        """
        if self._current_mtime() == self._mtime:
            return False
        self._read()
        return True

    # -- internals ---------------------------------------------------------

    def _current_mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0

    def _read(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read %s (%s); keeping the entries in memory", self.path, exc)
            return

        raw_entries = payload.get("entries") if isinstance(payload, dict) else payload
        if not isinstance(raw_entries, list):
            log.warning("%s has no entries list; ignoring", self.path)
            return

        entries: List[Entry] = []
        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, dict):
                log.warning("Skipping entry %d in %s: not an object", index, self.path)
                continue
            try:
                entries.append(Entry.from_dict(raw))
            except DictionaryError as exc:
                log.warning("Skipping entry %d in %s: %s", index, self.path, exc)

        self._entries = entries
        self._mtime = self._current_mtime()
