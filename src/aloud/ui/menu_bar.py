"""The menu bar item — secondary now, but still the thing you use most.

When Aloud is not the front app (which is most of the time, since you dictate
*into* other apps) this is the only visible surface. It has to answer two
questions at a glance — is it listening, and is it recording — and offer the
one action you might need without switching apps.

The glyph carries state on its own so the answer is available in peripheral
vision, without opening the menu.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

import AppKit

from .. import APP_NAME
from ..core import State
from ..hotkey import describe
from . import components as C
from . import tokens as T

#: A shape per state. Filled means live.
GLYPHS = {
    State.IDLE: "◌",
    State.RECORDING: "●",
    State.TRANSCRIBING: "◍",
    State.ERROR: "⊘",
}

GLYPH_COLOURS = {
    State.IDLE: T.TEXT_SECONDARY,
    State.RECORDING: T.STATUS_RECORDING_FILL,
    State.TRANSCRIBING: T.STATUS_TRANSCRIBING,
    State.ERROR: T.STATUS_ERROR,
}


class MenuBarItem:
    def __init__(self, controller, handlers: Dict[str, Callable]) -> None:
        self.controller = controller
        self.handlers = handlers
        self._keeper: list = []

        self.item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        self.item.button().setToolTip_(APP_NAME)
        self.menu = AppKit.NSMenu.alloc().init()
        self.item.setMenu_(self.menu)

        self.status_item = self._entry("Idle", None)
        self.hotkey_item = self._entry("", None)
        self.toggle_item = self._entry("Start Dictation", handlers["toggle"])
        self._rebuild()
        self.set_state(State.IDLE)

    # -- construction ------------------------------------------------------

    def _entry(self, title: str, handler: Optional[Callable]) -> AppKit.NSMenuItem:
        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        if handler is not None:
            target = C.action(lambda _sender, fn=handler: fn())
            self._keeper.append(target)
            item.setTarget_(target)
            item.setAction_(b"invoke:")
        item.setAttributedTitle_(_styled(title, T.TEXT_PRIMARY if handler else T.TEXT_SECONDARY))
        return item

    def _rebuild(self) -> None:
        self.menu.removeAllItems()
        self.menu.addItem_(self.status_item)
        self.menu.addItem_(self.hotkey_item)
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.menu.addItem_(self.toggle_item)
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.menu.addItem_(self._entry(f"Open {APP_NAME}", self.handlers["open_main"]))
        self.menu.addItem_(self._entry("Settings…", self.handlers["open_settings"]))
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.menu.addItem_(self._entry(f"Quit {APP_NAME}", self.handlers["quit"]))

    # -- state -------------------------------------------------------------

    def set_state(self, state: State) -> None:
        button = self.item.button()
        button.setAttributedTitle_(
            _styled(GLYPHS[state], GLYPH_COLOURS[state], T.TYPE_TITLE_3)
        )
        button.setToolTip_(f"{APP_NAME} — {state.value}")
        self._set_title(self.status_item, state.value, T.TEXT_SECONDARY)
        self._set_title(
            self.toggle_item,
            "Stop Dictation" if state is State.RECORDING else "Start Dictation",
            T.TEXT_PRIMARY,
        )
        self.toggle_item.setEnabled_(state is not State.TRANSCRIBING)

    def set_hotkey(self, key: str, mode: str) -> None:
        verb = "Hold" if mode == "hold" else "Tap"
        self._set_title(self.hotkey_item, f"{verb} {describe(key)}", T.TEXT_SECONDARY)

    def _set_title(self, item: AppKit.NSMenuItem, title: str, colour: T.Color) -> None:
        item.setTitle_(title)
        item.setAttributedTitle_(_styled(title, colour))

    def remove(self) -> None:
        AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self.item)


def _styled(text: str, colour: T.Color, style: T.TextStyle = T.TYPE_BODY):
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(
        text,
        {
            AppKit.NSFontAttributeName: T.ns_font(style),
            AppKit.NSForegroundColorAttributeName: T.ns_color(colour),
        },
    )
