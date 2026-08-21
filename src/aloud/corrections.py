"""The correction pass — the guaranteed half of the Dictionary.

Biasing the engine is a nudge; this is the promise. Every enabled entry becomes
a pattern, and one single pass over the transcript rewrites what matches.

Matching rules
--------------
**Whole word.** A pattern only fires between word boundaries, so a correction
for ``cloud`` can never touch ``clouds`` or ``Cloudflare``.

**Case-insensitive**, but the replacement is written exactly as you typed it.
You chose "Claude Code"; you get "Claude Code" regardless of what was heard.

**Longest match first.** Patterns are ordered by length, so ``Claude Code SDK``
wins over ``Claude Code`` at the same position.

**Separator-tolerant, both ways.** These models mangle names in two opposite
directions, and one entry has to survive both:

*Glued or hyphenated.* A multi-word pattern matches with any run of spaces,
hyphens or underscores between its words — including none at all — so one entry
for "Claude Code" catches "claude code", "Cloud-Code", and "CloudCode".

*Split apart.* The reverse happens to single words: "Supabase" comes back as
"Supa base", "Anthropic" as "Anthro pic". A single-word pattern therefore
tolerates separators between its letters too — but only when the word is at
least :data:`SPLIT_TOLERANT_MIN_LENGTH` characters, because letting a short word
match across gaps is how you start rewriting ordinary sentences.

**One pass.** Replacements are collected against the original string and applied
together, so a rule can never match text that another rule just produced. That
rules out cascades, and it means the reported offsets refer to what the engine
actually said.

Not corrupting real words
-------------------------
The separator tolerance is the risky part: allowing zero separator means a
pattern like ``in put`` would rewrite every ``input``. Two things guard against
it — the word-boundary requirement, and :func:`analyse`, which flags an entry
whose pattern is an everyday word (either as written, or once its spaces are
removed) before it can do any damage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .dictionary import CORRECTION, Dictionary, Entry
from .wordlist import is_common

#: Between the words of a multi-word pattern. Zero-or-more is what catches the
#: glued form; the word boundaries below are what keep that safe.
SEPARATOR = r"[\s\-_]*"

#: Apostrophes are included so a pattern never fires inside "cloud's".
BOUNDARY_BEFORE = r"(?<![\w'])"
BOUNDARY_AFTER = r"(?![\w'])"

_SPLIT_RE = re.compile(r"[\s\-_]+")
_STRIP_RE = re.compile(r"[\s\-_]+")

RISK_OK = "ok"
RISK_CAUTION = "caution"
RISK_RISKY = "risky"

#: Below this many characters, a pattern matches so often it is not worth having.
MIN_SAFE_LENGTH = 3

#: A single word shorter than this does not get split-tolerant matching. Six is
#: chosen so distinctive names ("Vercel", "Supabase") are covered while short
#: words — where a run of separated letters could plausibly occur in real text —
#: are not.
SPLIT_TOLERANT_MIN_LENGTH = 6


def normalise(text: str) -> str:
    """Collapse a phrase to its comparison key: lowercase, no separators."""
    return _STRIP_RE.sub("", text.strip().lower())


def words_of(phrase: str) -> List[str]:
    return [word for word in _SPLIT_RE.split(phrase.strip()) if word]


def pattern_for(phrase: str) -> str:
    """The regex body matching ``phrase`` in any spacing or hyphenation.

    Multi-word phrases tolerate separators between their words; a single word
    long enough to be distinctive also tolerates them between its letters, so a
    name the model split back up is still caught. See the module docstring.
    """
    parts = words_of(phrase)
    if not parts:
        raise ValueError("Cannot build a pattern from an empty phrase.")
    if len(parts) == 1 and len(parts[0]) >= SPLIT_TOLERANT_MIN_LENGTH:
        parts = list(parts[0])
    return SEPARATOR.join(re.escape(part) for part in parts)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppliedCorrection:
    """One rewrite that actually happened, for the history view."""

    entry_id: str
    matched: str       # exactly what the engine produced
    replacement: str   # what was written instead
    start: int         # offset into the original transcript
    end: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "entry": self.entry_id,
            "from": self.matched,
            "to": self.replacement,
            "at": self.start,
        }


@dataclass
class CorrectionResult:
    original: str
    text: str
    applied: List[AppliedCorrection] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.applied)

    def summary(self) -> str:
        """One line for the history row: what fired, and what it changed."""
        if not self.applied:
            return ""
        if len(self.applied) == 1:
            only = self.applied[0]
            return f"{only.matched} → {only.replacement}"
        return f"{len(self.applied)} corrections"


# ---------------------------------------------------------------------------
# Risk analysis
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Risk:
    """What the UI shows next to an entry that might misfire."""

    level: str = RISK_OK
    messages: tuple = ()

    @property
    def ok(self) -> bool:
        return self.level == RISK_OK

    @property
    def headline(self) -> str:
        return self.messages[0] if self.messages else ""


def analyse(entry: Entry, others: Optional[Iterable[Entry]] = None) -> Risk:
    """Judge whether an entry is likely to damage ordinary text.

    Called as the user types in the Dictionary editor, so it must be cheap and
    must never raise — an un-analysable entry is simply not flagged.
    """
    messages: List[str] = []
    level = RISK_OK

    def escalate(new_level: str, message: str) -> None:
        nonlocal level
        messages.append(message)
        if new_level == RISK_RISKY or level == RISK_OK:
            level = new_level

    phrase = entry.pattern.strip()
    if not phrase:
        return Risk(RISK_RISKY, ("This entry has nothing to match on.",))

    parts = words_of(phrase)
    collapsed = normalise(phrase)

    if len(collapsed) < MIN_SAFE_LENGTH:
        escalate(
            RISK_RISKY,
            f"“{phrase}” is only {len(collapsed)} characters — it will match constantly.",
        )

    if len(parts) == 1 and is_common(parts[0]):
        escalate(
            RISK_RISKY,
            f"“{parts[0]}” is an everyday word. Every ordinary use of it will be rewritten.",
        )
    elif len(parts) > 1 and is_common(collapsed):
        # The dangerous consequence of matching the glued form.
        escalate(
            RISK_RISKY,
            f"Written without spaces this is “{collapsed}”, an everyday word — "
            f"every use of it would be rewritten.",
        )

    if entry.kind == CORRECTION and normalise(entry.heard) == normalise(entry.write):
        escalate(
            RISK_CAUTION,
            "This only changes spacing or capitalisation — a term entry would do the same job.",
        )

    for other in others or ():
        if other.id == entry.id or not other.enabled:
            continue
        if normalise(other.pattern) == collapsed:
            escalate(
                RISK_CAUTION,
                f"Another entry already matches this ({other.label}); "
                f"only one of them will apply.",
            )
            break

    return Risk(level, tuple(messages))


# ---------------------------------------------------------------------------
# The ruleset
# ---------------------------------------------------------------------------


class Ruleset:
    """The compiled form of a dictionary: one regex, applied in one pass."""

    def __init__(self, entries: Sequence[Entry]):
        self._by_key: Dict[str, Entry] = {}
        alternatives: List[str] = []

        # Longest first, so `Claude Code SDK` beats `Claude Code` at the same
        # position — Python's alternation is leftmost-*first*, not longest, so
        # the ordering here is what implements the rule.
        ordered = sorted(
            (entry for entry in entries if entry.enabled and entry.pattern.strip()),
            key=lambda entry: (len(normalise(entry.pattern)), len(entry.words)),
            reverse=True,
        )
        for entry in ordered:
            key = normalise(entry.pattern)
            if key in self._by_key:
                continue  # first (longest, earliest) wins; `analyse` flags the clash
            self._by_key[key] = entry
            alternatives.append(pattern_for(entry.pattern))

        self._regex = (
            re.compile(
                BOUNDARY_BEFORE + "(?:" + "|".join(alternatives) + ")" + BOUNDARY_AFTER,
                re.IGNORECASE,
            )
            if alternatives
            else None
        )

    def __len__(self) -> int:
        return len(self._by_key)

    @property
    def empty(self) -> bool:
        return self._regex is None

    def apply(self, text: str) -> CorrectionResult:
        """Rewrite ``text``, reporting every substitution that changed something."""
        if self._regex is None or not text:
            return CorrectionResult(original=text, text=text)

        applied: List[AppliedCorrection] = []
        pieces: List[str] = []
        cursor = 0

        for match in self._regex.finditer(text):
            entry = self._by_key.get(normalise(match.group(0)))
            if entry is None:  # pragma: no cover - keys are built from the same patterns
                continue
            matched = match.group(0)
            replacement = entry.replacement
            if matched == replacement:
                continue  # already correct; not worth reporting as a change

            pieces.append(text[cursor : match.start()])
            pieces.append(replacement)
            cursor = match.end()
            applied.append(
                AppliedCorrection(
                    entry_id=entry.id,
                    matched=matched,
                    replacement=replacement,
                    start=match.start(),
                    end=match.end(),
                )
            )

        if not applied:
            return CorrectionResult(original=text, text=text)

        pieces.append(text[cursor:])
        return CorrectionResult(original=text, text="".join(pieces), applied=applied)


def ruleset_for(dictionary: Dictionary) -> Ruleset:
    return Ruleset(dictionary.entries)


def bias_prompt(terms: Sequence[str]) -> str:
    """Phrase the vocabulary list the way Whisper-style prompts expect.

    A bare comma-separated list works, but a short natural sentence primes the
    decoder better and costs only a few tokens.
    """
    kept = [term.strip() for term in terms if term.strip()]
    if not kept:
        return ""
    return "Glossary: " + ", ".join(kept) + "."
