"""The Transcripts pane: every file you have transcribed, searchable.

Each row is one saved transcript — its title, when it was made, how long the
recording was, which engine did it — and, while searching, the words around
the first match, so you can tell which sermon mentioned what without opening
all of them. Open brings back the full transcript window, with its layouts,
speakers and exports.

Search looks through titles, the full text and speaker names at once, and
every word you type has to appear somewhere (see :func:`aloud.library.search`).
"""

from __future__ import annotations

import time
from typing import Callable, List

import AppKit
import Foundation

from .. import library
from ..export import clock
from . import components as C
from . import tokens as T

#: More than this and the list stops being something you scan; search instead.
VISIBLE_LIMIT = 200


class LibraryView:
    def __init__(self, on_open: Callable[[library.Record], None]) -> None:
        self._on_open = on_open
        self._keeper: list = []
        self._index = library.Index()
        self._records: List[library.Record] = []
        self._query = ""
        self._row_keeper: list = []

        self.search = C.search_field("Search all transcripts", self._search_changed, self._keeper)
        self.count_label = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY)
        reveal = C.button("Open Folder", lambda _s: self._open_folder(), self._keeper)
        self.list_stack = C.stack([], spacing=T.SPACE["lg"])

        scroll = C.scroller(self.list_stack)
        header = C.stack([self.search, self.count_label, reveal],
                         vertical=False, spacing=T.SPACE["lg"])
        self.search.setContentHuggingPriority_forOrientation_(
            1, AppKit.NSLayoutConstraintOrientationHorizontal)

        self.view = C.stack([header, scroll], spacing=T.SPACE["xl"])
        for child in (header, scroll):
            child.leadingAnchor().constraintEqualToAnchor_(self.view.leadingAnchor()).setActive_(True)
            child.trailingAnchor().constraintEqualToAnchor_(self.view.trailingAnchor()).setActive_(True)

    # -- data --------------------------------------------------------------

    def reload(self) -> None:
        self._records = self._index.records()
        self.render()

    def _search_changed(self, sender) -> None:
        self._query = str(sender.stringValue()).strip()
        self.render()

    # -- rendering ---------------------------------------------------------

    def render(self) -> None:
        C.clear(self.list_stack)
        # Row buttons' targets live as long as the rows do. Replaced, not
        # appended to: the list re-renders on every keystroke in the search.
        self._row_keeper = []
        hits = library.search(self._query, self._records)
        total = len(self._records)
        if self._query:
            self.count_label.setStringValue_(f"{len(hits)} of {total}")
        else:
            self.count_label.setStringValue_(f"{total} transcript{'' if total == 1 else 's'}")

        if not hits:
            self.list_stack.addArrangedSubview_(C.pad(self._empty_state(), T.INSET["card"]))
            return
        for hit in hits[:VISIBLE_LIMIT]:
            row = self._row(hit)
            self.list_stack.addArrangedSubview_(row)
            row.widthAnchor().constraintEqualToAnchor_(self.list_stack.widthAnchor()).setActive_(True)

    def _empty_state(self):
        if self._query:
            return C.empty_state("No matches", f"No transcript contains “{self._query}”.")
        return C.empty_state(
            "No transcripts yet",
            "Drop an audio or video file on this window, or press ⌘O. Every file "
            "you transcribe is kept here, with its timings and speakers, so you "
            "can search it, reopen it and export it again later.",
        )

    def _row(self, hit: library.Hit) -> AppKit.NSView:
        record = hit.record
        meta_bits = [time.strftime("%b %-d, %Y at %H:%M", time.localtime(record.created_at))]
        if record.audio_seconds:
            meta_bits.append(clock(record.audio_seconds))
        if record.engine_label or record.engine:
            meta_bits.append(record.engine_label or record.engine)
        speakers = len(record.speaker_labels)
        if speakers:
            meta_bits.append(f"{speakers} speaker{'' if speakers == 1 else 's'}")
        if self._query and hit.count:
            meta_bits.append(f"{hit.count} match{'' if hit.count == 1 else 'es'}")

        keeper = self._row_keeper
        open_button = C.button("Open", lambda _s, r=record: self._on_open(r), keeper)
        reveal = C.icon_button("folder", "Show in Finder",
                               lambda _s, r=record: _reveal(r), keeper)
        trash = C.icon_button("trash", "Move to Trash",
                              lambda _s, r=record: self._trash(r), keeper)
        header = C.stack([C.label(record.title, T.TYPE_TITLE_3), C.spacer(), reveal, trash,
                          open_button], vertical=False, spacing=T.SPACE["md"])
        meta = C.label(" · ".join(meta_bits), T.TYPE_CAPTION, T.TEXT_TERTIARY)
        body = C.label(hit.snippet, T.TYPE_CALLOUT, T.TEXT_SECONDARY, wraps=True)

        parts = [header, meta, body]
        content = C.stack(parts, spacing=T.SPACE["sm"])
        for part in parts:
            part.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        row = C.card(shadow=T.SHADOW["raised"])
        C.pad(content, T.INSET["card"], container=row)
        return row

    # -- actions -----------------------------------------------------------

    def _trash(self, record: library.Record) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(f"Move “{record.title}” to the Trash?")
        alert.setInformativeText_(
            "The transcript and its saved details go to the Trash, where you can "
            "still recover them. The original recording is not touched.")
        alert.addButtonWithTitle_("Move to Trash")
        alert.addButtonWithTitle_("Cancel")
        if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
            return
        if not library.delete(record, trash=_move_to_trash):
            failed = AppKit.NSAlert.alloc().init()
            failed.setMessageText_("Could not move the transcript to the Trash")
            failed.setInformativeText_(str(record.folder))
            failed.runModal()
        self.reload()

    def _open_folder(self) -> None:
        import subprocess

        library.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(["open", str(library.LIBRARY_DIR)], check=False)


def _reveal(record: library.Record) -> None:
    import subprocess

    if record.folder is not None:
        subprocess.run(["open", "-R", str(record.folder / library.DATA_FILE)], check=False)


def _move_to_trash(path) -> bool:
    """The Finder's Trash, so a mistaken delete can be put back."""
    url = Foundation.NSURL.fileURLWithPath_(str(path))
    ok, _result, _error = Foundation.NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
        url, None, None)
    return bool(ok)
