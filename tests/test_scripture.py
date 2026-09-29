"""Scripture references: what gets formatted, and what must be left alone."""

import pytest

from aloud.scripture import format_references as fmt


@pytest.mark.parametrize("said,written", [
    ("John three sixteen", "John 3:16"),
    ("turn to Romans eight twenty eight", "turn to Romans 8:28"),
    ("first Corinthians thirteen four through seven", "1 Corinthians 13:4-7"),
    ("1st Corinthians 13 4 to 7", "1 Corinthians 13:4-7"),
    ("second Timothy chapter three verse sixteen", "2 Timothy 3:16"),
    ("Psalm twenty three", "Psalm 23"),
    ("Psalms one hundred and nineteen verse one oh five", "Psalm 119:1 oh five"),
    ("Psalm one hundred nineteen verse one hundred five", "Psalm 119:105"),
    ("Romans 8, 28", "Romans 8:28"),
    ("John 3 16", "John 3:16"),
    ("first John one nine", "1 John 1:9"),
    ("third John one", "third John one"),  # ambiguous book, chapter only
    ("Genesis chapter one", "Genesis 1"),
    ("Song of Solomon two four", "Song of Solomon 2:4"),
    ("Mark chapter four", "Mark 4"),
    ("Philippians four thirteen and fourteen", "Philippians 4:13-14"),
    ("Philippians four six and eight", "Philippians 4:6, 8"),
    ("read Hebrews eleven one. Then pray.", "read Hebrews 11:1. Then pray."),
    ("Ephesians twenty-two", "Ephesians twenty-two"),  # Ephesians has 6 chapters
    ("Psalm twenty-three", "Psalm 23"),
    ("Revelation twenty-one four", "Revelation 21:4"),
    ("Ephesians two eight through nine", "Ephesians 2:8-9"),
])
def test_references_are_written_the_printed_way(said, written):
    assert fmt(said) == written


@pytest.mark.parametrize("text", [
    "John two",                  # a name and a number: not enough to be sure
    "Mark two boxes as done",
    "the numbers four and five",
    "James said three things",
    "we read acts of kindness",
    "Job one is to pray",
    "Romans",
    "",
    "Psalm two hundred",        # past any real chapter
    "Luke skywalker",
    "Romans forty",             # Romans has 16 chapters
    "Genesis fifty one",        # past chapter 50
])
def test_ordinary_speech_is_left_alone(text):
    assert fmt(text) == text


def test_several_references_in_one_sentence():
    assert fmt("compare John three sixteen with Romans five eight") == \
        "compare John 3:16 with Romans 5:8"


def test_a_verse_range_must_go_forward():
    assert fmt("Romans eight twenty eight to one") == "Romans 8:28 to one"


def test_it_runs_in_the_cleanup_pipeline_and_can_be_turned_off():
    from aloud.postprocess import process

    assert process("um, turn to john three sixteen", {"fillers": ["um"]}) == \
        "Turn to John 3:16"
    assert process("john three sixteen", {"scripture": False}) == "John three sixteen"
