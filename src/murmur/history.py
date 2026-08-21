"""An append-only record of what was dictated.

Kept local, in JSON Lines, so it is trivial to grep, trivial to delete, and
ready to feed a future "learn my vocabulary" pass.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List

from .paths import HISTORY_FILE, ensure_dirs

log = logging.getLogger(__name__)


def record(text: str, engine: str, seconds: float, max_entries: int = 500) -> None:
    ensure_dirs()
    entry = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "engine": engine,
        "seconds": round(seconds, 3),
        "text": text,
    }
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("Could not append to the history file")
        return
    _trim(max_entries)


def _trim(max_entries: int) -> None:
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) <= max_entries:
        return
    HISTORY_FILE.write_text("\n".join(lines[-max_entries:]) + "\n", encoding="utf-8")


def recent(limit: int = 10) -> List[Dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    entries = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(entries))
