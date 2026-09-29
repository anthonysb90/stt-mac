"""Learning from your edits: a fix you make once becomes a Dictionary entry.

When you correct a transcript — "Romans ate" to "Romans 8", "cloud code" to
"Claude Code" — the same mistake will come back next week, because the engine
has not changed. This compares the text before and after an edit, finds the
short word-for-word swaps, and offers them as Dictionary corrections so the
fix applies automatically from then on.

Only swaps that look like mishearings are offered: a few words replaced by a
few words. Deleting a sentence, rewriting a paragraph or adding a thought is an
edit, not a correction, and offering to "always" apply it would be wrong.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Iterable, List

#: Longest phrase, in words, on either side of a suggested correction.
MAX_WORDS = 4

_WORD = re.compile(r"\S+")
_EDGE_PUNCTUATION = ".,;:!?\"'“”‘’()"


@dataclass(frozen=True)
class Suggestion:
    heard: str   # what the transcript said
    write: str   # what you changed it to
    count: int   # how many times this swap was made in the edit


def _words(text: str) -> List[str]:
    return _WORD.findall(text)


def _bare(word: str) -> str:
    return word.strip(_EDGE_PUNCTUATION)


def suggestions(before: str, after: str) -> List[Suggestion]:
    """Short replacements between two versions of a text, most frequent first."""
    old, new = _words(before), _words(after)
    # Compared with case intact. Lower-cased, "cloud code" -> "Claude Code"
    # matched on "code" and came out as "cloud" -> "Claude" -- a rule that
    # would rewrite every "cloud" ever said. Case-only changes are dropped
    # below instead.
    matcher = difflib.SequenceMatcher(a=[_bare(w) for w in old],
                                      b=[_bare(w) for w in new], autojunk=False)
    counts: dict = {}
    order: List[tuple] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace":
            continue
        if not (0 < i2 - i1 <= MAX_WORDS and 0 < j2 - j1 <= MAX_WORDS):
            continue
        heard = " ".join(_bare(w) for w in old[i1:i2]).strip()
        write = " ".join(_bare(w) for w in new[j1:j2]).strip()
        if not heard or not write or heard.lower() == write.lower():
            continue  # nothing, or only capitalisation, changed
        key = (heard.lower(), write)
        if key not in counts:
            order.append(key)
            counts[key] = [heard, 0]
        counts[key][1] += 1
    found = [Suggestion(counts[k][0], k[1], counts[k][1]) for k in order]
    found.sort(key=lambda s: -s.count)
    return found


def new_suggestions(found: Iterable[Suggestion], dictionary) -> List[Suggestion]:
    """Leave out swaps the Dictionary already makes."""
    known = set()
    for entry in getattr(dictionary, "entries", []):
        heard = str(getattr(entry, "heard", "") or getattr(entry, "term", "")).lower()
        known.add((heard, str(getattr(entry, "write", "") or getattr(entry, "term", ""))))
    return [s for s in found if (s.heard.lower(), s.write) not in known]


def apply_to_segments(segments, replacements: Iterable[Suggestion]):
    """Carry accepted corrections into timed segments, keeping their timings.

    Case-insensitive, whole-word; a correction that spans two segments is
    missed here, which leaves those two segments as they were -- not wrong,
    only uncorrected.
    """
    from .engines.base import Segment

    rules = [(re.compile(r"(?<!\w)" + re.escape(s.heard) + r"(?!\w)", re.IGNORECASE), s.write)
             for s in replacements]
    out = []
    for segment in segments:
        text = segment.text
        for pattern, write in rules:
            text = pattern.sub(write, text)
        out.append(Segment(segment.start, segment.end, text, segment.speaker))
    return out


def reflow(segments, new_text: str):
    """Put edited text back into timed segments, keeping every timing.

    The layouts with timestamps or speakers are built from segments, so an
    edit made to the plain text would otherwise vanish from them. Each word of
    the edited text is lined up with the original words (the same diff as
    above) and placed in the segment its counterpart came from; a word added
    between two others joins the segment of the word before it. A segment
    whose words were all deleted is dropped.
    """
    from .engines.base import Segment

    owners: List[int] = []
    old: List[str] = []
    for index, segment in enumerate(segments):
        for word in _words(segment.text):
            old.append(_bare(word))
            owners.append(index)
    new = _words(new_text)
    if not old:
        return list(segments)

    placed: List[List[str]] = [[] for _ in segments]
    matcher = difflib.SequenceMatcher(a=old, b=[_bare(w) for w in new], autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            continue
        for k, j in enumerate(range(j1, j2)):
            if tag == "insert":
                source = i1 - 1 if i1 > 0 else 0
            else:  # equal or replace: spread the new words over the old ones
                span = i2 - i1
                source = i1 + min(span - 1, k * span // max(j2 - j1, 1))
            placed[owners[source]].append(new[j])

    return [
        Segment(segment.start, segment.end, " ".join(words), segment.speaker)
        for segment, words in zip(segments, placed) if words
    ]


class Teacher:
    """Turns fixes made in a transcript into Dictionary corrections."""

    def __init__(self, controller) -> None:
        self.controller = controller

    def fresh(self, found: Iterable[Suggestion]) -> List[Suggestion]:
        return new_suggestions(found, self.controller.dictionary)

    def teach(self, chosen: Iterable[Suggestion]) -> List[str]:
        """Add each fix. Returns a sentence for each one the Dictionary refused."""
        chosen = list(chosen)
        problems = []
        for suggestion in chosen:
            try:
                self.controller.dictionary.add_correction(
                    suggestion.heard, suggestion.write, notes="Learned from an edit")
            except Exception as exc:  # e.g. an empty or duplicate entry
                problems.append(f"“{suggestion.heard}”: {exc}")
        if len(problems) < len(chosen):
            self.controller.save_dictionary()
        return problems
