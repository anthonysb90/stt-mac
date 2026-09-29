"""An append-only record of what was dictated.

Kept local, in JSON Lines, so it is trivial to grep, trivial to delete, and
ready to feed a future "learn my vocabulary" pass.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .paths import HISTORY_FILE, ensure_dirs

log = logging.getLogger(__name__)


def record(
    text: str,
    engine: str,
    seconds: float,
    max_entries: int = 500,
    corrections: Optional[List[Dict[str, Any]]] = None,
    raw: str = "",
) -> None:
    """Append one dictation.

    ``corrections`` and ``raw`` are stored only when the Dictionary actually
    changed something. That is what lets the history view answer the question
    the Dictionary exists to raise -- is any of this doing anything? -- without
    doubling the size of every ordinary entry.
    """
    ensure_dirs()
    entry: Dict[str, Any] = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "engine": engine,
        "seconds": round(seconds, 3),
        "text": text,
    }
    if corrections:
        entry["corrections"] = corrections
        if raw and raw != text:
            entry["raw"] = raw
    try:
        with HISTORY_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        log.exception("Could not append to the history file")
        return
    _trim(max_entries)


def _trim(max_entries: int) -> None:
    try:
        # errors="replace": one damaged byte must not make the file unreadable.
        lines = HISTORY_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if len(lines) <= max_entries:
        return
    # Written aside and renamed into place: rewriting in place meant a crash
    # or a full disk mid-write truncated the whole history.
    temporary = HISTORY_FILE.with_name(HISTORY_FILE.name + ".tmp")
    try:
        temporary.write_text("\n".join(lines[-max_entries:]) + "\n", encoding="utf-8")
        temporary.replace(HISTORY_FILE)
    except OSError:
        log.exception("Could not trim the history file")
        temporary.unlink(missing_ok=True)


def recent(limit: int = 10) -> List[Dict[str, Any]]:
    if not HISTORY_FILE.exists():
        return []
    entries = []
    try:
        text = HISTORY_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log.exception("Could not read the history file")
        return []
    for line in text.splitlines()[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(entries))
