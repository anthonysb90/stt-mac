"""The main window: transport at the top, History or Dictionary below.

Layout reasoning. The transport strip — state, level meter, start/stop — is
pinned above both panes rather than living inside one, because it is the only
part of the window that is true regardless of what you are looking at. Sinking
the meter into the History pane would mean switching to the Dictionary hides
whether the microphone is live.

The panes are a segmented control rather than a sidebar. Two destinations do
not earn 196pt of permanent chrome, and Cmd-1 / Cmd-2 reach them faster than a
pointer does.
"""

from __future__ import annotations

from typing import Callable

import AppKit
import Foundation
import objc

from .. import APP_NAME, audio, media
from ..core import State
from ..hotkey import describe
from . import components as C
from . import tokens as T
from .dictionary_view import DictionaryView
from .history_view import HistoryView
from .transcribe_view import TranscribeView
from .formatting import db_label, shorten as media_label
from .meter import LevelMeter

HISTORY, DICTIONARY, TRANSCRIBE = 0, 1, 2

STATE_COLOURS = {
    State.IDLE: T.STATUS_IDLE,
    State.RECORDING: T.STATUS_RECORDING,
    State.TRANSCRIBING: T.STATUS_TRANSCRIBING,
    State.ERROR: T.STATUS_ERROR,
}


class _WindowDelegate(Foundation.NSObject):
    """Keeps the app alive when the window closes, as a real Mac app does."""

    def initWithHandler_(self, handler):
        self = objc.super(_WindowDelegate, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def windowWillClose_(self, _notification):
        self._handler()


class MainWindow:
    def __init__(self, controller, on_settings: Callable,
                 on_transcribe: Callable) -> None:
        self.controller = controller
        self._on_settings = on_settings
        self._on_transcribe = on_transcribe
        self._keeper: list = []
        self._pane = HISTORY
        self._has_been_placed = False

        self.history = HistoryView()
        self.dictionary = DictionaryView(
            controller.dictionary, on_changed=controller.reload_rules
        )
        self.transcribe = TranscribeView(on_transcribe=on_transcribe)

        self.state_pill = C.label("Idle", T.TYPE_BODY_STRONG, T.STATUS_IDLE)
        self.meter = LevelMeter.alloc().initWithSource_(lambda: self.controller.level)
        self.meter_readout = C.label("—", T.TYPE_CAPTION, T.TEXT_TERTIARY, align="right")
        self.meter_readout.widthAnchor().constraintEqualToConstant_(T.METRIC["meter_readout_width"]).setActive_(True)
        self.hotkey_hint = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY)
        self.device_popup = self._build_device_popup()
        self.record_button = C.button("Start Dictation", lambda _s: self.controller.toggle(),
                                      self._keeper, prominent=True)

        self.segments = self._build_segments()
        self.pane_host = C.stack([], spacing=0)
        self.pane_host.setAlignment_(AppKit.NSLayoutAttributeLeading)

        self.window = self._build_window()
        self._delegate = _WindowDelegate.alloc().initWithHandler_(self._closed)
        self.window.setDelegate_(self._delegate)

        self.refresh_hotkey()
        self.show_pane(HISTORY)
        self.set_state(controller.state)

    # -- construction ------------------------------------------------------

    def _build_segments(self) -> AppKit.NSSegmentedControl:
        target = C.action(lambda sender: self.show_pane(sender.selectedSegment()))
        self._keeper.append(target)
        control = AppKit.NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            ["History", "Dictionary", "Transcribe"],
            AppKit.NSSegmentSwitchTrackingSelectOne,
            target,
            b"invoke:",
        )
        control.setSelectedSegment_(HISTORY)
        control.setTranslatesAutoresizingMaskIntoConstraints_(False)
        return control

    def _build_device_popup(self) -> AppKit.NSPopUpButton:
        return C.menu_popup(
            self._build_device_menu, self._keeper,
            width=T.METRIC["device_popup_width"],
            tooltip="Which microphone to record from",
        )

    def _build_device_menu(self, menu) -> None:
        menu.removeAllItems()
        current = self.controller.input_device()
        menu.addItem_(C.menu_item(
            media_label(audio.describe_device(None)),
            lambda: self.controller.set_input_device(None),
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
        self._select_current_device(menu, current)

    def _select_current_device(self, menu, current) -> None:
        for item in menu.itemArray():
            if item.state() == AppKit.NSControlStateValueOn:
                self.device_popup.selectItem_(item)
                return
        if menu.numberOfItems():
            self.device_popup.selectItemAtIndex_(0)

    def on_devices_changed(self) -> None:
        self._build_device_menu(self.device_popup.menu())

    def _transport(self) -> AppKit.NSView:
        meter_column = C.stack(
            [
                C.stack([self.meter, self.meter_readout], vertical=False, spacing=T.SPACE["md"]),
                self.hotkey_hint,
            ],
            spacing=T.SPACE["xs"],
        )
        meter_column.setAlignment_(AppKit.NSLayoutAttributeLeading)
        self.meter.widthAnchor().constraintGreaterThanOrEqualToConstant_(T.METRIC["meter_width_min"]).setActive_(True)

        row = C.stack(
            [self.state_pill, meter_column, C.spacer(),
             self.device_popup, self.record_button],
            vertical=False, spacing=T.SPACE["xl"],
        )
        self.state_pill.widthAnchor().constraintEqualToConstant_(T.METRIC["state_pill_width"]).setActive_(True)

        bar = C.card(fill=T.BG_SURFACE, border=None, radius=0)
        C.pad(row, T.INSET["card"], container=bar)
        return bar

    def _build_window(self) -> AppKit.NSWindow:
        transport = self._transport()
        chrome = C.stack([self.segments, C.spacer()], vertical=False, spacing=T.SPACE["md"])

        body = C.stack([chrome, self.pane_host], spacing=T.SPACE["xl"])
        body.setAlignment_(AppKit.NSLayoutAttributeLeading)
        padded_body = AppKit.NSView.alloc().init()
        C.pad(body, T.INSET["window"], container=padded_body)

        root = C.stack([transport, C.separator(), padded_body], spacing=0)
        root.setAlignment_(AppKit.NSLayoutAttributeLeading)

        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (T.METRIC["window_width"], T.METRIC["window_height"])),
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setTitle_(APP_NAME)
        window.setMinSize_((T.METRIC["window_width_min"], T.METRIC["window_height_min"]))
        window.setReleasedWhenClosed_(False)
        # Remember where the user put it. Without this the window opens at the
        # content rect's origin — (0, 0), which in Cocoa is the *bottom* left
        # corner of the screen, so it appears tucked into the corner.
        window.setFrameAutosaveName_("AloudMainWindow")
        window.setBackgroundColor_(T.ns_color(T.BG_WINDOW))
        window.setTitlebarAppearsTransparent_(False)

        surface = C.drop_surface(
            handler=self.accept_drop,
            accepts=media.is_supported,
            content=root,
        )
        host = AppKit.NSView.alloc().init()
        window.setContentView_(host)
        host.addSubview_(surface)
        root = surface
        AppKit.NSLayoutConstraint.activateConstraints_([
            root.leadingAnchor().constraintEqualToAnchor_(host.leadingAnchor()),
            root.trailingAnchor().constraintEqualToAnchor_(host.trailingAnchor()),
            root.topAnchor().constraintEqualToAnchor_(host.topAnchor()),
            root.bottomAnchor().constraintEqualToAnchor_(host.bottomAnchor()),
        ])
        for child in (transport, padded_body):
            child.widthAnchor().constraintEqualToAnchor_(root.widthAnchor()).setActive_(True)
        for child in (chrome, self.pane_host):
            child.widthAnchor().constraintEqualToAnchor_(body.widthAnchor()).setActive_(True)
        return window

    # -- panes -------------------------------------------------------------

    def accept_drop(self, path) -> None:
        """A file was dropped anywhere on the window."""
        self.show_pane(TRANSCRIBE)
        self.transcribe.select(path)

    def show_pane(self, index: int) -> None:
        self._pane = index
        self.segments.setSelectedSegment_(index)
        C.clear(self.pane_host)
        pane = {HISTORY: self.history, DICTIONARY: self.dictionary,
                TRANSCRIBE: self.transcribe}[index]
        pane.reload()
        self.pane_host.addArrangedSubview_(pane.view)
        pane.view.widthAnchor().constraintEqualToAnchor_(
            self.pane_host.widthAnchor()
        ).setActive_(True)

    # -- observer callbacks (already marshalled to the main thread) --------

    def set_state(self, state: State) -> None:
        self.state_pill.setStringValue_(state.value)
        self.state_pill.setTextColor_(T.ns_color(STATE_COLOURS[state]))
        self.record_button.setTitle_(
            "Stop Dictation" if state is State.RECORDING else "Start Dictation"
        )
        self.record_button.setEnabled_(state is not State.TRANSCRIBING)

        if state is State.RECORDING:
            self.meter.start()
            self._start_readout()
        else:
            self.meter.stop()
            self._stop_readout()

    def on_result(self) -> None:
        if self._pane == HISTORY:
            self.history.reload()

    def on_dictionary_changed(self) -> None:
        if self._pane == DICTIONARY:
            self.dictionary.render()

    def show_transcribe(self) -> None:
        self.show()
        self.show_pane(TRANSCRIBE)

    def refresh_hotkey(self) -> None:
        mode = str(self.controller.config.get("hotkey.mode", "hold"))
        key = str(self.controller.config.get("hotkey.key", "right_option"))
        verb = "Hold" if mode == "hold" else "Tap"
        self.hotkey_hint.setStringValue_(f"{verb} {describe(key)} anywhere to dictate")

    # -- the numeric readout beside the meter ------------------------------

    def _start_readout(self) -> None:
        if getattr(self, "_readout_timer", None) is not None:
            return
        target = C.action(lambda _sender: self.meter_readout.setStringValue_(
            db_label(self.controller.level)
        ))
        self._keeper.append(target)
        self._readout_timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / T.METER_REFRESH_HZ, target, b"invoke:", None, True
        )
        Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._readout_timer, Foundation.NSRunLoopCommonModes
        )

    def _stop_readout(self) -> None:
        timer = getattr(self, "_readout_timer", None)
        if timer is not None:
            timer.invalidate()
            self._readout_timer = None
        self.meter_readout.setStringValue_("—")

    # -- window ------------------------------------------------------------

    def show(self) -> None:
        if not self._has_been_placed:
            # setFrameUsingName returns False when there is no saved frame,
            # which is the first launch — centre it then, and only then.
            if not self.window.setFrameUsingName_("AloudMainWindow"):
                self.window.center()
            self._has_been_placed = True
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if self._pane == HISTORY:
            self.history.reload()
        else:
            self.dictionary.reload()

    def _closed(self) -> None:
        # Closing the window must not stop dictation; the hotkey still works.
        self._stop_readout()
        self.meter.stop()
