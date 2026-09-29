"""Timed segments, and the files built from them."""

import pytest

from aloud import export
from aloud import segments as seg
from aloud.engines.base import Segment


def words(*items):
    return [seg.Word(*item) for item in items]


# -- grouping words ----------------------------------------------------------


def test_words_group_into_sentences():
    grouped = seg.from_words(words(
        (0.0, 0.3, "Hello"), (0.3, 0.6, "there."), (0.7, 1.0, "How"), (1.0, 1.3, "are"),
        (1.3, 1.6, "you?"),
    ))
    assert [s.text for s in grouped] == ["Hello there.", "How are you?"]
    assert (grouped[0].start, grouped[0].end) == (0.0, 0.6)
    assert (grouped[1].start, grouped[1].end) == (0.7, 1.6)


def test_a_long_pause_starts_a_new_segment_mid_sentence():
    grouped = seg.from_words(words((0.0, 0.5, "and"), (3.0, 3.4, "then")))
    assert [s.text for s in grouped] == ["and", "then"]


def test_a_speaker_change_always_breaks():
    grouped = seg.from_words(words((0.0, 0.4, "yes", "A"), (0.4, 0.8, "no", "B")))
    assert [(s.text, s.speaker) for s in grouped] == [("yes", "A"), ("no", "B")]


def test_groups_stay_under_the_length_limit():
    many = words(*[(i * 0.2, i * 0.2 + 0.2, "word") for i in range(100)])
    grouped = seg.from_words(many, max_chars=40)
    assert all(len(s.text) <= 40 for s in grouped)
    assert seg.text_of(grouped) == " ".join(["word"] * 100)


def test_blank_words_are_ignored():
    assert seg.from_words(words((0, 1, "  "), (1, 2, ""))) == []


def test_offset_moves_every_segment():
    moved = seg.offset([Segment(1.0, 2.0, "a", "A")], 600.0)
    assert moved == [Segment(601.0, 602.0, "a", "A")]


# -- subtitle sizing ---------------------------------------------------------


def test_short_segments_are_left_alone():
    s = Segment(0.0, 2.0, "Short line.")
    assert seg.for_subtitles([s]) == [s]


def test_long_segments_are_split_and_keep_their_span():
    text = " ".join(["word"] * 60)  # 299 characters
    pieces = seg.for_subtitles([Segment(10.0, 40.0, text, "A")])
    assert len(pieces) >= 4
    assert all(len(p.text) <= seg.SUBTITLE_MAX_CHARS + 5 for p in pieces)
    assert pieces[0].start == 10.0 and pieces[-1].end == 40.0
    assert all(a.end == pytest.approx(b.start) for a, b in zip(pieces, pieces[1:]))
    assert all(p.speaker == "A" for p in pieces)
    assert " ".join(p.text for p in pieces) == text


def test_a_short_but_slow_segment_is_split_by_time():
    pieces = seg.for_subtitles([Segment(0.0, 20.0, "one two three four five six")])
    assert len(pieces) >= 3
    assert all(p.end - p.start <= seg.SUBTITLE_MAX_SECONDS + 0.01 for p in pieces)


# -- formats and layouts ------------------------------------------------------

TIMED = [
    Segment(0.0, 2.5, "Welcome to the service."),
    Segment(3661.2, 3664.0, "An hour in."),
]


def doc(segments=TIMED, text="Welcome to the service. An hour in.", speakers=None):
    return export.Doc("Title", text, list(segments), dict(speakers or {}), "details")


def text_of(fmt, layout, d):
    return export.render(fmt, layout, d).decode("utf-8")


def test_timed_formats_need_timings():
    assert [f.key for f in export.available(doc([]))] == ["txt", "docx", "pdf", "md", "html", "json"]
    assert {"csv", "srt", "vtt"} <= {f.key for f in export.available(doc())}


def test_layouts_offered_follow_what_the_engine_reported():
    assert [l.key for l in export.available_layouts(doc([]))] == ["plain", "manuscript"]
    assert [l.key for l in export.available_layouts(doc())] == ["plain", "manuscript", "timestamps"]
    spoken = doc([Segment(0, 1, "Hi.", "A")])
    assert "speakers" in [l.key for l in export.available_layouts(spoken)]


def test_srt():
    out = text_of(export.SRT, export.PLAIN, doc())
    assert out.startswith("1\n00:00:00,000 --> 00:00:02,500\nWelcome to the service.\n")
    assert "2\n01:01:01,200 --> 01:01:04,000\nAn hour in.\n" in out


def test_vtt():
    out = text_of(export.VTT, export.PLAIN, doc())
    assert out.startswith("WEBVTT\n")
    assert "01:01:01.200 --> 01:01:04.000\nAn hour in." in out


def test_timestamped_text():
    out = text_of(export.TXT, export.TIMESTAMPS, doc())
    assert out.splitlines() == ["[0:00:00] Welcome to the service.", "[1:01:01] An hour in."]


def test_plain_text_is_the_transcript():
    assert text_of(export.TXT, export.PLAIN, doc([], "Hello.")) == "Hello.\n"


SPOKEN = [Segment(0, 1, "Hi.", "0"), Segment(1, 2, "Hello.", "1"), Segment(2, 3, "Bye.", "1")]


def test_speaker_layouts_merge_turns_and_use_given_names():
    d = doc(SPOKEN, speakers={"1": "Pastor Tim"})
    assert text_of(export.TXT, export.SPEAKERS, d) == "Speaker 1: Hi.\n\nPastor Tim: Hello. Bye.\n"
    assert text_of(export.TXT, export.TIMESTAMPS_SPEAKERS, d).splitlines() == [
        "[0:00:00] Speaker 1: Hi.", "[0:00:01] Pastor Tim: Hello. Bye."]
    assert "Pastor Tim: Hello." in text_of(export.SRT, export.PLAIN, d)
    assert "<v Speaker 1>Hi." in text_of(export.VTT, export.PLAIN, d)


def test_manuscript_uses_speaker_headings_and_paragraph_breaks():
    d = doc(SPOKEN, speakers={"0": "Host"})
    assert text_of(export.TXT, export.MANUSCRIPT, d) == "Host\nHi.\n\nSpeaker 2\nHello. Bye.\n"
    paused = doc([Segment(0, 1, "One."), Segment(1.2, 2, "Two."), Segment(9, 10, "Three.")])
    assert text_of(export.TXT, export.MANUSCRIPT, paused) == "One. Two.\n\nThree.\n"


def test_an_untimed_transcript_still_gets_paragraphs():
    text = " ".join(f"Sentence {i}." for i in range(12))
    out = text_of(export.TXT, export.TIMESTAMPS, doc([], text))
    assert out.count("\n\n") == 2  # five sentences a paragraph, timings or not


def test_speakers_are_numbered_by_first_appearance():
    """AssemblyAI's "B" speaking first is Speaker 1, not Speaker B."""
    d = doc([Segment(0, 1, "First.", "B"), Segment(1, 2, "Second.", "A")])
    assert text_of(export.TXT, export.SPEAKERS, d).startswith("Speaker 1: First.")


@pytest.mark.parametrize("raw,label", [("A", "Speaker A"), ("speaker_0", "Speaker 1"),
                                       ("2", "Speaker 3"), ("Pastor Tim", "Pastor Tim")])
def test_unordered_speaker_labels_read_naturally(raw, label):
    from aloud.library import default_speaker_name

    assert default_speaker_name(raw) == label


def test_timed_formats_refuse_an_untimed_transcript():
    with pytest.raises(ValueError):
        export.render(export.SRT, export.PLAIN, doc([]))


def test_long_cues_wrap_onto_two_lines():
    s = Segment(0, 5, "This is a subtitle line that is long enough to need wrapping")
    body = text_of(export.SRT, export.PLAIN, doc([s])).splitlines()
    assert len(body[2]) <= 42 and len(body[3]) <= 42


def test_json_has_everything_with_names_resolved():
    import json

    data = json.loads(text_of(export.JSON, export.PLAIN, doc(SPOKEN, speakers={"0": "Host"})))
    assert data["speakers"] == {"0": "Host", "1": "Speaker 2"}
    assert data["segments"][0] == {"start": 0, "end": 1, "text": "Hi.", "speaker": "Host"}


def test_csv_has_a_row_per_segment():
    rows = text_of(export.CSV, export.PLAIN, doc(SPOKEN)).splitlines()
    assert rows[0] == "start,end,speaker,text"
    assert rows[1] == "00:00:00.000,00:00:01.000,Speaker 1,Hi."


def test_markdown_and_html_carry_the_title_and_escape_text():
    d = export.Doc("Q&A <night>", "a < b", [], {}, "")
    assert text_of(export.MD, export.PLAIN, d).startswith("# Q&A <night>\n")
    page = text_of(export.HTML, export.PLAIN, d)
    assert "<h1>Q&amp;A &lt;night&gt;</h1>" in page and "a &lt; b" in page


# -- Word and PDF ---------------------------------------------------------------

LONG = [Segment(i * 5.0, i * 5.0 + 4, f"Sentence number {i} of the sermon, with “quotes” — señor.",
                "A" if i % 7 else "B") for i in range(300)]


@pytest.mark.parametrize("layout", export.LAYOUTS, ids=lambda l: l.key)
def test_word_documents_are_valid(layout):
    import io
    import zipfile
    from xml.dom import minidom

    data = export.render(export.DOCX, layout, doc(LONG, speakers={"A": "Pastor Tim & Co"}))
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        names = package.namelist()
        assert names[0] == "[Content_Types].xml"
        assert {"word/document.xml", "word/styles.xml", "_rels/.rels"} <= set(names)
        for name in names:
            if name.endswith((".xml", ".rels")):
                minidom.parseString(package.read(name))  # well-formed XML
        body = package.read("word/document.xml").decode()
    assert "Pastor Tim &amp; Co" in body or layout.key in ("plain", "timestamps")


def test_word_drops_characters_xml_forbids():
    import io
    import zipfile
    from xml.dom import minidom

    d = export.Doc("T", "bad \x0b char", [], {}, "")
    data = export.render(export.DOCX, export.PLAIN, d)
    minidom.parseString(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))


@pytest.mark.parametrize("layout", export.LAYOUTS, ids=lambda l: l.key)
def test_pdfs_are_well_formed_and_paginated(layout):
    import re

    data = export.render(export.PDF, layout, doc(LONG, text=seg.text_of(LONG)))
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    # The cross-reference offsets must point at the objects they name.
    xref = int(re.search(rb"startxref\n(\d+)", data).group(1))
    table = data[xref:].split(b"\n")
    count = int(table[1].split()[1])
    for number in range(1, count):
        offset = int(table[2 + number].split()[0])
        assert data[offset:].startswith(f"{number} 0 obj".encode())
    pages = int(re.search(rb"/Count (\d+)", data).group(1))
    assert pages > 1


def test_pdf_reports_text_its_fonts_cannot_show():
    from aloud import documents

    assert documents.pdf_can_render("Señor, “amen” — café…")
    assert not documents.pdf_can_render("감사합니다")
    export.render(export.PDF, export.PLAIN, doc([], "감사합니다"))  # still writes, as "?"


def test_pdfs_open_in_a_real_reader():
    pypdf = pytest.importorskip("pypdf")
    import io

    reader = pypdf.PdfReader(io.BytesIO(export.render(export.PDF, export.MANUSCRIPT,
                                                      doc(LONG, speakers={"A": "Pastor Tim"}))))
    first = reader.pages[0].extract_text()
    assert "Title" in first and "Pastor Tim" in first and "señor" in first
