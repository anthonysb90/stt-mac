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
import Foundation
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


#: A full pasteboard snapshot: one list of (type, bytes) per pasteboard item.
Snapshot = list


def snapshot_clipboard() -> Optional[Snapshot]:
    """Everything on the pasteboard, whatever its type.

    Reading only the string, as this used to, meant a copied screenshot or a
    Finder file was destroyed by dictating: the string read came back None, so
    nothing was restored, despite ``restore_clipboard=True`` promising exactly
    that. Items are captured with all of their representations so an image, a
    file reference, or rich text survives the round trip.

    Returns None for an empty pasteboard, and an empty snapshot ([]) when the
    pasteboard has items that cannot be read -- the caller treats both as
    "nothing to restore", which is the safe direction: better to skip a
    restore than to write back a corrupted one.
    """
    pasteboard = AppKit.NSPasteboard.generalPasteboard()
    try:
        items = pasteboard.pasteboardItems()
        if not items:
            return None
        captured: Snapshot = []
        for item in items:
            representations = []
            for kind in item.types():
                data = item.dataForType_(kind)
                if data is not None:
                    representations.append((str(kind), bytes(data)))
            if representations:
                captured.append(representations)
        return captured or None
    except Exception:
        log.exception("Could not snapshot the pasteboard; skipping restore")
        return None


def restore_clipboard(snapshot: Snapshot) -> None:
    """Put a snapshot back, rebuilding each item with its original types."""
    pasteboard = AppKit.NSPasteboard.generalPasteboard()
    rebuilt = []
    for representations in snapshot:
        item = AppKit.NSPasteboardItem.alloc().init()
        for kind, payload in representations:
            item.setData_forType_(
                Foundation.NSData.dataWithBytes_length_(payload, len(payload)), kind
            )
        rebuilt.append(item)
    pasteboard.clearContents()
    pasteboard.writeObjects_(rebuilt)


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
        #: The clipboard as it was before a run of quick dictations, and the
        #: text we last put there. See _take_snapshot.
        self._lock = threading.Lock()
        self._original: Optional[Snapshot] = None
        self._last_payload: Optional[str] = None
        self._generation = 0

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

        generation = self._take_snapshot() if self.restore_clipboard else None
        write_clipboard(payload)
        with self._lock:
            self._last_payload = payload
        # A beat for the pasteboard write to land before the target app reads it.
        time.sleep(0.03)
        send_command_v()

        if generation is not None:
            self._restore_later(generation, payload)

    def _take_snapshot(self) -> int:
        """Remember what to put back, once per run of quick dictations.

        Two dictations inside the restore delay used to lose the clipboard:
        the second snapshot captured the first dictation's text, the first
        restore stood down (the clipboard no longer matched), and the second
        restore put the *dictation* back instead of what the person had
        copied. Now a snapshot taken while our own text is still on the
        clipboard is not a snapshot at all -- the original is kept, and only
        the latest dictation's restore runs.
        """
        with self._lock:
            ours = self._original is not None and self._last_payload is not None
            if not ours or read_clipboard() != self._last_payload:
                # Nothing pending, or the person copied something new since.
                self._original = snapshot_clipboard()
            self._generation += 1
            return self._generation

    def _restore_later(self, generation: int, payload: str) -> None:
        """Put the old clipboard back, but only if we still own the pasteboard."""

        def restore() -> None:
            time.sleep(self.restore_delay)
            try:
                with self._lock:
                    if generation != self._generation:
                        return  # a later dictation will restore instead
                    previous, self._original = self._original, None
                    self._last_payload = None
                if previous is not None and read_clipboard() == payload:
                    restore_clipboard(previous)
            except Exception:
                log.exception("Failed to restore the clipboard")

        threading.Thread(target=restore, name="aloud-clipboard", daemon=True).start()
