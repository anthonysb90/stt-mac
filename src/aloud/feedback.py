"""Audible cues for state changes.

Dictation is eyes-free by nature: you are looking at the app you are dictating
into, not at Aloud. Two short system sounds are what actually tell you that
recording started and stopped, and they remain the fastest feedback in the app
even now that there is a window and a level meter to look at.

Errors get a sound here and an alert from the app layer; this module stays out
of the business of presenting them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

log = logging.getLogger(__name__)

#: Where macOS keeps alert sounds, most specific first so a user's own copy of
#: a name wins over the system one.
SOUND_DIRS = (
    Path.home() / "Library" / "Sounds",
    Path("/Library/Sounds"),
    Path("/System/Library/Sounds"),
)

SOUND_SUFFIXES = {".aiff", ".aif", ".wav", ".m4a", ".caf", ".aifc"}

#: What the built-in sounds actually sound like. Choosing a start and stop cue
#: from a list of bare names is guesswork, and this is a setting people will
#: want to get right once and then forget.
SOUND_DESCRIPTIONS = {
    "Basso": "harsh error thud",
    "Blow": "breathy and airy",
    "Bottle": "soft rising bloop",
    "Frog": "comic croak",
    "Funk": "buzzy and jarring",
    "Glass": "bright clean chime",
    "Hero": "triumphant, long",
    "Morse": "short flat beep",
    "Ping": "neutral ping",
    "Pop": "hollow thunk",
    "Purr": "soft warm descent",
    "Sosumi": "sharp and harsh",
    "Submarine": "deep sonar ping",
    "Tink": "light high ping",
}

#: Sounds that read as failure. Offered, but never suggested for start or stop.
UNSUITABLE_FOR_CUES = {"Basso", "Funk", "Sosumi"}


def available_sounds() -> List[str]:
    """Every alert sound this Mac can play, by name."""
    names = set()
    for directory in SOUND_DIRS:
        if not directory.is_dir():
            continue
        try:
            for entry in directory.iterdir():
                if entry.suffix.lower() in SOUND_SUFFIXES:
                    names.add(entry.stem)
        except OSError:
            continue
    return sorted(names)


def describe(name: str) -> str:
    """"Bottle — soft rising bloop", for a picker you can choose from."""
    detail = SOUND_DESCRIPTIONS.get(name)
    return f"{name} — {detail}" if detail else name


def name_from_description(label: str) -> str:
    return label.split(" — ", 1)[0]


def play(name: str) -> bool:
    """Play a sound once, by name. Returns whether it was found."""
    if not name:
        return False
    try:
        import AppKit

        sound = AppKit.NSSound.soundNamed_(name)
        if sound is None:
            log.debug("No system sound named %r", name)
            return False
        sound.stop()
        sound.play()
        return True
    except Exception:
        log.exception("Could not play sound %r", name)
        return False


class Feedback:
    def __init__(self, options: Dict[str, Any] | None = None) -> None:
        self.update(options)

    def update(self, options: Dict[str, Any] | None = None) -> None:
        """Re-read the settings, so a change in Settings takes effect at once."""
        options = options or {}
        self.enabled = bool(options.get("sounds", True))
        self.start_sound = options.get("start_sound", "Bottle")
        self.stop_sound = options.get("stop_sound", "Glass")
        self.error_sound = options.get("error_sound", "Basso")

    def _play(self, name: str) -> None:
        if not self.enabled:
            return
        play(name)

    def recording_started(self) -> None:
        self._play(self.start_sound)

    def recording_stopped(self) -> None:
        self._play(self.stop_sound)

    def error(self) -> None:
        self._play(self.error_sound)
