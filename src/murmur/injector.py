"""Deliver text into whichever app currently has focus.

Two strategies, both requiring the Accessibility permission:

``paste``  Put the text on the pasteboard and synthesize Cmd-V. Near-instant
           regardless of length, and correct in essentially every app. The
           previous clipboard contents are restored afterwards.
``type``   Synthesize the characters themselves via a Unicode keyboard event.
           Slower, but leaves the clipboard untouched and works in the handful
           of fields that reject paste.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import AppKit
import Quartz

log = logging.getLogger(__name__)

_V_KEYCODE = 9
#: Unicode events carry the payload in a string, so the keycode is irrelevant.
_UNICODE_KEYCODE = 0
#: CGEventKeyboardSetUnicodeString is unreliable for very long strings.
_TYPE_CHUNK = 20


class InjectionError(RuntimeError):
    pass


# -- pasteboard ------------------------------------------------------------


def read_clipboard() -> Optional[str]:
    pasteboard = AppKit.NSPasteboard.generalPasteboard()
    return pasteboard.stringForType_(AppKit.NSPasteboardTypeString)


def write_clipboard(text: str) -> None:
    pasteboard = AppKit.NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)


# -- key synthesis ---------------------------------------------------------


def _post(event) -> None:
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def send_command_v() -> None:
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateKeyboardEvent(source, _V_KEYCODE, True)
    up = Quartz.CGEventCreateKeyboardEvent(source, _V_KEYCODE, False)
    Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
    _post(down)
    _post(up)


def type_text(text: str, chunk_delay: float = 0.005) -> None:
    """Synthesize the text as Unicode keyboard events."""
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for start in range(0, len(text), _TYPE_CHUNK):
        chunk = text[start : start + _TYPE_CHUNK]
        for is_down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(source, _UNICODE_KEYCODE, is_down)
            Quartz.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
            _post(event)
        time.sleep(chunk_delay)


# -- public API ------------------------------------------------------------


class TextInjector:
    """Applies the configured delivery strategy."""

    def __init__(
        self,
        mode: str = "paste",
        restore_clipboard: bool = True,
        restore_delay: float = 0.8,
        trailing_space: bool = True,
    ) -> None:
        self.mode = mode
        self.restore_clipboard = restore_clipboard
        self.restore_delay = restore_delay
        self.trailing_space = trailing_space

    def deliver(self, text: str) -> None:
        if not text:
            return
        payload = text + " " if self.trailing_space and not text.endswith(("\n", " ")) else text

        if self.mode == "clipboard":
            write_clipboard(payload)
            return
        if self.mode == "type":
            type_text(payload)
            return
        if self.mode != "paste":
            raise InjectionError(f"Unknown output mode {self.mode!r}")

        previous = read_clipboard() if self.restore_clipboard else None
        write_clipboard(payload)
        # A beat for the pasteboard write to land before the target app reads it.
        time.sleep(0.03)
        send_command_v()

        if previous is not None:
            self._restore_later(previous, payload)

    def _restore_later(self, previous: str, payload: str) -> None:
        """Put the old clipboard back, but only if we still own the pasteboard."""

        def restore() -> None:
            time.sleep(self.restore_delay)
            try:
                if read_clipboard() == payload:
                    write_clipboard(previous)
            except Exception:
                log.exception("Failed to restore the clipboard")

        threading.Thread(target=restore, name="murmur-clipboard", daemon=True).start()
