"""Turn raw transcription into text worth pasting.

This is the layer that separates "a Whisper wrapper" from a dictation app.
Everything here is a pure string transform, so it is cheap to test and cheap
to extend -- an LLM cleanup pass would slot in as one more step.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

_WHITESPACE_RE = re.compile(r"[ \t]+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.!?;:])")


def _strip_fillers(text: str, fillers: List[str]) -> str:
    """Remove filler words when they stand alone as a word."""
    for filler in fillers:
        if not filler:
            continue
        pattern = re.compile(
            rf"(?<![\w']){re.escape(filler)}(?![\w'])[,]?\s*", re.IGNORECASE
        )
        text = pattern.sub("", text)
    return text


def _apply_dictionary(text: str, dictionary: Dict[str, str]) -> str:
    """Case-insensitive whole-word replacements for names and jargon."""
    for wrong, right in dictionary.items():
        if not wrong:
            continue
        pattern = re.compile(rf"(?<![\w']){re.escape(wrong)}(?![\w'])", re.IGNORECASE)
        text = pattern.sub(right, text)
    return text


def _apply_commands(text: str, commands: Dict[str, str]) -> str:
    """Replace spoken commands ("new line") with literal characters."""
    # Longest phrase first so "new paragraph" wins over "new".
    for phrase in sorted(commands, key=len, reverse=True):
        replacement = commands[phrase]
        pattern = re.compile(
            rf"(?<![\w']){re.escape(phrase)}(?![\w'])[.,]?", re.IGNORECASE
        )
        text = pattern.sub(replacement, text)
    return text


def _tidy(text: str) -> str:
    text = _WHITESPACE_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip(" \t")


def _capitalize_first(text: str) -> str:
    for index, char in enumerate(text):
        if char.isalpha():
            return text[:index] + char.upper() + text[index + 1 :]
    return text


def process(text: str, options: Dict[str, Any] | None = None) -> str:
    """Run the cleanup pipeline. Order matters; each step assumes the last ran."""
    options = options or {}
    if not text:
        return ""

    if options.get("strip_fillers", True):
        text = _strip_fillers(text, options.get("fillers", []) or [])

    dictionary = options.get("dictionary") or {}
    if dictionary:
        text = _apply_dictionary(text, dictionary)

    commands = options.get("commands") or {}
    if commands:
        text = _apply_commands(text, commands)

    if options.get("collapse_whitespace", True):
        text = _tidy(text)

    if options.get("capitalize_first", True):
        text = _capitalize_first(text)

    return text
