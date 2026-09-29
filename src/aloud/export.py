"""Saving a transcript: plain text, subtitles, or notes with timestamps.

The formats that need timings are only offered when the engine supplied them.
An SRT file with every cue at 00:00:00 would open without complaint and be
useless, which is worse than not offering it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from . import segments as seg
from .engines.base import Segment


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    extension: str
    #: Whether this format is meaningless without segment timings.
    needs_timings: bool


TEXT = Format("txt", "Plain Text", "txt", False)
TIMESTAMPED = Format("timestamped", "Text with Timestamps", "txt", True)
SRT = Format("srt", "Subtitles (SRT)", "srt", True)
VTT = Format("vtt", "Web Subtitles (WebVTT)", "vtt", True)

FORMATS = (TEXT, TIMESTAMPED, SRT, VTT)
BY_KEY = {f.key: f for f in FORMATS}


def available(segments: Sequence[Segment]) -> List[Format]:
    """The formats that make sense for this transcript."""
    timed = bool(segments)
    return [f for f in FORMATS if timed or not f.needs_timings]


def render(fmt: Format, text: str, segments: Sequence[Segment]) -> str:
    """The file contents for ``fmt``. Plain text always works."""
    if fmt.needs_timings and not segments:
        raise ValueError(f"{fmt.label} needs timings, and this transcript has none")
    if fmt.key == "txt":
        return _plain(text, segments)
    if fmt.key == "timestamped":
        return _timestamped(segments)
    if fmt.key == "srt":
        return _srt(segments)
    if fmt.key == "vtt":
        return _vtt(segments)
    raise ValueError(f"Unknown format {fmt.key!r}")


# -- formats ------------------------------------------------------------------


def _plain(text: str, segments: Sequence[Segment]) -> str:
    """The transcript, broken where the speaker changes when that is known."""
    if not any(s.speaker for s in segments):
        return text.strip() + "\n"
    blocks: List[str] = []
    for speaker, lines in _by_speaker(segments):
        blocks.append(f"{_speaker_label(speaker)}: {' '.join(lines)}")
    return "\n\n".join(blocks) + "\n"


def _timestamped(segments: Sequence[Segment]) -> str:
    lines = []
    for s in segments:
        who = f"{_speaker_label(s.speaker)}: " if s.speaker else ""
        lines.append(f"[{clock(s.start)}] {who}{s.text.strip()}")
    return "\n".join(lines) + "\n"


def _srt(segments: Sequence[Segment]) -> str:
    cues = []
    for index, s in enumerate(seg.for_subtitles(segments), start=1):
        cues.append(
            f"{index}\n{srt_time(s.start)} --> {srt_time(s.end)}\n{_cue_text(s)}\n"
        )
    return "\n".join(cues)


def _vtt(segments: Sequence[Segment]) -> str:
    cues = ["WEBVTT\n"]
    for s in seg.for_subtitles(segments):
        text = _cue_text(s, vtt=True)
        cues.append(f"{vtt_time(s.start)} --> {vtt_time(s.end)}\n{text}\n")
    return "\n".join(cues)


# -- helpers ------------------------------------------------------------------


def _cue_text(s: Segment, vtt: bool = False) -> str:
    text = _wrap(s.text.strip())
    if not s.speaker:
        return text
    if vtt:
        return f"<v {_speaker_label(s.speaker)}>{text}"
    return f"{_speaker_label(s.speaker)}: {text}"


def _wrap(text: str, width: int = seg.SUBTITLE_MAX_CHARS // 2) -> str:
    """At most two lines, broken near the middle, the way subtitles are set."""
    if len(text) <= width:
        return text
    middle = len(text) // 2
    left = text.rfind(" ", 0, middle + 1)
    right = text.find(" ", middle)
    candidates = [i for i in (left, right) if i > 0]
    if not candidates:
        return text
    split = min(candidates, key=lambda i: abs(i - middle))
    return text[:split].rstrip() + "\n" + text[split:].lstrip()


def _by_speaker(segments: Sequence[Segment]):
    current, lines = None, []
    for s in segments:
        if lines and s.speaker != current:
            yield current, lines
            lines = []
        current = s.speaker
        lines.append(s.text.strip())
    if lines:
        yield current, lines


def _speaker_label(speaker: str) -> str:
    """"0" and "speaker_0" both read as "Speaker 1"; letters are kept."""
    raw = str(speaker or "").strip()
    tail = raw.rsplit("_", 1)[-1]
    if tail.isdigit():
        return f"Speaker {int(tail) + 1}"
    return f"Speaker {raw}" if len(raw) <= 2 else raw


def _split_ms(seconds: float):
    total_ms = int(round(max(seconds, 0.0) * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return hours, minutes, secs, ms


def srt_time(seconds: float) -> str:
    h, m, s, ms = _split_ms(seconds)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def vtt_time(seconds: float) -> str:
    h, m, s, ms = _split_ms(seconds)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def clock(seconds: float) -> str:
    """h:mm:ss, always with hours, so a column of them lines up."""
    h, m, s, _ = _split_ms(seconds)
    return f"{h}:{m:02d}:{s:02d}"
