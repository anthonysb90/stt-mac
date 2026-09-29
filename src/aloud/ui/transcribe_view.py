"""The Transcribe pane: drop a file in, look at it, then commit to it.

The looking is the point. A two-hour recording and a two-minute one are
indistinguishable in a file picker, and on a CPU-only Mac only one of them is
worth starting. Showing the length, size and format before the Transcribe
button means the decision is informed rather than a surprise ten minutes later.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

import AppKit

from .. import media
from ..mainthread import run_on_main
from . import components as C
from . import tokens as T


class TranscribeView:
    """Drop zone, file details, and the button that starts the work."""

    def __init__(self, on_transcribe: Callable[[Path], None], on_batch: Callable = None,
                 on_open: Callable = None, on_cancel_job: Callable = None,
                 on_open_unsaved: Callable = None) -> None:
        self._on_transcribe = on_transcribe
        self._on_batch = on_batch
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
        if on_batch is not None:
            self.zone.set_many_handler(on_batch)
        from .queue_view import QueueView

        self.queue = QueueView(on_open=on_open or (lambda _r: None),
                               on_cancel=on_cancel_job or (lambda _j: None),
                               on_open_unsaved=on_open_unsaved)

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
            [self.zone, self.details, self.actions, self.queue.view], spacing=T.SPACE["xl"]
        )
        self.view.setAlignment_(AppKit.NSLayoutAttributeLeading)
        for child in (self.zone, self.details, self.actions, self.queue.view):
            child.widthAnchor().constraintEqualToAnchor_(self.view.widthAnchor()).setActive_(True)

        self.clear()

    # -- the drop zone -----------------------------------------------------

    def _zone_content(self) -> AppKit.NSView:
        self.zone_headline = C.label(
            "Drop an audio or video file here", T.TYPE_TITLE_3, T.TEXT_SECONDARY, align="center"
        )
        self.zone_detail = C.label(
            "mp3, m4a, wav, flac, or the audio from a video — several at once is fine",
            T.TYPE_CAPTION, T.TEXT_TERTIARY, align="center",
        )
        choose = C.button("Choose File…", lambda _s: self.choose(), self._keeper)
        stack = C.stack(
            [self.zone_headline, self.zone_detail, choose], spacing=T.SPACE["md"],
            alignment=AppKit.NSLayoutAttributeCenterX,
        )
        return stack

    def choose(self) -> None:
        from .transcript_window import open_files_panel

        paths = open_files_panel(multiple=self._on_batch is not None)
        if len(paths) > 1:
            self._on_batch(paths)
        elif paths:
            self.select(paths[0])

    # -- selection ---------------------------------------------------------

    def select(self, path: Path) -> None:
        """Show the file at once, and its details as soon as they are read.

        ffprobe runs on a background thread. It used to run right here, on the
        main thread, with a 20-second timeout -- so a file on a slow network
        drive froze every window, and did it again on every visit to this pane.
        """
        path = Path(path)
        self._token = token = object()
        # Forget the previous file now: a reload() during the probe would
        # otherwise re-select it and throw this one away.
        self.selection = None
        C.clear(self.details)
        self.zone_headline.setStringValue_(path.name)
        self.zone_detail.setStringValue_("Reading the file…")
        self.transcribe_button.setEnabled_(False)
        self.clear_button.setHidden_(False)

        def probe() -> None:
            try:
                info = media.inspect(path)
            except Exception:  # e.g. ffprobe answering "N/A" for a rate
                info = media.FileInfo(path=path, container=path.suffix.lower().lstrip("."),
                                      supported=media.is_supported(path))
                try:
                    info.size_bytes = path.stat().st_size
                    info.modified = path.stat().st_mtime
                except OSError:
                    pass
            run_on_main(lambda: self._show_info(info, token))

        threading.Thread(target=probe, name="aloud-probe", daemon=True).start()

    def _show_info(self, info: media.FileInfo, token) -> None:
        if token is not getattr(self, "_token", None):
            return  # another file was chosen while this one was being read
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
        self._token = None
        self.zone_headline.setStringValue_("Drop an audio or video file here")
        self.zone_detail.setStringValue_(
            "mp3, m4a, wav, flac, or the audio from a video — several at once is fine")
        self.transcribe_button.setEnabled_(False)
        self.clear_button.setHidden_(True)
        C.clear(self.details)

    def start(self) -> None:
        if self.selection is None or not self.selection.supported:
            return
        path = self.selection.path
        # Cleared straight away: the work has its own window now, and a second
        # click on a still-armed button would transcribe the same file twice.
        self.clear()
        self._on_transcribe(path)

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
        """Re-read the selected file, but only if it changed on disk."""
        if self.selection is None:
            return
        try:
            changed = self.selection.path.stat().st_mtime != self.selection.modified
        except OSError:
            changed = True
        if changed:
            self.select(self.selection.path)
