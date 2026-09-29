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


# -- formats -----------------------------------------------------------------

TIMED = [
    Segment(0.0, 2.5, "Welcome to the service."),
    Segment(3661.2, 3664.0, "An hour in."),
]


def test_only_plain_text_is_offered_without_timings():
    assert [f.key for f in export.available([])] == ["txt"]
    assert [f.key for f in export.available(TIMED)] == ["txt", "timestamped", "srt", "vtt"]


def test_srt():
    out = export.render(export.SRT, "", TIMED)
    assert out.startswith("1\n00:00:00,000 --> 00:00:02,500\nWelcome to the service.\n")
    assert "2\n01:01:01,200 --> 01:01:04,000\nAn hour in.\n" in out


def test_vtt():
    out = export.render(export.VTT, "", TIMED)
    assert out.startswith("WEBVTT\n")
    assert "01:01:01.200 --> 01:01:04.000\nAn hour in." in out


def test_timestamped_text():
    out = export.render(export.TIMESTAMPED, "", TIMED)
    assert out.splitlines() == ["[0:00:00] Welcome to the service.", "[1:01:01] An hour in."]


def test_plain_text_is_the_transcript():
    assert export.render(export.TEXT, "Hello.", []) == "Hello.\n"


def test_speakers_are_named_in_every_format():
    spoken = [Segment(0, 1, "Hi.", "0"), Segment(1, 2, "Hello.", "1"), Segment(2, 3, "Bye.", "1")]
    assert export.render(export.TEXT, "ignored", spoken) == "Speaker 1: Hi.\n\nSpeaker 2: Hello. Bye.\n"
    assert "Speaker 2: Hello." in export.render(export.SRT, "", spoken)
    assert "<v Speaker 1>Hi." in export.render(export.VTT, "", spoken)
    assert "[0:00:01] Speaker 2: Hello." in export.render(export.TIMESTAMPED, "", spoken)


@pytest.mark.parametrize("raw,label", [("A", "Speaker A"), ("speaker_0", "Speaker 1"),
                                       ("2", "Speaker 3"), ("Pastor Tim", "Pastor Tim")])
def test_speaker_labels_read_naturally(raw, label):
    assert export._speaker_label(raw) == label


def test_timed_formats_refuse_an_untimed_transcript():
    with pytest.raises(ValueError):
        export.render(export.SRT, "text", [])


def test_long_cues_wrap_onto_two_lines():
    s = Segment(0, 5, "This is a subtitle line that is long enough to need wrapping")
    body = export.render(export.SRT, "", [s]).splitlines()
    assert len(body[2]) <= 42 and len(body[3]) <= 42
