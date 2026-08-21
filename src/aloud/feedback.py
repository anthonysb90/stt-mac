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
from typing import Any, Dict

log = logging.getLogger(__name__)


class Feedback:
    def __init__(self, options: Dict[str, Any] | None = None) -> None:
        options = options or {}
        self.enabled = bool(options.get("sounds", True))
        self.start_sound = options.get("start_sound", "Tink")
        self.stop_sound = options.get("stop_sound", "Pop")
        self.error_sound = options.get("error_sound", "Basso")

    def _play(self, name: str) -> None:
        if not self.enabled or not name:
            return
        try:
            import AppKit

            sound = AppKit.NSSound.soundNamed_(name)
            if sound is None:
                log.debug("No system sound named %r", name)
                return
            sound.stop()
            sound.play()
        except Exception:
            log.exception("Could not play sound %r", name)

    def recording_started(self) -> None:
        self._play(self.start_sound)

    def recording_stopped(self) -> None:
        self._play(self.stop_sound)

    def error(self) -> None:
        self._play(self.error_sound)
