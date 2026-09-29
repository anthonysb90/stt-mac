"""The window a file transcription lives in, start to finish.

One window with two phases rather than two windows, because a progress dialog
that vanishes and is replaced by a result dialog loses your place — and for a
long recording you have been watching that window for several minutes.

While it works: a progress bar, and the words arriving. Watching the transcript
build is the honest progress indicator; a bar alone cannot distinguish slow
from stuck, and on a CPU-only Mac an hour of audio is genuinely slow.

When it finishes: the same text, plus Copy and Save. Save offers every format
the transcript can support — plain text always, and subtitles (SRT, WebVTT) or
timestamped text when the engine reported where its words fell. The choice is
a popup inside the save panel, so the file name's extension follows it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import AppKit

from .. import APP_NAME, export
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
        self.save_button = C.button("Save…", lambda _s: self.save(), self._keeper)
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
        if not text and done <= 0 and total <= 0:
            # A heartbeat from an engine that cannot measure its progress
            # (AssemblyAI while it polls). Still working; nothing new to show,
            # and "0:00 so far" would read as stuck.
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
            getattr(dictation, "engine_label", "") or dictation.engine,
            f"transcribed in {clock(dictation.seconds)}",
        ]
        speakers = {s.speaker for s in getattr(dictation, "segments", []) if s.speaker}
        if speakers:
            bits.append(f"{len(speakers)} speaker{'' if len(speakers) == 1 else 's'}")
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
        segments = list(getattr(self.dictation, "segments", []) or [])
        formats = export.available(segments)
        chosen = {"format": formats[0]}

        panel = AppKit.NSSavePanel.savePanel()
        panel.setNameFieldStringValue_(_suggested_name(self.title))
        panel.setAllowedFileTypes_([formats[0].extension])
        panel.setExtensionHidden_(False)

        def pick(sender) -> None:
            fmt = formats[sender.indexOfSelectedItem()]
            chosen["format"] = fmt
            panel.setAllowedFileTypes_([fmt.extension])
            stem = Path(str(panel.nameFieldStringValue())).stem or "transcript"
            panel.setNameFieldStringValue_(f"{stem}.{fmt.extension}")

        popup = C.popup([f.label for f in formats], formats[0].label, pick, self._keeper)
        caption = C.label("Format", T.TYPE_BODY, T.TEXT_SECONDARY)
        accessory = C.pad(
            C.stack([caption, popup], vertical=False, spacing=T.SPACE["md"]),
            T.INSET["control"],
        )
        # A save panel lays its accessory out by frame, not by constraints;
        # handed a constraint-only view it can collapse it to nothing.
        accessory.layoutSubtreeIfNeeded()
        accessory.setTranslatesAutoresizingMaskIntoConstraints_(True)
        accessory.setFrameSize_(accessory.fittingSize())
        panel.setAccessoryView_(accessory)
        if len(formats) == 1:
            note = "Subtitles need timings, and this engine did not report any."
            popup.setToolTip_(note)

        if panel.runModal() != AppKit.NSModalResponseOK:
            return
        url = panel.URL()
        if url is None:
            return
        target = Path(url.path())
        try:
            target.write_text(export.render(chosen["format"], text, segments), encoding="utf-8")
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
