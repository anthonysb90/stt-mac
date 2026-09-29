"""Turning edits into Dictionary corrections."""

from aloud import learn
from aloud.engines.base import Segment


def test_a_short_swap_is_suggested():
    found = learn.suggestions("Turn to Romans ate twenty eight.", "Turn to Romans 8:28.")
    assert found == [learn.Suggestion("ate twenty eight", "8:28", 1)]


def test_repeated_fixes_are_counted_once_and_ranked():
    before = "I use cloud code daily. cloud code is great. The Supa base team."
    after = "I use Claude Code daily. Claude Code is great. The Supabase team."
    found = learn.suggestions(before, after)
    assert found[0] == learn.Suggestion("cloud code", "Claude Code", 2)
    assert learn.Suggestion("Supa base", "Supabase", 1) in found


def test_rewrites_and_deletions_are_not_corrections():
    before = "We will meet on Tuesday at the church office to plan the service."
    after = "Let's plan the service next week."
    assert learn.suggestions(before, after) == []
    assert learn.suggestions("One two three four.", "One four.") == []  # a deletion
    assert learn.suggestions("Amen.", "Amen. Praise God.") == []        # an addition


def test_punctuation_and_case_alone_are_not_corrections():
    assert learn.suggestions("hello there", "Hello, there.") == []


def test_swaps_the_dictionary_already_makes_are_left_out():
    class Entry:
        heard, write, term = "cloud code", "Claude Code", ""

    class Dictionary:
        entries = [Entry()]

    found = learn.suggestions("cloud code and supa base", "Claude Code and Supabase")
    assert [s.write for s in learn.new_suggestions(found, Dictionary())] == ["Supabase"]


def test_accepted_corrections_carry_into_segments_with_their_timings():
    segments = [Segment(0, 2, "Open cloud code now.", "A"), Segment(2, 3, "Cloudflare", "A")]
    fixed = learn.apply_to_segments(segments, [learn.Suggestion("cloud code", "Claude Code", 1)])
    assert fixed[0] == Segment(0, 2, "Open Claude Code now.", "A")
    assert fixed[1].text == "Cloudflare", "whole words only"


def test_edits_flow_back_into_the_timed_segments():
    segments = [Segment(0, 2, "Good morning church.", "A"),
                Segment(2, 4, "Turn to Romans ate.", "A"),
                Segment(5, 6, "Um okay.", "B")]
    edited = "Good morning, church family. Turn to Romans 8. Okay."
    out = learn.reflow(segments, edited)
    assert [s.text for s in out] == ["Good morning, church family.", "Turn to Romans 8.", "Okay."]
    assert [(s.start, s.end, s.speaker) for s in out] == [(0, 2, "A"), (2, 4, "A"), (5, 6, "B")]


def test_a_segment_whose_words_were_all_deleted_is_dropped():
    segments = [Segment(0, 1, "Keep this.", ""), Segment(1, 2, "Delete me.", "")]
    assert [s.text for s in learn.reflow(segments, "Keep this.")] == ["Keep this."]


def test_nothing_is_lost_when_every_word_changes():
    segments = [Segment(0, 1, "one two", ""), Segment(1, 2, "three four", "")]
    out = learn.reflow(segments, "uno dos tres cuatro")
    assert " ".join(s.text for s in out) == "uno dos tres cuatro"


from test_core_pipeline import app  # noqa: E402,F401  (fixture)


def test_taught_fixes_correct_the_next_dictation(app):
    teacher = learn.Teacher(app)
    found = learn.suggestions("Turn to Romans ate.", "Turn to Romans 8.")
    assert teacher.fresh(found) == found
    assert teacher.teach(found) == []
    assert app.corrections_for("open Romans ate please").text == "open Romans 8 please"
    assert teacher.fresh(found) == [], "already in the Dictionary"
