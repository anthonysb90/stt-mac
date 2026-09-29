"""Timed transcript pieces: building them, and cutting them to subtitle size.

Engines disagree about what they hand back. faster-whisper and the OpenAI API
return sentence-ish segments; Deepgram, AssemblyAI and ElevenLabs return one
entry per *word*. Subtitles and timestamped notes need something in between —
a line you can read in the time it is on screen — so word lists are grouped
here, and anything too long for a subtitle is split here, once, rather than in
every engine.

Pure Python, no AppKit, so all of it is tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .engines.base import Segment

#: Where a sentence ends. A group breaks after one of these.
SENTENCE_END = (".", "?", "!", "…")

#: A pause this long starts a new group even mid-sentence, because that is
#: where a reader expects the next line to begin.
DEFAULT_MAX_GAP = 1.0

#: Longest group built from words, in characters. About two lines of text.
DEFAULT_MAX_CHARS = 120

#: Subtitle limits. 42 characters is the broadcast line length, two lines per
#: cue; seven seconds is about as long as a cue can sit before it feels stuck.
SUBTITLE_MAX_CHARS = 84
SUBTITLE_MAX_SECONDS = 7.0


@dataclass
class Word:
    """One timed word, as the word-level engines report it."""

    start: float
    end: float
    text: str
    speaker: str = ""


def from_words(
    words: Iterable[Word],
    max_chars: int = DEFAULT_MAX_CHARS,
    max_gap: float = DEFAULT_MAX_GAP,
) -> List[Segment]:
    """Group words into readable segments.

    A group ends at the end of a sentence, at a pause longer than ``max_gap``,
    when the speaker changes, or when it would pass ``max_chars``. Speaker
    changes always win: a line that starts with one voice and ends with another
    is wrong in a way no other break is.
    """
    segments: List[Segment] = []
    current: List[Word] = []

    def flush() -> None:
        if not current:
            return
        text = " ".join(w.text.strip() for w in current if w.text.strip())
        if text:
            segments.append(Segment(current[0].start, current[-1].end, text, current[0].speaker))
        current.clear()

    for word in words:
        if not word.text.strip():
            continue
        if current:
            previous = current[-1]
            length = sum(len(w.text) + 1 for w in current) + len(word.text)
            if (
                word.speaker != previous.speaker
                or word.start - previous.end > max_gap
                or length > max_chars
            ):
                flush()
        current.append(word)
        if word.text.rstrip().endswith(SENTENCE_END):
            flush()
    flush()
    return segments


def offset(segments: Sequence[Segment], seconds: float) -> List[Segment]:
    """Move every segment later by ``seconds`` — for a file sent in chunks."""
    return [segment.shifted(seconds) for segment in segments]


def text_of(segments: Sequence[Segment]) -> str:
    return " ".join(s.text.strip() for s in segments if s.text.strip())


def for_subtitles(
    segments: Sequence[Segment],
    max_chars: int = SUBTITLE_MAX_CHARS,
    max_seconds: float = SUBTITLE_MAX_SECONDS,
) -> List[Segment]:
    """Split segments that are too long to read as one subtitle.

    Time is shared out in proportion to characters. That is an estimate — the
    engine did not say where inside a segment each word fell — but speech rate
    is steady enough within a sentence that the cue lands within a word or two
    of where it should.
    """
    out: List[Segment] = []
    for segment in segments:
        duration = max(segment.end - segment.start, 0.0)
        if len(segment.text) <= max_chars and duration <= max_seconds:
            out.append(segment)
            continue

        pieces_by_length = max(1, -(-len(segment.text) // max_chars))
        pieces_by_time = max(1, int(-(-duration // max_seconds))) if max_seconds > 0 else 1
        count = max(pieces_by_length, pieces_by_time)
        word_count = len(segment.text.split())
        # Uneven word lengths can leave one piece over a limit; add pieces
        # until none is, or every piece is a single word and it cannot help.
        while True:
            timed = _timed_pieces(segment, _split_words(segment.text, count))
            fits = all(
                len(p.text) <= max_chars and p.end - p.start <= max_seconds for p in timed
            )
            if fits or count >= word_count:
                break
            count += 1
        out.extend(timed)
    return out


def _timed_pieces(segment: Segment, pieces: List[str]) -> List[Segment]:
    """Share ``segment``'s time out over ``pieces`` in proportion to length."""
    duration = max(segment.end - segment.start, 0.0)
    total_chars = sum(len(p) for p in pieces) or 1
    timed = []
    cursor = segment.start
    for index, piece in enumerate(pieces):
        end = segment.end if index == len(pieces) - 1 else cursor + duration * len(piece) / total_chars
        timed.append(Segment(cursor, end, piece, segment.speaker))
        cursor = end
    return timed


def _split_words(text: str, parts: int) -> List[str]:
    """Split ``text`` into about ``parts`` pieces of similar length, on spaces."""
    words = text.split()
    if parts <= 1 or len(words) <= 1:
        return [text.strip()]
    target = len(text) / parts
    pieces: List[str] = []
    current: List[str] = []
    length = 0
    for word in words:
        if current and length + len(word) > target and len(pieces) < parts - 1:
            pieces.append(" ".join(current))
            current, length = [], 0
        current.append(word)
        length += len(word) + 1
    if current:
        pieces.append(" ".join(current))
    return pieces
