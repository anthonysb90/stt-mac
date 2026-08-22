"""The window a file transcription lives in, start to finish.

One window with two phases rather than two windows, because a progress dialog
that vanishes and is replaced by a result dialog loses your place — and for a
long recording you have been watching that window for several minutes.

While it works: a progress bar, and the words arriving. Watching the transcript
build is the honest progress indicator; a bar alone cannot distinguish slow
from stuck, and on a CPU-only Mac an hour of audio is genuinely slow.

When it finishes: the same text, plus Copy and Save.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import AppKit

from .. import APP_NAME
from . import components as C
from . import tokens as T
from .formatting import clock, summarise


class TranscriptWindow:
    """Progress, then result, for one imported file."""

    def __init__(self, title: str, on_cancel=None) -> None:
        self.title = title
        self._on_cancel = on_cancel
        self._keeper: list = []
        self.dictation = None
        self._finished = False

        self.heading = C.label(title, T.TYPE_TITLE_2)
        self.status = C.label("Preparing…", T.TYPE_CALLOUT, T.TEXT_SECONDARY, wraps=True)
        self.bar = C.progress_bar(indeterminate=True)
        self.body = C.text_view("")
        self.scroll = C.text_scroller(self.body)

        self.cancel_button = C.button("Cancel", lambda _s: self.cancel(), self._keeper)
        self.copy_button = C.button("Copy", lambda _s: self.copy(), self._keeper, prominent=True)
        self.save_button = C.button("Save as Text…", lambda _s: self.save(), self._keeper)
        for finished_only in (self.copy_button, self.save_button):
            finished_only.setHidden_(True)

        self.buttons = C.stack(
            [C.spacer(), self.cancel_button, self.save_button, self.copy_button],
            vertical=False, spacing=T.SPACE["md"],
        )
        self.window = self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> AppKit.NSWindow:
        content = C.stack(
            [self.heading, self.status, self.bar, self.scroll, self.buttons],
            spacing=T.SPACE["lg"],
        )
        content.setAlignment_(AppKit.NSLayoutAttributeLeading)

        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (T.METRIC["window_width_min"], T.METRIC["window_height_min"])),
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setTitle_(f"{self.title} — {APP_NAME}")
        window.setReleasedWhenClosed_(False)
        window.setBackgroundColor_(T.ns_color(T.BG_WINDOW))

        host = AppKit.NSView.alloc().init()
        window.setContentView_(host)
        C.pad(content, T.INSET["window"], container=host)
        for row in (self.heading, self.status, self.bar, self.scroll, self.buttons):
            row.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        self.scroll.setContentHuggingPriority_forOrientation_(
            1, AppKit.NSLayoutConstraintOrientationVertical
        )
        return window

    # -- phase one: working ------------------------------------------------

    def show(self) -> None:
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.bar.startAnimation_(None)

    def begin(self, detail: str = "Transcribing…") -> None:
        self.status.setStringValue_(detail)

    def update(self, text: str, done: float, total: float) -> None:
        """One more segment has arrived."""
        if self._finished:
            return
        self._set_body(text)

        if total > 0:
            fraction = max(0.0, min(done / total, 1.0))
            if self.bar.isIndeterminate():
                self.bar.stopAnimation_(None)
                self.bar.setIndeterminate_(False)
            self.bar.setDoubleValue_(fraction)
            self.status.setStringValue_(
                f"Transcribing — {clock(done)} of {clock(total)}  ·  {int(fraction * 100)}%"
            )
        else:
            self.status.setStringValue_(f"Transcribing — {clock(done)} so far")

    def _set_body(self, text: str) -> None:
        self.body.setString_(text)
        # Follow the text as it arrives, the way a log window does.
        self.body.scrollRangeToVisible_((len(text), 0))

    # -- phase two: finished -----------------------------------------------

    def finish(self, dictation) -> None:
        self.dictation = dictation
        self._finished = True

        self.bar.stopAnimation_(None)
        self.bar.setIndeterminate_(False)
        self.bar.setDoubleValue_(1.0)
        self.bar.setHidden_(True)

        self._set_body(dictation.text)
        self.body.setSelectedRange_((0, 0))

        words = len(dictation.text.split())
        bits = [
            f"{words:,} word{'' if words == 1 else 's'}",
            dictation.engine,
            f"transcribed in {clock(dictation.seconds)}",
        ]
        if dictation.corrections:
            bits.append(summarise(dictation.corrections))
        self.status.setStringValue_(" · ".join(bits))
        self.status.setTextColor_(T.ns_color(T.TEXT_TERTIARY))

        self.cancel_button.setHidden_(True)
        self.copy_button.setHidden_(False)
        self.save_button.setHidden_(False)
        self.copy()

    def fail(self, message: str) -> None:
        self._finished = True
        self.bar.stopAnimation_(None)
        self.bar.setHidden_(True)
        self.status.setStringValue_(message)
        self.status.setTextColor_(T.ns_color(T.STATUS_ERROR))
        self.cancel_button.setTitle_("Close")

    # -- actions -----------------------------------------------------------

    def cancel(self) -> None:
        if self._finished:
            self.window.close()
            return
        self.status.setStringValue_("Stopping…")
        self.cancel_button.setEnabled_(False)
        if self._on_cancel is not None:
            self._on_cancel()

    def cancelled(self) -> None:
        """The worker confirmed it stopped. Keep whatever was transcribed."""
        self._finished = True
        self.bar.stopAnimation_(None)
        self.bar.setHidden_(True)
        self.status.setStringValue_("Stopped. The text below is what was transcribed.")
        self.cancel_button.setTitle_("Close")
        self.cancel_button.setEnabled_(True)
        if self.body.string():
            self.copy_button.setHidden_(False)
            self.save_button.setHidden_(False)

    def copy(self) -> None:
        text = self.dictation.text if self.dictation else str(self.body.string())
        if not text:
            return
        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)

    def save(self) -> None:
        text = self.dictation.text if self.dictation else str(self.body.string())
        panel = AppKit.NSSavePanel.savePanel()
        panel.setNameFieldStringValue_(_suggested_name(self.title))
        panel.setAllowedFileTypes_(["txt"])
        if panel.runModal() != AppKit.NSModalResponseOK:
            return
        url = panel.URL()
        if url is None:
            return
        target = Path(url.path())
        try:
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("Could not save the transcript")
            alert.setInformativeText_(str(exc))
            alert.runModal()
            return
        subprocess.run(["open", "-R", str(target)], check=False)


def _suggested_name(label: str) -> str:
    stem = Path(label).stem if label else "transcript"
    return f"{stem}.txt"


def open_panel() -> Optional[Path]:
    """Ask for an audio or video file. Returns None if the user cancels."""
    from ..media import AUDIO_EXTENSIONS

    panel = AppKit.NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsMultipleSelection_(False)
    panel.setMessage_("Choose an audio or video file to transcribe")
    panel.setPrompt_("Transcribe")
    panel.setAllowedFileTypes_(list(AUDIO_EXTENSIONS))
    if panel.runModal() != AppKit.NSModalResponseOK:
        return None
    urls = panel.URLs()
    if not urls or len(urls) == 0:
        return None
    return Path(urls[0].path())
