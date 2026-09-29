"""Exporting a transcript: a *layout* (what the text looks like) in a *format*
(what kind of file it is).

The two are separate choices because they are separate questions. "Text with
timestamps and speaker names" reads the same whether it lands in a .txt, a
Word document or a PDF; and a PDF is still a PDF whether it holds a clean
manuscript or a timestamped log.

Layouts:

==========================  ===================================================
Plain text                  The words, one block. Paste-anywhere.
Manuscript                  Readable paragraphs, broken at pauses and speaker
                            changes, speaker names as headings. For sermons,
                            talks and anything that will be read or edited.
Text with timestamps        One line per segment: ``[0:12:04] …``
Timestamps and speakers     ``[0:12:04] Pastor Tim: …``
Speaker names               ``Pastor Tim: …`` — a script, one turn per speaker.
==========================  ===================================================

Formats: plain text, Markdown, HTML, Word (.docx), PDF, JSON, CSV, and the two
subtitle formats. JSON, CSV and subtitles have one fixed shape each and ignore
the layout; the rest render any layout.

Layouts that need timings or speakers degrade rather than fail: a timestamps
layout for an engine that reported none becomes the manuscript, which is the
honest version of what was asked for.
"""

from __future__ import annotations

import csv
import html
import io
import json
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence

from . import segments as seg
from .engines.base import Segment

#: A pause at least this long starts a new manuscript paragraph.
PARAGRAPH_PAUSE = 2.0
#: A paragraph this long breaks at the next sentence end even without a pause.
PARAGRAPH_CHARS = 700


# ---------------------------------------------------------------------------
# The document being exported
# ---------------------------------------------------------------------------


@dataclass
class Doc:
    """Everything an export needs, independent of where it came from."""

    title: str
    text: str
    segments: List[Segment]
    #: speaker label -> display name; labels missing here get "Speaker N".
    speakers: dict
    subtitle: str = ""

    @property
    def speaker_order(self) -> List[str]:
        order: List[str] = []
        for s in self.segments:
            if s.speaker and s.speaker not in order:
                order.append(s.speaker)
        return order

    def name(self, label: str) -> str:
        from .library import default_speaker_name

        chosen = (self.speakers.get(label) or "").strip()
        return chosen or default_speaker_name(label, self.speaker_order)

    @property
    def timed(self) -> bool:
        return bool(self.segments)

    @property
    def has_speakers(self) -> bool:
        return any(s.speaker for s in self.segments)


def doc_from_record(record) -> Doc:
    """A :class:`Doc` from a library record."""
    bits = [time.strftime("%B %-d, %Y", time.localtime(record.created_at))]
    if record.audio_seconds:
        bits.append(clock(record.audio_seconds))
    if record.source_name:
        bits.append(record.source_name)
    return Doc(record.title, record.text, list(record.segments), dict(record.speakers),
               " · ".join(bits))


def readable_text(record) -> str:
    """The library's transcript.txt: the manuscript layout, for reading in
    Finder, Quick Look or any text editor without Aloud."""
    doc = doc_from_record(record)
    return f"{doc.title}\n{doc.subtitle}\n\n" + render(TXT, MANUSCRIPT, doc).decode("utf-8")


# ---------------------------------------------------------------------------
# Layouts: a transcript becomes a list of blocks
# ---------------------------------------------------------------------------


@dataclass
class Block:
    text: str
    #: Seconds from the start, when the layout shows times.
    time: Optional[float] = None
    #: Display name, when the layout shows speakers inline ("Name: text").
    speaker: str = ""
    #: A speaker heading in the manuscript layout; ``text`` is the name.
    heading: bool = False


@dataclass(frozen=True)
class Layout:
    key: str
    label: str


PLAIN = Layout("plain", "Plain Text")
MANUSCRIPT = Layout("manuscript", "Manuscript")
TIMESTAMPS = Layout("timestamps", "Text with Timestamps")
TIMESTAMPS_SPEAKERS = Layout("timestamps_speakers", "Timestamps and Speakers")
SPEAKERS = Layout("speakers", "Speaker Names")

LAYOUTS = (PLAIN, MANUSCRIPT, TIMESTAMPS, TIMESTAMPS_SPEAKERS, SPEAKERS)
LAYOUT_BY_KEY = {layout.key: layout for layout in LAYOUTS}


def available_layouts(doc: Doc) -> List[Layout]:
    """Layouts worth offering: the timed ones need timings, speakers need speakers."""
    offered = [PLAIN, MANUSCRIPT]
    if doc.timed:
        offered.append(TIMESTAMPS)
    if doc.has_speakers:
        offered += [TIMESTAMPS_SPEAKERS, SPEAKERS]
    return offered


def blocks(layout: Layout, doc: Doc) -> List[Block]:
    if layout is PLAIN or layout.key == "plain":
        return [Block(" ".join(doc.text.split()))] if doc.text.strip() else []
    if not doc.timed:
        return _paragraphs_from_text(doc.text)
    if layout.key == "timestamps":
        return [Block(s.text, time=s.start) for s in doc.segments]
    if layout.key == "timestamps_speakers":
        if not doc.has_speakers:
            return [Block(s.text, time=s.start) for s in doc.segments]
        return [Block(t, time=start, speaker=doc.name(label)) for label, start, t in _turns(doc)]
    if layout.key == "speakers":
        if not doc.has_speakers:
            return _manuscript(doc)
        return [Block(t, speaker=doc.name(label)) for label, _start, t in _turns(doc)]
    return _manuscript(doc)


def _turns(doc: Doc):
    """``(label, start, text)`` per uninterrupted run of one speaker."""
    turns = []
    for s in doc.segments:
        if turns and turns[-1][0] == s.speaker:
            label, start, text = turns[-1]
            turns[-1] = (label, start, f"{text} {s.text}")
        else:
            turns.append((s.speaker, s.start, s.text))
    return turns


def _manuscript(doc: Doc) -> List[Block]:
    out: List[Block] = []
    current: List[str] = []
    speaker = None
    last_end = None

    def flush():
        if current:
            out.append(Block(" ".join(current)))
            current.clear()

    for s in doc.segments:
        if doc.has_speakers and s.speaker != speaker:
            flush()
            out.append(Block(doc.name(s.speaker), heading=True))
            speaker = s.speaker
        elif last_end is not None and s.start - last_end >= PARAGRAPH_PAUSE:
            flush()
        elif current and sum(len(t) + 1 for t in current) > PARAGRAPH_CHARS \
                and current[-1].rstrip().endswith(seg.SENTENCE_END):
            flush()
        current.append(s.text.strip())
        last_end = s.end
    flush()
    return out


def _paragraphs_from_text(text: str, sentences_per: int = 5) -> List[Block]:
    """Paragraphs for an untimed transcript: every few sentences."""
    words = text.split()
    out, current, count = [], [], 0
    for word in words:
        current.append(word)
        if word.endswith(seg.SENTENCE_END):
            count += 1
            if count >= sentences_per:
                out.append(Block(" ".join(current)))
                current, count = [], 0
    if current:
        out.append(Block(" ".join(current)))
    return out


# ---------------------------------------------------------------------------
# Formats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    extension: str
    #: Whether the layout choice applies. JSON, CSV and subtitles have a fixed shape.
    uses_layout: bool = True
    needs_timings: bool = False


TXT = Format("txt", "Plain Text", "txt")
MD = Format("md", "Markdown", "md")
HTML = Format("html", "Web Page (HTML)", "html")
DOCX = Format("docx", "Word Document", "docx")
PDF = Format("pdf", "PDF", "pdf")
JSON = Format("json", "JSON (everything)", "json", uses_layout=False)
CSV = Format("csv", "Spreadsheet (CSV)", "csv", uses_layout=False, needs_timings=True)
SRT = Format("srt", "Subtitles (SRT)", "srt", uses_layout=False, needs_timings=True)
VTT = Format("vtt", "Web Subtitles (WebVTT)", "vtt", uses_layout=False, needs_timings=True)

FORMATS = (TXT, DOCX, PDF, MD, HTML, JSON, CSV, SRT, VTT)
BY_KEY = {f.key: f for f in FORMATS}


def available(doc_or_segments) -> List[Format]:
    """The formats that make sense for this transcript."""
    timed = doc_or_segments.timed if isinstance(doc_or_segments, Doc) else bool(doc_or_segments)
    return [f for f in FORMATS if timed or not f.needs_timings]


def render(fmt: Format, layout: Layout, doc: Doc) -> bytes:
    """The file's contents."""
    if fmt.needs_timings and not doc.timed:
        raise ValueError(f"{fmt.label} needs timings, and this transcript has none")
    if fmt is JSON or fmt.key == "json":
        return _json(doc).encode("utf-8")
    if fmt.key == "csv":
        return _csv(doc).encode("utf-8")
    if fmt.key == "srt":
        return _srt(doc).encode("utf-8")
    if fmt.key == "vtt":
        return _vtt(doc).encode("utf-8")

    parts = blocks(layout, doc)
    if fmt.key == "txt":
        return _txt(parts).encode("utf-8")
    if fmt.key == "md":
        return _markdown(doc, parts).encode("utf-8")
    if fmt.key == "html":
        return _html(doc, parts).encode("utf-8")
    from . import documents

    if fmt.key == "docx":
        return documents.docx(doc.title, doc.subtitle, parts)
    if fmt.key == "pdf":
        return documents.pdf(doc.title, doc.subtitle, parts)
    raise ValueError(f"Unknown format {fmt.key!r}")


def as_text(layout: Layout, doc: Doc) -> str:
    """What the transcript window shows, and what Copy copies."""
    return _txt(blocks(layout, doc))


# -- text-like formats --------------------------------------------------------


def line_of(block: Block) -> str:
    """One block as a line of plain text."""
    prefix = f"[{clock(block.time)}] " if block.time is not None else ""
    who = f"{block.speaker}: " if block.speaker else ""
    return f"{prefix}{who}{block.text}"


def _txt(parts: Sequence[Block]) -> str:
    """Timestamped lines sit one per line; paragraphs get a blank line between
    them; a manuscript heading sits directly above its paragraph."""
    if not parts:
        return ""
    compact = all(b.time is not None for b in parts if not b.heading)
    lines: List[str] = []
    previous: Optional[Block] = None
    for block in parts:
        if previous is not None and not previous.heading and not compact:
            lines.append("")
        elif previous is not None and block.heading:
            lines.append("")
        lines.append(block.text if block.heading else line_of(block))
        previous = block
    return "\n".join(lines).rstrip() + "\n"


def _markdown(doc: Doc, parts: Sequence[Block]) -> str:
    out = [f"# {doc.title}", ""]
    if doc.subtitle:
        out += [f"*{doc.subtitle}*", ""]
    for block in parts:
        if block.heading:
            out += [f"### {block.text}", ""]
            continue
        prefix = f"`{clock(block.time)}` " if block.time is not None else ""
        who = f"**{block.speaker}:** " if block.speaker else ""
        out += [f"{prefix}{who}{block.text}", ""]
    return "\n".join(out).rstrip() + "\n"


def _html(doc: Doc, parts: Sequence[Block]) -> str:
    esc = html.escape
    body = [f"<h1>{esc(doc.title)}</h1>"]
    if doc.subtitle:
        body.append(f'<p class="meta">{esc(doc.subtitle)}</p>')
    for block in parts:
        if block.heading:
            body.append(f"<h3>{esc(block.text)}</h3>")
            continue
        time_html = f'<span class="time">{clock(block.time)}</span> ' if block.time is not None else ""
        who = f"<strong>{esc(block.speaker)}:</strong> " if block.speaker else ""
        body.append(f"<p>{time_html}{who}{esc(block.text)}</p>")
    style = (
        "body{font:16px/1.6 -apple-system,Helvetica,Arial,sans-serif;max-width:44em;"
        "margin:3em auto;padding:0 1em;color:#1d1d1f}"
        ".meta{color:#6e6e73}.time{color:#6e6e73;font-variant-numeric:tabular-nums}"
        "h3{margin:1.6em 0 .3em}"
        "@media(prefers-color-scheme:dark){body{background:#1d1d1f;color:#f5f5f7}}"
    )
    return (
        "<!doctype html>\n<html><head><meta charset=\"utf-8\">"
        f"<title>{esc(doc.title)}</title><style>{style}</style></head>\n<body>\n"
        + "\n".join(body) + "\n</body></html>\n"
    )


# -- fixed-shape formats ------------------------------------------------------


def _json(doc: Doc) -> str:
    data = {
        "title": doc.title,
        "text": doc.text,
        "speakers": {label: doc.name(label) for label in doc.speaker_order},
        "segments": [
            {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text,
             **({"speaker": doc.name(s.speaker)} if s.speaker else {})}
            for s in doc.segments
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _csv(doc: Doc) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["start", "end", "speaker", "text"])
    for s in doc.segments:
        writer.writerow([clock_ms(s.start), clock_ms(s.end),
                         doc.name(s.speaker) if s.speaker else "", s.text])
    return buffer.getvalue()


def _srt(doc: Doc) -> str:
    cues = []
    for index, s in enumerate(seg.for_subtitles(doc.segments), start=1):
        cues.append(f"{index}\n{srt_time(s.start)} --> {srt_time(s.end)}\n{_cue_text(doc, s)}\n")
    return "\n".join(cues)


def _vtt(doc: Doc) -> str:
    cues = ["WEBVTT\n"]
    for s in seg.for_subtitles(doc.segments):
        cues.append(f"{vtt_time(s.start)} --> {vtt_time(s.end)}\n{_cue_text(doc, s, vtt=True)}\n")
    return "\n".join(cues)


def _cue_text(doc: Doc, s: Segment, vtt: bool = False) -> str:
    text = _wrap(s.text.strip())
    if not s.speaker:
        return text
    name = doc.name(s.speaker)
    return f"<v {name}>{text}" if vtt else f"{name}: {text}"


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


# -- times --------------------------------------------------------------------


def _split_ms(seconds: float):
    total_ms = int(round(max(seconds or 0.0, 0.0) * 1000))
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


def clock_ms(seconds: float) -> str:
    return vtt_time(seconds)


def clock(seconds: float) -> str:
    """h:mm:ss, always with hours, so a column of them lines up."""
    h, m, s, _ = _split_ms(seconds)
    return f"{h}:{m:02d}:{s:02d}"
