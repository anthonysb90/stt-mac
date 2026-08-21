"""Audible and visual cues for state changes.

Dictation is eyes-free by nature: the user is looking at the app they are
dictating into, not at the menu bar. Short system sounds are what actually
tell them recording started and stopped.
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
        self.notify_on_error = bool(options.get("notify_on_error", True))

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

    def error(self, title: str, message: str) -> None:
        self._play(self.error_sound)
        if not self.notify_on_error:
            return
        try:
            import rumps

            rumps.notification(title=title, subtitle="", message=message)
        except Exception:
            # Notifications only work from a signed bundle; the log is the fallback.
            log.debug("Notification suppressed: %s — %s", title, message)
