"""The Transcribe pane: drop a file in, look at it, then commit to it.

The looking is the point. A two-hour recording and a two-minute one are
indistinguishable in a file picker, and on a CPU-only Mac only one of them is
worth starting. Showing the length, size and format before the Transcribe
button means the decision is informed rather than a surprise ten minutes later.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import AppKit

from .. import media
from . import components as C
from . import tokens as T


class TranscribeView:
    """Drop zone, file details, and the button that starts the work."""

    def __init__(self, on_transcribe: Callable[[Path], None]) -> None:
        self._on_transcribe = on_transcribe
        self._keeper: list = []
        self.selection: Optional[media.FileInfo] = None

        self.zone = C.drop_zone(
            handler=self.select,
            accepts=lambda path: media.is_supported(path),
            content=self._zone_content(),
        )
        self.zone.heightAnchor().constraintGreaterThanOrEqualToConstant_(
            T.METRIC["drop_zone_height"]
        ).setActive_(True)

        self.details = C.stack([], spacing=T.SPACE["sm"])
        self.details.setAlignment_(AppKit.NSLayoutAttributeLeading)

        self.transcribe_button = C.button(
            "Transcribe", lambda _s: self.start(), self._keeper, prominent=True
        )
        self.transcribe_button.setEnabled_(False)
        self.clear_button = C.button("Clear", lambda _s: self.clear(), self._keeper)
        self.clear_button.setHidden_(True)

        self.actions = C.stack(
            [C.spacer(), self.clear_button, self.transcribe_button],
            vertical=False, spacing=T.SPACE["md"],
        )

        self.view = C.stack(
            [self.zone, self.details, self.actions], spacing=T.SPACE["xl"]
        )
        self.view.setAlignment_(AppKit.NSLayoutAttributeLeading)
        for child in (self.zone, self.details, self.actions):
            child.widthAnchor().constraintEqualToAnchor_(self.view.widthAnchor()).setActive_(True)

        self.clear()

    # -- the drop zone -----------------------------------------------------

    def _zone_content(self) -> AppKit.NSView:
        self.zone_headline = C.label(
            "Drop an audio or video file here", T.TYPE_TITLE_3, T.TEXT_SECONDARY, align="center"
        )
        self.zone_detail = C.label(
            "mp3, m4a, wav, flac, or the audio from a video",
            T.TYPE_CAPTION, T.TEXT_TERTIARY, align="center",
        )
        choose = C.button("Choose File…", lambda _s: self.choose(), self._keeper)
        stack = C.stack(
            [self.zone_headline, self.zone_detail, choose], spacing=T.SPACE["md"],
            alignment=AppKit.NSLayoutAttributeCenterX,
        )
        return stack

    def choose(self) -> None:
        from .transcript_window import open_panel

        path = open_panel()
        if path is not None:
            self.select(path)

    # -- selection ---------------------------------------------------------

    def select(self, path: Path) -> None:
        info = media.inspect(path)
        self.selection = info
        self.zone_headline.setStringValue_(info.name)
        self.zone_detail.setStringValue_(
            "Drop another file to replace it" if info.supported
            else "Aloud does not recognise this kind of file"
        )
        self.transcribe_button.setEnabled_(bool(info.supported))
        self.clear_button.setHidden_(False)
        self._render_details(info)

    def clear(self) -> None:
        self.selection = None
        self.zone_headline.setStringValue_("Drop an audio or video file here")
        self.zone_detail.setStringValue_("mp3, m4a, wav, flac, or the audio from a video")
        self.transcribe_button.setEnabled_(False)
        self.clear_button.setHidden_(True)
        C.clear(self.details)

    def start(self) -> None:
        if self.selection is None or not self.selection.supported:
            return
        self._on_transcribe(self.selection.path)

    # -- details -----------------------------------------------------------

    def _render_details(self, info: media.FileInfo) -> None:
        C.clear(self.details)
        card = C.card()
        rows = [self._detail_row(label, value) for label, value in info.rows()]
        inner = C.stack(rows, spacing=T.SPACE["sm"])
        inner.setAlignment_(AppKit.NSLayoutAttributeLeading)
        C.pad(inner, T.INSET["card"], container=card)
        for row in rows:
            row.widthAnchor().constraintEqualToAnchor_(inner.widthAnchor()).setActive_(True)
        self.details.addArrangedSubview_(card)
        card.widthAnchor().constraintEqualToAnchor_(self.details.widthAnchor()).setActive_(True)

    def _detail_row(self, label: str, value: str) -> AppKit.NSView:
        caption = C.label(label, T.TYPE_LABEL, T.TEXT_TERTIARY)
        caption.widthAnchor().constraintEqualToConstant_(
            T.METRIC["form_label_width"]
        ).setActive_(True)
        return C.stack(
            [caption, C.label(value, T.TYPE_BODY, T.TEXT_PRIMARY, wraps=True)],
            vertical=False, spacing=T.SPACE["lg"],
            alignment=AppKit.NSLayoutAttributeFirstBaseline,
        )

    # -- called by the window ---------------------------------------------

    def reload(self) -> None:
        """Re-read the selected file, in case it changed on disk."""
        if self.selection is not None:
            self.select(self.selection.path)
