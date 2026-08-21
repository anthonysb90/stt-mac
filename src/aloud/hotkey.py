"""Global push-to-talk hotkey, built on a Quartz event tap.

A CGEventTap sees keyboard events system-wide before they reach the focused
app, which is the only way to implement "hold this key anywhere" on macOS.
It requires the Accessibility permission (see :mod:`aloud.permissions`).

The tap is installed in *listen-only* mode: we observe the key but never
swallow it. That keeps the tap from interfering with normal typing, at the
cost of the chosen key still performing its usual job. Picking a modifier that
does nothing on its own -- right Option is the default -- sidesteps that.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Tuple

import Quartz

log = logging.getLogger(__name__)

#: name -> (virtual keycode, event flag mask)
#:
#: Left and right variants of a modifier share a flag mask but have distinct
#: keycodes, which is how we tell "right Option" from "left Option".
MODIFIER_KEYS: dict[str, Tuple[int, int]] = {
    "fn": (63, Quartz.kCGEventFlagMaskSecondaryFn),
    "left_shift": (56, Quartz.kCGEventFlagMaskShift),
    "right_shift": (60, Quartz.kCGEventFlagMaskShift),
    "left_control": (59, Quartz.kCGEventFlagMaskControl),
    "right_control": (62, Quartz.kCGEventFlagMaskControl),
    "left_option": (58, Quartz.kCGEventFlagMaskAlternate),
    "right_option": (61, Quartz.kCGEventFlagMaskAlternate),
    "left_command": (55, Quartz.kCGEventFlagMaskCommand),
    "right_command": (54, Quartz.kCGEventFlagMaskCommand),
}

_TAP_DISABLED_EVENTS = (
    Quartz.kCGEventTapDisabledByTimeout,
    Quartz.kCGEventTapDisabledByUserInput,
)


def _main_runloop():
    """The main CFRunLoop, however this PyObjC build exposes it."""
    getter = getattr(Quartz, "CFRunLoopGetMain", None) or Quartz.CFRunLoopGetCurrent
    return getter()


class HotkeyError(RuntimeError):
    """Raised when the event tap cannot be created."""


class HotkeyListener:
    """Watches one modifier key and reports press/release.

    In ``hold`` mode ``on_press``/``on_release`` fire on the key's own edges.
    In ``toggle`` mode each press alternates between the two callbacks, so the
    rest of the app sees an identical start/stop pair either way.

    Callbacks run on the main run loop thread and must return quickly; anything
    slow will make the tap time out and be disabled by the system.
    """

    def __init__(
        self,
        key: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
        mode: str = "hold",
    ) -> None:
        if key not in MODIFIER_KEYS:
            raise HotkeyError(
                f"Unknown hotkey {key!r}. Choose one of: {', '.join(sorted(MODIFIER_KEYS))}"
            )
        self.key = key
        self.mode = mode
        self._keycode, self._mask = MODIFIER_KEYS[key]
        self._on_press = on_press
        self._on_release = on_release
        self._tap = None
        self._source = None
        self._runloop = None
        self._down = False
        self._toggled_on = False
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def install(self, runloop=None) -> None:
        """Create the tap and attach it to a run loop. Call on the main thread."""
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._handle,
            None,
        )
        if self._tap is None:
            raise HotkeyError(
                "Could not create a keyboard event tap. Grant Accessibility "
                "access in System Settings > Privacy & Security > Accessibility."
            )

        self._source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._runloop = runloop or _main_runloop()
        Quartz.CFRunLoopAddSource(
            self._runloop, self._source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(self._tap, True)
        log.info("Hotkey listener installed: %s (%s mode)", self.key, self.mode)

    def uninstall(self) -> None:
        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
        if self._source is not None and self._runloop is not None:
            Quartz.CFRunLoopRemoveSource(
                self._runloop, self._source, Quartz.kCFRunLoopCommonModes
            )
        self._tap = None
        self._source = None
        self._runloop = None

    # -- tap callback ------------------------------------------------------

    def _handle(self, proxy, event_type, event, refcon):
        # The system disables a tap that blocks for too long; re-arm it.
        if event_type in _TAP_DISABLED_EVENTS:
            log.warning("Event tap was disabled by the system; re-enabling")
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event

        if event_type != Quartz.kCGEventFlagsChanged:
            return event

        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )
        if keycode != self._keycode:
            return event

        is_down = bool(Quartz.CGEventGetFlags(event) & self._mask)
        try:
            self._dispatch(is_down)
        except Exception:  # never let an exception escape into the tap
            log.exception("Hotkey callback raised")
        return event

    def _dispatch(self, is_down: bool) -> None:
        with self._lock:
            if is_down == self._down:
                return  # key repeat / duplicate flag event
            self._down = is_down

            if self.mode == "toggle":
                if not is_down:
                    return  # act on press only
                self._toggled_on = not self._toggled_on
                callback = self._on_press if self._toggled_on else self._on_release
            else:
                callback = self._on_press if is_down else self._on_release

        callback()

    # -- state -------------------------------------------------------------

    @property
    def active(self) -> bool:
        """True while the app considers itself 'holding' the key."""
        return self._toggled_on if self.mode == "toggle" else self._down

    def reset(self) -> None:
        with self._lock:
            self._down = False
            self._toggled_on = False


def describe(key: str) -> str:
    """A human-readable label for a hotkey name, e.g. ``Right Option``."""
    if key == "fn":
        return "Fn"
    return key.replace("_", " ").title()


def available_keys() -> list[str]:
    return sorted(MODIFIER_KEYS)
