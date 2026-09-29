"""Scripture references, written the way they are printed.

Speech engines write what they hear: "John three sixteen", "first Corinthians
thirteen four through seven", "Romans 8 28". This turns those into "John 3:16",
"1 Corinthians 13:4-7" and "Romans 8:28", in dictation, Quick Dictate and
transcribed files alike.

Being wrong here is worse than doing nothing, so the rules are cautious:

* Books that are also names or ordinary words — John, Mark, Luke, James, Job,
  Ruth, Acts, Numbers and the like — are only formatted when a chapter *and* a
  verse follow, or the word "chapter" does. "John two" stays as it was said;
  "John two eleven" becomes John 2:11.
* Other books format with a chapter alone: "Romans eight" becomes Romans 8.
* Numbers stop at 176, the longest chapter (Psalm 119), so a book name followed
  by an unrelated number is left alone.

Pure text in, text out; no AppKit, and tested directly.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------

#: Canonical name -> spoken forms (lower case). Numbered books are listed
#: without their number; the ordinal is handled separately.
_BOOKS: Dict[str, Tuple[str, ...]] = {
    "Genesis": ("genesis",), "Exodus": ("exodus",), "Leviticus": ("leviticus",),
    "Numbers": ("numbers",), "Deuteronomy": ("deuteronomy",), "Joshua": ("joshua",),
    "Judges": ("judges",), "Ruth": ("ruth",), "Ezra": ("ezra",), "Nehemiah": ("nehemiah",),
    "Esther": ("esther",), "Job": ("job",), "Psalm": ("psalm", "psalms"),
    "Proverbs": ("proverbs",), "Ecclesiastes": ("ecclesiastes",),
    "Song of Solomon": ("song of solomon", "song of songs", "songs of solomon"),
    "Isaiah": ("isaiah",), "Jeremiah": ("jeremiah",), "Lamentations": ("lamentations",),
    "Ezekiel": ("ezekiel",), "Daniel": ("daniel",), "Hosea": ("hosea",), "Joel": ("joel",),
    "Amos": ("amos",), "Obadiah": ("obadiah",), "Jonah": ("jonah",), "Micah": ("micah",),
    "Nahum": ("nahum",), "Habakkuk": ("habakkuk",), "Zephaniah": ("zephaniah",),
    "Haggai": ("haggai",), "Zechariah": ("zechariah",), "Malachi": ("malachi",),
    "Matthew": ("matthew",), "Mark": ("mark",), "Luke": ("luke",), "John": ("john",),
    "Acts": ("acts", "acts of the apostles"), "Romans": ("romans",),
    "Galatians": ("galatians",), "Ephesians": ("ephesians",),
    "Philippians": ("philippians",), "Colossians": ("colossians",),
    "Philemon": ("philemon",), "Hebrews": ("hebrews",), "James": ("james",),
    "Jude": ("jude",), "Revelation": ("revelation", "revelations"),
}

#: Books that take a number: 1 Samuel, 2 Kings, 3 John…
_NUMBERED: Dict[str, Tuple[str, ...]] = {
    "Samuel": ("samuel",), "Kings": ("kings",), "Chronicles": ("chronicles",),
    "Corinthians": ("corinthians",), "Thessalonians": ("thessalonians",),
    "Timothy": ("timothy",), "Peter": ("peter",), "John": ("john",),
}
_MAX_BOOK_NUMBER = {"John": 3}  # 1–3 John; the rest go to 2

_ORDINALS = {
    "first": 1, "1st": 1, "one": 1, "1": 1, "i": 1,
    "second": 2, "2nd": 2, "two": 2, "2": 2, "ii": 2,
    "third": 3, "3rd": 3, "three": 3, "3": 3, "iii": 3,
}

#: Also a name or an everyday word: needs chapter *and* verse, or "chapter".
_AMBIGUOUS = {
    "Numbers", "Judges", "Ruth", "Job", "Daniel", "Joel", "Amos", "Jonah", "Micah",
    "Matthew", "Mark", "Luke", "John", "Acts", "James", "Jude", "Esther", "Ezra",
    "Joshua", "Titus", "Peter", "Timothy", "Samuel", "Kings",
}
_BOOKS["Titus"] = ("titus",)

#: How many chapters each book has. A "chapter" past the end is not a
#: reference, which catches most accidental matches before they happen.
CHAPTERS: Dict[str, int] = {
    "Genesis": 50, "Exodus": 40, "Leviticus": 27, "Numbers": 36, "Deuteronomy": 34,
    "Joshua": 24, "Judges": 21, "Ruth": 4, "1 Samuel": 31, "2 Samuel": 24,
    "1 Kings": 22, "2 Kings": 25, "1 Chronicles": 29, "2 Chronicles": 36, "Ezra": 10,
    "Nehemiah": 13, "Esther": 10, "Job": 42, "Psalm": 150, "Proverbs": 31,
    "Ecclesiastes": 12, "Song of Solomon": 8, "Isaiah": 66, "Jeremiah": 52,
    "Lamentations": 5, "Ezekiel": 48, "Daniel": 12, "Hosea": 14, "Joel": 3, "Amos": 9,
    "Obadiah": 1, "Jonah": 4, "Micah": 7, "Nahum": 3, "Habakkuk": 3, "Zephaniah": 3,
    "Haggai": 2, "Zechariah": 14, "Malachi": 4, "Matthew": 28, "Mark": 16, "Luke": 24,
    "John": 21, "Acts": 28, "Romans": 16, "1 Corinthians": 16, "2 Corinthians": 13,
    "Galatians": 6, "Ephesians": 6, "Philippians": 4, "Colossians": 4,
    "1 Thessalonians": 5, "2 Thessalonians": 3, "1 Timothy": 6, "2 Timothy": 4,
    "Titus": 3, "Philemon": 1, "Hebrews": 13, "James": 5, "1 Peter": 5, "2 Peter": 3,
    "1 John": 5, "2 John": 1, "3 John": 1, "Jude": 1, "Revelation": 22,
}

# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
          "seven": 7, "eight": 8, "nine": 9}
_TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
          "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
MAX_NUMBER = 176  # Psalm 119 has 176 verses; no chapter or verse is longer

_TOKEN = re.compile(r"\d+(?:st|nd|rd|th)?|[A-Za-z]+(?:[-'][A-Za-z]+)*|[:,\-–]")


class _Tokens:
    def __init__(self, text: str) -> None:
        self.items = [(m.group(0), m.start(), m.end()) for m in _TOKEN.finditer(text)]

    def word(self, i: int) -> str:
        return self.items[i][0].lower() if 0 <= i < len(self.items) else ""

    def __len__(self) -> int:
        return len(self.items)


def _split_hyphen(word: str) -> List[str]:
    return word.split("-")


def _number(tokens: _Tokens, i: int) -> Optional[Tuple[int, int]]:
    """A number starting at token ``i``: ``(value, next index)``, or None.

    Reads digits, and words up to "one hundred and seventy six". Two adjacent
    small numbers are *not* joined — "three sixteen" is 3 then 16 — because
    that is how chapter and verse are said.
    """
    word = tokens.word(i)
    if word.isdigit():
        value = int(word)
        return (value, i + 1) if 0 < value <= MAX_NUMBER else None

    value, j = 0, i
    if word in ("a", "one") and tokens.word(i + 1) == "hundred":
        value, j = 100, i + 2
        if tokens.word(j) == "and":
            j += 1
    elif word == "hundred":
        return None
    rest = _small(tokens, j)
    if rest is None:
        return (value, j) if value else None
    if tokens.word(rest[1]) in ("hundred", "thousand"):
        return None  # "two hundred": a bigger number than any chapter or verse
    total = value + rest[0]
    return (total, rest[1]) if 0 < total <= MAX_NUMBER else None


def _small(tokens: _Tokens, i: int) -> Optional[Tuple[int, int]]:
    """1–99 in words, including hyphenated "twenty-two"."""
    word = tokens.word(i)
    if "-" in word:
        tens, _, unit = word.partition("-")
        if tens in _TENS and unit in _UNITS and unit != "zero":
            return _TENS[tens] + _UNITS[unit], i + 1
        return None
    if word in _TEENS:
        return _TEENS[word], i + 1
    if word in _TENS:
        value = _TENS[word]
        if tokens.word(i + 1) in _UNITS and tokens.word(i + 1) != "zero":
            return value + _UNITS[tokens.word(i + 1)], i + 2
        return value, i + 1
    if word in _UNITS and word != "zero":
        return _UNITS[word], i + 1
    return None


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _book_at(tokens: _Tokens, i: int) -> Optional[Tuple[str, int]]:
    """The book starting at token ``i``: ``(canonical name, next index)``."""
    ordinal = _ORDINALS.get(tokens.word(i))
    if ordinal is not None:
        for name, forms in _NUMBERED.items():
            for form in forms:
                end = _match_words(tokens, i + 1, form)
                if end is not None and ordinal <= _MAX_BOOK_NUMBER.get(name, 2):
                    return f"{ordinal} {name}", end
    for name, forms in _BOOKS.items():
        for form in sorted(forms, key=len, reverse=True):
            end = _match_words(tokens, i, form)
            if end is not None:
                return name, end
    return None


def _match_words(tokens: _Tokens, i: int, phrase: str) -> Optional[int]:
    for offset, part in enumerate(phrase.split()):
        if tokens.word(i + offset) != part:
            return None
    return i + len(phrase.split())


def _reference(tokens: _Tokens, i: int, book: str) -> Optional[Tuple[str, int]]:
    """Chapter, verse and range after a book: ``(text, next index)``."""
    j = i
    said_chapter = tokens.word(j) == "chapter"
    if said_chapter:
        j += 1
    chapter = _number(tokens, j)
    if chapter is None or chapter[0] > CHAPTERS.get(book, MAX_NUMBER):
        # Past the book's last chapter: "Ephesians twenty" is not a reference.
        return None
    ref, j = str(chapter[0]), chapter[1]

    k = j
    if tokens.word(k) in (":", ","):
        k += 1
    if tokens.word(k) in ("verse", "verses"):
        k += 1
    verse = _number(tokens, k)
    if verse is None:
        base = _base_book(book)
        if base in _AMBIGUOUS and not said_chapter:
            return None
        return ref, j
    ref += f":{verse[0]}"
    j = verse[1]

    # A range or a second verse: "through seven", "to 7", "- 7", "and seventeen".
    joiner = tokens.word(j)
    if joiner in ("through", "thru", "to", "-", "–", "and"):
        end = _number(tokens, j + 1)
        if end is not None and end[0] > verse[0]:
            if joiner == "and" and end[0] != verse[0] + 1:
                ref += f", {end[0]}"
            else:
                ref += f"-{end[0]}"
            j = end[1]
    return ref, j


def _base_book(book: str) -> str:
    return book.split(" ", 1)[1] if book[:1].isdigit() else book


def format_references(text: str) -> str:
    """Rewrite every spoken scripture reference in ``text``."""
    if not text:
        return text
    tokens = _Tokens(text)
    out: List[str] = []
    cursor = 0
    i = 0
    while i < len(tokens):
        found = _book_at(tokens, i)
        if found is not None:
            book, after = found
            ref = _reference(tokens, after, book)
            if ref is not None:
                reference, end = ref
                start_char = tokens.items[i][1]
                end_char = tokens.items[end - 1][2]
                out.append(text[cursor:start_char])
                out.append(f"{book} {reference}")
                cursor = end_char
                i = end
                continue
        i += 1
    out.append(text[cursor:])
    return "".join(out)
