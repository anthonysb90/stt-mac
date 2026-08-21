"""Presentation logic with no AppKit in it.

Everything a view needs to *decide* — how to phrase a timestamp, where the
corrections fall in a transcript, which meter colour a level earns — lives
here rather than in the view that draws it, so it can be tested directly.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Sequence, Tuple

from . import tokens as T


def describe_when(stamp: str) -> str:
    """A friendly timestamp: time today, date and time before that."""
    try:
        when = time.strptime(stamp, "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return stamp or ""
    today = time.localtime()
    if (when.tm_year, when.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("Today at %H:%M", when)
    return time.strftime("%d %b at %H:%M", when)


def summarise(corrections: Sequence[Dict[str, Any]], limit: int = 2) -> str:
    """"cloud code → Claude Code · +2 more" — what the dictionary actually did."""
    if not corrections:
        return ""
    shown = [f"{c.get('from', '')} → {c.get('to', '')}" for c in corrections[:limit]]
    remaining = len(corrections) - len(shown)
    if remaining > 0:
        shown.append(f"+{remaining} more")
    return " · ".join(shown)


def highlight_spans(
    text: str, corrections: Sequence[Dict[str, Any]]
) -> List[Tuple[int, int]]:
    """Locate each correction's replacement in the delivered text.

    Offsets recorded during the correction pass refer to the *raw* transcript,
    and the post-processing that runs afterwards moves them. Searching for the
    replacement instead is exact except when the same phrase appears twice with
    only some occurrences corrected — a case where highlighting one span too
    many is a far smaller wrong than highlighting the wrong words.
    """
    spans: List[Tuple[int, int]] = []
    cursor_by_value: Dict[str, int] = {}
    for correction in corrections:
        replacement = str(correction.get("to", ""))
        if not replacement:
            continue
        start = text.find(replacement, cursor_by_value.get(replacement, 0))
        if start < 0:
            continue
        spans.append((start, len(replacement)))
        cursor_by_value[replacement] = start + len(replacement)
    return spans


def db_label(level: float) -> str:
    """A rough dBFS readout for the numeric display beside the meter."""
    if level <= T.METER_SILENCE_FLOOR / 2:
        return "—"
    return f"{20 * math.log10(max(level, 1e-6)):.0f} dB"


def meter_colour(level: float) -> T.Color:
    """Which stop of the meter ramp a level falls into."""
    chosen = T.METER_LOW
    for threshold, token_name in T.METER_STOPS:
        if level >= threshold:
            chosen = getattr(T, token_name)
    return chosen


def preview(text: str, limit: int = 48) -> str:
    """Shorten a transcript for a one-line summary."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"
