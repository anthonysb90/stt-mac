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
import Foundation

from .. import APP_NAME
from .. import audio
from ..core import State
from ..hotkey import describe
from . import components as C
from . import dock
from . import tokens as T

#: SF Symbols, drawn as *template* images so macOS tints them itself — black on
#: a light menu bar, white on a dark one, dimmed when the bar is inactive.
#:
#: The first version of this drew a coloured text glyph instead, which was a
#: mistake: a custom grey on a bar the system also draws in grey is very close
#: to invisible, and the colour could not follow the bar's own appearance.
#: Recording is the one state that overrides the tint, because "the microphone
#: is live" has to be visible in peripheral vision.
SYMBOLS = {
    State.IDLE: "mic",
    State.RECORDING: "mic.fill",
    State.TRANSCRIBING: "waveform",
    State.ERROR: "exclamationmark.triangle",
}

#: Used only where SF Symbols are unavailable (before Big Sur). Set as a plain
#: title so the system picks the colour.
GLYPHS = {
    State.IDLE: "◌",
    State.RECORDING: "●",
    State.TRANSCRIBING: "◍",
    State.ERROR: "⊘",
}

#: Only states that must override the system tint appear here.
GLYPH_TINTS = {
    State.RECORDING: T.STATUS_RECORDING_FILL,
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
        self.dock_item = self._entry("Show in Dock", self._toggle_dock)
        # With the Dock icon hidden this menu is the entire application, so its
        # checkmarks have to be right every time it opens -- Settings can change
        # the same value from the other side.
        self._refresher = C.MenuRefresher.alloc().initWithHandler_(self._refresh)
        self._keeper.append(self._refresher)
        self.menu.setDelegate_(self._refresher)
        self._rebuild()
        self.set_state(State.IDLE)

    # -- the Dock icon -----------------------------------------------------

    def _toggle_dock(self) -> None:
        visible = not bool(self.controller.config.get("interface.dock_icon", True))
        self.controller.config.set("interface.dock_icon", visible)
        self.controller.config.save()
        dock.apply(visible)
        # No need to tick the item here: clicking it dismisses the menu, and
        # the delegate rebuilds the checkmarks the next time it opens.

    def _refresh(self, _menu=None) -> None:
        """Bring the checkmarks up to date just before the menu is shown."""
        self.dock_item.setState_(
            AppKit.NSControlStateValueOn
            if bool(self.controller.config.get("interface.dock_icon", True))
            else AppKit.NSControlStateValueOff
        )

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

    def _microphone_item(self) -> AppKit.NSMenuItem:
        holder = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Microphone", None, ""
        )
        holder.setSubmenu_(C.refreshing_menu(self._build_microphone_menu, self._keeper))
        return holder

    def _build_microphone_menu(self, menu) -> None:
        """Rebuilt on every open, so a mic plugged in a moment ago is there."""
        menu.removeAllItems()
        current = self.controller.input_device()

        menu.addItem_(C.menu_item(
            audio.describe_device(None), lambda: self.controller.set_input_device(None),
            self._keeper, checked=current is None,
        ))
        devices = audio.list_input_devices()
        if devices:
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
        for device in devices:
            index = device["index"]
            menu.addItem_(C.menu_item(
                device["name"],
                lambda i=index: self.controller.set_input_device(i),
                self._keeper,
                checked=(current == index or current == device["name"]),
            ))
        if not devices:
            menu.addItem_(C.menu_item("No microphones found", None, self._keeper, enabled=False))

    def _rebuild(self) -> None:
        self.menu.removeAllItems()
        self.menu.addItem_(self.status_item)
        self.menu.addItem_(self.hotkey_item)
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.menu.addItem_(self.toggle_item)
        self.menu.addItem_(self._microphone_item())
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.menu.addItem_(self._entry(f"Open {APP_NAME}", self.handlers["open_main"]))
        self.menu.addItem_(self.dock_item)
        self.menu.addItem_(self._entry("Settings…", self.handlers["open_settings"]))
        self.menu.addItem_(AppKit.NSMenuItem.separatorItem())
        self.menu.addItem_(self._entry(f"Quit {APP_NAME}", self.handlers["quit"]))

    # -- state -------------------------------------------------------------

    def set_state(self, state: State) -> None:
        button = self.item.button()
        self._draw_glyph(button, state)
        button.setToolTip_(f"{APP_NAME} — {state.value}")
        self._set_title(self.status_item, state.value, T.TEXT_SECONDARY)
        self._set_title(
            self.toggle_item,
            "Stop Dictation" if state is State.RECORDING else "Start Dictation",
            T.TEXT_PRIMARY,
        )
        self.toggle_item.setEnabled_(state is not State.TRANSCRIBING)

    @staticmethod
    def _draw_glyph(button, state: State) -> None:
        """Prefer a template symbol; fall back to a system-coloured glyph."""
        image = None
        if hasattr(AppKit.NSImage, "imageWithSystemSymbolName_accessibilityDescription_"):
            image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                SYMBOLS[state], f"{APP_NAME} {state.value}"
            )

        tint = GLYPH_TINTS.get(state)
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
            button.setTitle_("")
            # A template image is tinted by the system unless we say otherwise.
            button.setContentTintColor_(T.ns_color(tint) if tint else None)
            return

        button.setImage_(None)
        if tint is None:
            # No attributed string: the system knows what colour its own menu
            # bar text should be, and it changes with the wallpaper.
            button.setTitle_(GLYPHS[state])
        else:
            button.setAttributedTitle_(_styled(GLYPHS[state], tint, T.TYPE_TITLE_3))

    def set_hotkey(self, key: str, mode: str) -> None:
        verb = "Hold" if mode == "hold" else "Tap"
        self._set_title(self.hotkey_item, f"{verb} {describe(key)}", T.TEXT_SECONDARY)

    def _set_title(self, item: AppKit.NSMenuItem, title: str, colour: T.Color) -> None:
        item.setTitle_(title)
        item.setAttributedTitle_(_styled(title, colour))

    def remove(self) -> None:
        AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self.item)


def _styled(text: str, colour: T.Color, style: T.TextStyle = T.TYPE_BODY):
    return Foundation.NSAttributedString.alloc().initWithString_attributes_(
        text,
        {
            AppKit.NSFontAttributeName: T.ns_font(style),
            AppKit.NSForegroundColorAttributeName: T.ns_color(colour),
        },
    )
