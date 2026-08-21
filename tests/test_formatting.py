"""Presentation logic — the decisions views make before they draw anything."""

import time

import pytest

from aloud.ui import tokens as T
from aloud.ui.formatting import (
    db_label,
    describe_when,
    highlight_spans,
    meter_colour,
    preview,
    summarise,
)


# -- correction highlighting -------------------------------------------------


def test_a_replacement_is_located_in_the_delivered_text():
    assert highlight_spans("We shipped Claude Code today", [{"to": "Claude Code"}]) == [(11, 11)]


def test_repeated_replacements_get_separate_spans():
    spans = highlight_spans(
        "Claude Code and Claude Code", [{"to": "Claude Code"}, {"to": "Claude Code"}]
    )
    assert spans == [(0, 11), (16, 11)]


def test_a_replacement_that_post_processing_removed_is_skipped():
    assert highlight_spans("nothing here", [{"to": "Absent"}]) == []


def test_spans_stay_inside_the_text():
    text = "We shipped Claude Code today"
    for start, length in highlight_spans(text, [{"to": "Claude Code"}]):
        assert text[start : start + length] == "Claude Code"


def test_empty_replacements_are_ignored():
    assert highlight_spans("anything", [{"to": ""}, {}]) == []


# -- summaries ---------------------------------------------------------------


def test_a_single_correction_is_shown_in_full():
    assert summarise([{"from": "cloud code", "to": "Claude Code"}]) == "cloud code → Claude Code"


def test_extra_corrections_collapse_into_a_count():
    corrections = [{"from": str(i), "to": str(i)} for i in range(5)]
    assert summarise(corrections).endswith("+3 more")


def test_no_corrections_means_no_summary():
    assert summarise([]) == ""


# -- timestamps --------------------------------------------------------------


def test_today_is_shown_as_a_time():
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    assert describe_when(stamp).startswith("Today at")


def test_another_day_is_shown_with_its_date():
    assert describe_when("2024-03-09T08:05:00") == "09 Mar at 08:05"


def test_a_malformed_stamp_is_passed_through_rather_than_raising():
    assert describe_when("not a date") == "not a date"
    assert describe_when("") == ""


# -- the meter ---------------------------------------------------------------


def test_silence_reads_as_a_dash_not_minus_infinity():
    assert db_label(0.0) == "—"


@pytest.mark.parametrize("level,expected", [(1.0, "0 dB"), (0.5, "-6 dB"), (0.1, "-20 dB")])
def test_levels_convert_to_dbfs(level, expected):
    assert db_label(level) == expected


def test_the_meter_ramp_climbs_through_its_three_stops():
    assert meter_colour(0.10) is T.METER_LOW
    assert meter_colour(0.80) is T.METER_MID
    assert meter_colour(0.99) is T.METER_PEAK


def test_the_ramp_boundaries_match_the_tokens():
    for threshold, token_name in T.METER_STOPS:
        assert meter_colour(threshold) is getattr(T, token_name)


# -- previews ----------------------------------------------------------------


def test_short_text_is_left_alone():
    assert preview("Short enough") == "Short enough"


def test_long_text_is_elided_to_the_limit():
    result = preview("word " * 40, limit=20)
    assert len(result) == 20 and result.endswith("…")


def test_whitespace_is_collapsed():
    assert preview("too   many\n\nspaces") == "too many spaces"
