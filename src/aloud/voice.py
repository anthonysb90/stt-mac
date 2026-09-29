"""Smarter dictation: spoken commands, snippets, and formatting per app.

Three small things that make dictation feel like it understands where it is:

* **"Scratch that."** Said on its own, it removes the dictation you just made
  — as long as it was in the last minute and you are still in the same app,
  so it can never reach back and undo something unrelated.
* **Snippets.** Say "insert my signature" (or any trigger you set) and the
  saved text is typed instead: an address, a sign-off, a standard reply.
* **Per-app formatting.** A chat message does not end in a full stop; a
  terminal command should not be capitalised. The app in front decides:
  ``chat`` drops the final period, ``code`` leaves the text exactly as
  spoken (no capital, no trailing space), ``prose`` is the default.

Pure text logic; the app-facing parts (which app is in front, sending ⌘Z)
live in :mod:`aloud.injector`.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

#: Whole utterances that mean "remove what I just dictated".
SCRATCH_PHRASES = ("scratch that", "delete that", "undo that", "strike that")

#: Built-in styles by bundle identifier. Settings/config can override any.
DEFAULT_APP_STYLES: Dict[str, str] = {
    # chat: messages do not end in a full stop
    "com.apple.MobileSMS": "chat",
    "com.tinyspeck.slackmacgap": "chat",
    "net.whatsapp.WhatsApp": "chat",
    "com.hnc.Discord": "chat",
    "ru.keepcoder.Telegram": "chat",
    "com.microsoft.teams2": "chat",
    "com.microsoft.teams": "chat",
    "com.facebook.archon": "chat",  # Messenger
    "org.whispersystems.signal-desktop": "chat",
    # code: exactly as spoken
    "com.apple.Terminal": "code",
    "com.googlecode.iterm2": "code",
    "com.microsoft.VSCode": "code",
    "com.apple.dt.Xcode": "code",
    "dev.warp.Warp-Stable": "code",
    "com.todesktop.230313mzl4w4u92": "code",  # Cursor
}

STYLES = ("prose", "chat", "code")


def _normalise(text: str) -> str:
    return re.sub(r"[^\w\s']", "", text).strip().lower()


def is_scratch(text: str) -> bool:
    """Whether a whole dictation is a request to remove the previous one."""
    return _normalise(text) in SCRATCH_PHRASES


def expand_snippets(text: str, snippets: Optional[Dict[str, str]]) -> str:
    """Replace "insert <trigger>" -- or a dictation that is only the trigger.

    Matching ignores case and surrounding punctuation, so "Insert my
    signature." works however the engine punctuated it. Longest trigger
    first, so "my address" cannot shadow "my address line two".
    """
    if not snippets or not text:
        return text
    triggers = sorted((t for t in snippets if t.strip()), key=len, reverse=True)
    if not triggers:
        return text
    spoken = _normalise(text)
    for trigger in triggers:
        key = _normalise(trigger)
        if spoken in (key, f"insert {key}"):
            return snippets[trigger]
    for trigger in triggers:
        words = r"\s+".join(re.escape(w) for w in _normalise(trigger).split())
        pattern = re.compile(r"\binsert\s+" + words + r"\b[.,!?]?", re.IGNORECASE)
        text = pattern.sub(lambda _m, t=trigger: snippets[t], text)
    return text


def style_for(bundle_id: str, overrides: Optional[Dict[str, str]] = None) -> str:
    """The formatting style for the app in front."""
    if overrides and bundle_id in overrides and overrides[bundle_id] in STYLES:
        return overrides[bundle_id]
    return DEFAULT_APP_STYLES.get(bundle_id or "", "prose")


def apply_style(text: str, style: str) -> str:
    """Adjust finished text for the kind of app it is going into."""
    if style == "chat":
        # One trailing full stop, not "?" or "!" or an ellipsis.
        if text.endswith(".") and not text.endswith(".."):
            return text[:-1]
        return text
    if style == "code":
        text = text.rstrip(".")
        if text[:1].isupper() and not text[:2].isupper():
            text = text[0].lower() + text[1:]
        return text
    return text


def trailing_space(style: str, default: bool) -> bool:
    """Whether to add a space after the text: never into code."""
    return False if style == "code" else default
