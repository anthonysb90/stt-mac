"""Where an imported file's transcript goes.

A dictation is typed straight into whatever you were looking at, which is the
whole point of it. A file is different: you might have handed it an hour of
audio, and there is no sensible app to type that into. So it gets a window —
selectable, copyable, saveable — and the clipboard, which is what you were
going to do with it anyway.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import AppKit

from .. import APP_NAME
from . import components as C
from . import tokens as T
from .formatting import summarise


class TranscriptWindow:
    """A plain, resizable window holding one transcript."""

    def __init__(self, dictation) -> None:
        self.dictation = dictation
        self._keeper: list = []
        self.window = self._build()

    # -- construction ------------------------------------------------------

    def _build(self) -> AppKit.NSWindow:
        title = self.dictation.label or "Transcript"

        words = len(self.dictation.text.split())
        meta_bits = [
            f"{words:,} word{'' if words == 1 else 's'}",
            self.dictation.engine,
            f"{self.dictation.seconds:.1f}s",
        ]
        if self.dictation.corrections:
            meta_bits.append(summarise(self.dictation.corrections))
        meta = C.label(" · ".join(meta_bits), T.TYPE_CAPTION, T.TEXT_TERTIARY, wraps=True)

        scroll = C.text_scroller(C.text_view(self.dictation.text))

        copy_button = C.button("Copy", lambda _s: self.copy(), self._keeper, prominent=True)
        save_button = C.button("Save as Text…", lambda _s: self.save(), self._keeper)
        buttons = C.stack(
            [C.spacer(), save_button, copy_button], vertical=False, spacing=T.SPACE["md"]
        )

        content = C.stack(
            [C.label(title, T.TYPE_TITLE_2), meta, scroll, buttons], spacing=T.SPACE["lg"]
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
        window.setTitle_(f"{title} — {APP_NAME}")
        window.setReleasedWhenClosed_(False)
        window.setBackgroundColor_(T.ns_color(T.BG_WINDOW))

        host = AppKit.NSView.alloc().init()
        window.setContentView_(host)
        C.pad(content, T.INSET["window"], container=host)
        for row in (meta, scroll, buttons):
            row.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        # The transcript should take the slack, not the buttons.
        scroll.setContentHuggingPriority_forOrientation_(
            1, AppKit.NSLayoutConstraintOrientationVertical
        )
        return window

    # -- actions -----------------------------------------------------------

    def show(self) -> None:
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def copy(self) -> None:
        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(self.dictation.text, AppKit.NSPasteboardTypeString)

    def save(self) -> None:
        panel = AppKit.NSSavePanel.savePanel()
        panel.setNameFieldStringValue_(_suggested_name(self.dictation.label))
        panel.setAllowedFileTypes_(["txt"])
        if panel.runModal() != AppKit.NSModalResponseOK:
            return
        url = panel.URL()
        if url is None:
            return
        target = Path(url.path())
        try:
            target.write_text(self.dictation.text, encoding="utf-8")
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
