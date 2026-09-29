"""The queue: files transcribing in the background, several at a time.

A single file gets its own window to watch. Several files chosen together —
and every file a watch folder picks up — would be a pile of windows, so they
are listed here instead, one row each: waiting, converting, transcribing (with
how far along), done (with Open), or what went wrong. Finished transcripts are
in the Transcripts pane as well, so clearing a row loses nothing.
"""

from __future__ import annotations

from typing import Callable, Dict

import AppKit

from . import components as C
from . import tokens as T

ORIGIN_NOTES = {"batch": "", "watch": "from a watch folder"}


class QueueView:
    def __init__(self, on_open: Callable, on_cancel: Callable,
                 on_open_unsaved: Callable = None) -> None:
        self._on_open = on_open
        self._on_open_unsaved = on_open_unsaved
        self._on_cancel = on_cancel
        self._keeper: list = []
        self._rows: Dict[int, dict] = {}

        clear = C.button("Clear Finished", lambda _s: self.clear_finished(), self._keeper)
        self.header = C.stack([C.label("Queue", T.TYPE_TITLE_3), C.spacer(), clear],
                              vertical=False, spacing=T.SPACE["md"])
        self.list = C.stack([], spacing=T.SPACE["md"])
        # Scrolls past a few rows: twenty dropped files used to push the
        # window taller than the screen. As tall as its rows, up to a limit.
        self.scroll = C.scroller(self.list)
        self.list.widthAnchor().constraintEqualToAnchor_(
            self.scroll.widthAnchor()).setActive_(True)
        self.scroll.heightAnchor().constraintLessThanOrEqualToConstant_(
            T.METRIC["queue_height_max"]).setActive_(True)
        fit = self.scroll.heightAnchor().constraintEqualToAnchor_(self.list.heightAnchor())
        fit.setPriority_(AppKit.NSLayoutPriorityDefaultHigh)
        fit.setActive_(True)
        self.view = C.stack([self.header, self.scroll], spacing=T.SPACE["md"])
        for part in (self.header, self.scroll):
            part.widthAnchor().constraintEqualToAnchor_(self.view.widthAnchor()).setActive_(True)
        self.view.setHidden_(True)

    # -- rows ----------------------------------------------------------------

    def add(self, job_id: int, name: str, origin: str = "batch") -> None:
        note = ORIGIN_NOTES.get(origin, "")
        title = C.label(name, T.TYPE_BODY_STRONG)
        status = C.label("Waiting" + (f" · {note}" if note else ""), T.TYPE_CAPTION,
                         T.TEXT_TERTIARY)
        bar = C.progress_bar(indeterminate=False)
        bar.setHidden_(True)
        keeper: list = []  # released with the row, not held for the app's life
        open_button = C.button("Open", lambda _s, j=job_id: self._open(j), keeper)
        open_button.setHidden_(True)
        cancel = C.icon_button("xmark.circle", "Stop this one",
                               lambda _s, j=job_id: self._on_cancel(j), keeper)
        top = C.stack([title, C.spacer(), cancel, open_button], vertical=False,
                      spacing=T.SPACE["md"])
        parts = [top, status, bar]
        content = C.stack(parts, spacing=T.SPACE["xs"])
        for part in parts:
            part.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        card = C.card()
        C.pad(content, T.INSET["row"], container=card)
        self.list.addArrangedSubview_(card)
        card.widthAnchor().constraintEqualToAnchor_(self.list.widthAnchor()).setActive_(True)
        self._rows[job_id] = {"card": card, "status": status, "bar": bar, "open": open_button,
                              "cancel": cancel, "record": None, "done": False, "note": note,
                              "keeper": keeper}
        self.view.setHidden_(False)

    def has(self, job_id: int) -> bool:
        return job_id in self._rows

    def status(self, job_id: int, text: str, fraction=None) -> None:
        row = self._rows.get(job_id)
        if row is None or row["done"]:
            return
        row["status"].setStringValue_(text + (f" · {row['note']}" if row["note"] else ""))
        if fraction is None:
            return
        row["bar"].setHidden_(False)
        row["bar"].setDoubleValue_(max(0.0, min(fraction, 1.0)))

    def done(self, job_id: int, record, detail: str, dictation=None) -> None:
        row = self._finish(job_id, detail, T.STATUS_SUCCESS)
        if row is not None:
            row["record"] = record
            # Kept so Open works even when the library is off or its save
            # failed -- otherwise the transcript would be unreachable.
            row["dictation"] = dictation
            row["open"].setHidden_(record is None and dictation is None)

    def failed(self, job_id: int, message: str) -> None:
        self._finish(job_id, message, T.STATUS_ERROR)

    def _finish(self, job_id: int, text: str, colour):
        row = self._rows.get(job_id)
        if row is None:
            return None
        row["done"] = True
        row["bar"].setHidden_(True)
        row["cancel"].setHidden_(True)
        row["status"].setStringValue_(text)
        row["status"].setTextColor_(T.ns_color(colour))
        return row

    def _open(self, job_id: int) -> None:
        row = self._rows.get(job_id)
        if row is None:
            return
        if row["record"] is not None:
            self._on_open(row["record"])
        elif row.get("dictation") is not None and self._on_open_unsaved is not None:
            self._on_open_unsaved(row["dictation"])

    def clear_finished(self) -> None:
        for job_id in [j for j, row in self._rows.items() if row["done"]]:
            row = self._rows.pop(job_id)
            self.list.removeArrangedSubview_(row["card"])
            row["card"].removeFromSuperview()
        self.view.setHidden_(not self._rows)
