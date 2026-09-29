"""Quick Dictate: a floating window that writes down what you say, as you say it.

Hotkey dictation types into whatever app has focus, which is right when you
know where the words are going and wrong when you do not yet — a thought on
the way to a meeting, a prayer request in the hallway, the first draft of an
email you have not opened. This window is for those. The words stay here,
editable, until you decide what to do with them.

How it behaves:

* **Opens listening.** Tap the hotkey twice from any app (hold mode), or use
  Dictation → Quick Dictate, the menu bar, or the button in the main window.
* **Words appear as you speak.** Finished phrases in full colour; the phrase
  you are still saying in grey, until it settles. See :mod:`aloud.live`.
* **Stopping is instant**, however long you talked: only the last phrase is
  left to transcribe. Press Stop, Esc, or the hotkey.
* **Then it is ordinary text.** Edit it, select and ⌘C, or:
  ⌘↩ pastes it into the app you were in before, **Copy** takes all of it,
  **Save** keeps it in Transcripts. Start again to add more to the end.

It floats above other windows and follows you across Spaces and into full
screen apps, so it is there wherever you were when the thought arrived.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import AppKit
import Foundation
import objc

from .. import APP_NAME, library
from ..mainthread import run_on_main
from . import components as C
from . import tokens as T
from .formatting import clock
from .meter import LevelMeter

#: Seconds between activating the previous app and pasting into it: long
#: enough for it to become frontmost, short enough to feel immediate.
PASTE_DELAY = 0.3


class _Panel(AppKit.NSPanel):
    """A panel that reports Esc, and can take keyboard focus for editing."""

    def canBecomeKeyWindow(self):
        return True

    def cancelOperation_(self, _sender):
        handler = getattr(self, "escape_handler", None)
        if handler is not None:
            handler()


class _Delegate(Foundation.NSObject):
    def initWithHandler_(self, handler):
        self = objc.super(_Delegate, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def windowWillClose_(self, _notification):
        self._handler()


class _TextDelegate(Foundation.NSObject):
    """Lets Esc reach the window while the text has focus.

    NSTextView keeps Esc for itself (it offers word completions), so the
    panel's cancelOperation: never heard it and Esc did nothing.
    """

    def initWithHandler_(self, handler):
        self = objc.super(_TextDelegate, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def textView_doCommandBySelector_(self, _view, selector):
        if selector == b"cancelOperation:" or selector == "cancelOperation:":
            self._handler()
            return True
        return False


class QuickDictateWindow:
    def __init__(self, controller, on_saved=None) -> None:
        self.controller = controller
        self._on_saved = on_saved
        self._keeper: list = []
        self._session = None
        self._result = None
        self._base = ""
        self._previous_app = None
        self._timer = None
        self._timer_target = None
        self._placed = False
        #: Bumped whenever a session starts or is abandoned. Callbacks carry
        #: the value they were made with and are ignored once it moves on, so
        #: a late "finished" from a cancelled or replaced session can neither
        #: refill a cleared window nor detach the session that replaced it.
        self._generation = 0

        self.status = C.label("Ready", T.TYPE_BODY_STRONG, T.STATUS_IDLE)
        self.meter = LevelMeter.alloc().initWithSource_(self._level)
        self.elapsed = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY, align="right")
        self.elapsed.widthAnchor().constraintEqualToConstant_(
            T.METRIC["meter_readout_width"]).setActive_(True)
        self.meter.widthAnchor().constraintGreaterThanOrEqualToConstant_(
            T.METRIC["meter_width_min"]).setActive_(True)
        self.hint = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY, wraps=True)

        self.body = C.text_view("")
        self.body.setEditable_(True)
        self.scroll = C.text_scroller(self.body)

        self.record_button = C.button("Start", lambda _s: self.toggle(), self._keeper)
        self.clear_button = C.button("Clear", lambda _s: self.clear(), self._keeper)
        self.save_button = C.button("Save", lambda _s: self.save(), self._keeper)
        self.copy_button = C.button("Copy", lambda _s: self.copy(), self._keeper)
        self.paste_button = C.button("Paste", lambda _s: self.paste(), self._keeper)
        # ⌘↩, not a bare Return: Return has to stay a new line in the text.
        self.paste_button.setKeyEquivalent_("\r")
        self.paste_button.setKeyEquivalentModifierMask_(AppKit.NSEventModifierFlagCommand)
        self.save_button.setToolTip_("Keep this in Transcripts")

        self._delegate = _Delegate.alloc().initWithHandler_(self._closing)
        self.window = self._build()
        self._text_delegate = _TextDelegate.alloc().initWithHandler_(self._escape)
        self.body.setDelegate_(self._text_delegate)
        # Hooked up after construction: _escape reads self.window.
        self.window.escape_handler = self._escape

    # -- construction ------------------------------------------------------

    def _build(self) -> AppKit.NSPanel:
        top = C.stack([self.status, self.meter, self.elapsed], vertical=False,
                      spacing=T.SPACE["lg"])
        buttons = C.stack(
            [self.record_button, self.clear_button, C.spacer(), self.save_button,
             self.copy_button, self.paste_button],
            vertical=False, spacing=T.SPACE["md"],
        )
        rows = [top, self.scroll, buttons, self.hint]
        content = C.stack(rows, spacing=T.SPACE["lg"])
        for row in rows:
            row.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        self.scroll.setContentHuggingPriority_forOrientation_(
            1, AppKit.NSLayoutConstraintOrientationVertical)

        panel = _Panel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (T.METRIC["quick_width"], T.METRIC["quick_height"])),
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskResizable
            | AppKit.NSWindowStyleMaskUtilityWindow,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        panel.setTitle_(f"Quick Dictate — {APP_NAME}")
        panel.setReleasedWhenClosed_(False)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setBecomesKeyOnlyIfNeeded_(False)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorMoveToActiveSpace
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        panel.setContentMinSize_((T.METRIC["quick_width_min"], T.METRIC["quick_height_min"]))
        panel.setBackgroundColor_(T.ns_color(T.BG_WINDOW))
        panel.setDelegate_(self._delegate)

        host = AppKit.NSView.alloc().init()
        panel.setContentView_(host)
        C.pad(content, T.INSET["card"], container=host)
        return panel

    def _place(self) -> None:
        """Top-centre of the screen the first time; wherever it was left after."""
        if self._placed:
            return
        self._placed = True
        name = f"{APP_NAME}QuickDictate"
        self.window.setFrameAutosaveName_(name)
        if self.window.setFrameUsingName_(name):
            return
        screen = AppKit.NSScreen.mainScreen()
        if screen is None:
            self.window.center()
            return
        visible = screen.visibleFrame()
        size = self.window.frame().size
        x = visible.origin.x + (visible.size.width - size.width) / 2.0
        y = visible.origin.y + visible.size.height - size.height - T.SPACE["6xl"] * 2
        self.window.setFrameOrigin_((x, y))

    # -- showing -----------------------------------------------------------

    def show(self, start: bool = True) -> None:
        """Bring the window up, remembering which app to paste back into."""
        if not self.window.isVisible():
            self._previous_app = _frontmost_other_app()
        self._refresh_paste_title()
        self._place()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._refresh_hint()
        if start and not self.listening:
            self.start()

    @property
    def listening(self) -> bool:
        return self._session is not None and self._session.active

    def _level(self) -> float:
        session = self._session
        return session.level if session is not None else 0.0

    # -- recording ---------------------------------------------------------

    def toggle(self) -> None:
        if self.listening:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        self._base = str(self.body.string()).strip()
        self._result = None
        self._generation += 1
        gen = self._generation

        def current() -> bool:
            return gen == self._generation

        try:
            self._session = self.controller.start_live(
                on_update=lambda committed, preview: run_on_main(
                    lambda: current() and self._show_live(committed, preview)),
                on_finished=lambda result: run_on_main(
                    lambda: current() and self._finished(result)),
                on_error=lambda message: run_on_main(
                    lambda: current() and self._failed(message)),
            )
        except RuntimeError as exc:
            self._failed(str(exc))
            return
        self.body.setEditable_(False)
        self.record_button.setTitle_("Stop")
        self._set_status("● Listening", T.STATUS_RECORDING)
        self.meter.start()
        self._start_clock()

    def stop(self) -> None:
        if not self.listening:
            return
        self._session.stop()
        self.record_button.setEnabled_(False)
        self._set_status("Finishing…", T.TEXT_SECONDARY)
        self.meter.stop()
        self._stop_clock()

    def _show_live(self, committed: str, preview: str) -> None:
        if self._session is None:
            return
        settled = _join(self._base, committed)
        self._set_text(_join(settled, preview), grey_from=len(settled) if preview else None)

    def _detach(self) -> None:
        """Forget the session, and tell the controller -- if it is still ours."""
        session, self._session = self._session, None
        if session is not None and self.controller.live is session:
            self.controller.live = None

    def _finished(self, result) -> None:
        self._result = result
        self._detach()
        self._set_text(_join(self._base, result.text))
        self.body.setEditable_(True)
        self.record_button.setEnabled_(True)
        self.record_button.setTitle_("Continue" if self.body.string() else "Start")
        words = len(str(self.body.string()).split())
        self._set_status(f"Ready · {words:,} word{'' if words == 1 else 's'}", T.STATUS_IDLE)
        self.meter.stop()
        self._stop_clock()
        # Cursor at the end, ready to type a correction or keep going.
        end = self.body.textStorage().length()
        self.window.makeFirstResponder_(self.body)
        self.body.setSelectedRange_((end, 0))

    def _failed(self, message: str) -> None:
        self._detach()
        self.body.setEditable_(True)
        self.record_button.setEnabled_(True)
        self.record_button.setTitle_("Start")
        self.meter.stop()
        self._stop_clock()
        self._set_status("Could not listen", T.STATUS_ERROR)
        self.hint.setStringValue_(message)
        self.hint.setTextColor_(T.ns_color(T.STATUS_ERROR))

    # -- the text ----------------------------------------------------------

    def _set_text(self, text: str, grey_from: Optional[int] = None) -> None:
        """Replace the text; everything from ``grey_from`` on is the preview."""
        self.body.setString_(text)
        storage = self.body.textStorage()
        storage.addAttribute_value_range_(
            AppKit.NSForegroundColorAttributeName, T.ns_color(T.TEXT_PRIMARY),
            (0, storage.length()))
        if grey_from is not None:
            start = library.utf16_length(text[:grey_from])
            storage.addAttribute_value_range_(
                AppKit.NSForegroundColorAttributeName, T.ns_color(T.TEXT_TERTIARY),
                (start, storage.length() - start))
        self.body.scrollRangeToVisible_((storage.length(), 0))

    def _text(self) -> str:
        return str(self.body.string()).strip()

    # -- actions -----------------------------------------------------------

    def clear(self) -> None:
        if self.listening:
            self._session.cancel()
            self._failed_quietly()
        self._base = ""
        self._result = None
        self._set_text("")
        self.record_button.setTitle_("Start")
        self._set_status("Ready", T.STATUS_IDLE)

    def _failed_quietly(self) -> None:
        self._generation += 1  # anything still on its way from it is stale
        self._detach()
        self.body.setEditable_(True)
        self.record_button.setEnabled_(True)
        self.meter.stop()
        self._stop_clock()

    def copy(self) -> None:
        text = self._text()
        if not text:
            return
        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)
        self._set_status("Copied", T.STATUS_SUCCESS)

    def paste(self) -> None:
        """Put the text into the app you were using before this opened."""
        text = self._text()
        if not text or self.listening:
            return
        target = self._previous_app
        if target is None:
            self.copy()
            self.hint.setStringValue_(
                "Copied. There was no other app in front when this opened, so "
                "paste it with ⌘V wherever it is going.")
            return
        from .. import voice

        # Formatted for where it is going: no final period into Messages.
        style = voice.style_for(str(target.bundleIdentifier() or ""),
                                self.controller.config.get("app_styles", {}) or {})
        text = voice.apply_style(text, style)
        self.window.orderOut_(None)
        target.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)

        def deliver() -> None:
            time.sleep(PASTE_DELAY)
            try:
                self.controller.injector.deliver(text)
            except Exception as exc:  # reported, and the text is still here
                run_on_main(lambda: self._paste_failed(str(exc)))

        threading.Thread(target=deliver, name="aloud-quick-paste", daemon=True).start()

    def _paste_failed(self, message: str) -> None:
        self.window.makeKeyAndOrderFront_(None)
        self._set_status("Could not paste", T.STATUS_ERROR)
        self.hint.setStringValue_(f"{message} The text is still here; Copy it instead.")
        self.hint.setTextColor_(T.ns_color(T.STATUS_ERROR))

    def save(self) -> None:
        """Keep the text in Transcripts, titled by how it starts."""
        text = self._text()
        if not text:
            return
        result = self._result
        # Timings only hold while the text is still what was heard: once it
        # has been edited, the segments would contradict it.
        unchanged = result is not None and _join(self._base, result.text) == text \
            and not self._base
        words = text.split()
        title = " ".join(words[:8]) + ("…" if len(words) > 8 else "")
        record = library.Record(
            title=f"Quick note — {title}",
            text=text,
            raw_text=result.raw if unchanged else "",
            segments=list(result.segments) if unchanged else [],
            source_name="Quick Dictate",
            audio_seconds=result.seconds if result is not None else 0.0,
            engine=result.engine if result is not None else "",
            engine_label=result.engine_label if result is not None else "",
        )
        try:
            library.save(record)
        except OSError as exc:
            self._set_status("Could not save", T.STATUS_ERROR)
            self.hint.setStringValue_(str(exc))
            return
        self._set_status("Saved to Transcripts", T.STATUS_SUCCESS)
        if self._on_saved is not None:
            self._on_saved()

    def _escape(self) -> None:
        if self.listening:
            self.stop()
        else:
            self.window.performClose_(None)

    def _closing(self) -> None:
        """Closing while listening throws that recording away."""
        if self.listening:
            self._session.cancel()
            self._failed_quietly()
            self.record_button.setTitle_("Start")
            self._set_status("Ready", T.STATUS_IDLE)

    # -- small helpers -----------------------------------------------------

    def _set_status(self, text: str, colour) -> None:
        self.status.setStringValue_(text)
        self.status.setTextColor_(T.ns_color(colour))

    def _refresh_paste_title(self) -> None:
        app = self._previous_app
        name = str(app.localizedName()) if app is not None and app.localizedName() else ""
        self.paste_button.setTitle_(f"Paste into {name}" if name else "Paste")

    def _refresh_hint(self) -> None:
        engine = self.controller.engine
        where = "uploads your audio" if getattr(engine, "cloud", False) else "on this Mac"
        self.hint.setStringValue_(
            f"{engine.label}, {where}. Esc or the hotkey stops · ⌘↩ pastes · "
            f"tap the hotkey twice to open this from any app.")
        self.hint.setTextColor_(T.ns_color(T.TEXT_TERTIARY))

    def _start_clock(self) -> None:
        if self._timer is not None:
            return
        if self._timer_target is None:
            self._timer_target = C.action(lambda _s: self._tick())
            self._keeper.append(self._timer_target)
        self._tick()
        self._timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            0.5, self._timer_target, b"invoke:", None, True)
        Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._timer, Foundation.NSRunLoopCommonModes)

    def _stop_clock(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def _tick(self) -> None:
        session = self._session
        if session is not None and session.started_at:
            self.elapsed.setStringValue_(clock(time.monotonic() - session.started_at))


def _join(first: str, second: str) -> str:
    first, second = first.strip(), second.strip()
    if not first:
        return second
    if not second:
        return first
    return f"{first} {second}"


def _frontmost_other_app():
    """The app in front before Aloud came forward, or None if that was us."""
    app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if app is None:
        return None
    me = AppKit.NSRunningApplication.currentApplication()
    if me is not None and app.processIdentifier() == me.processIdentifier():
        return None
    return app
