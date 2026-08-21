"""Past dictations: searchable, copyable, and honest about what was corrected.

The correction display is the point of this view. A dictionary you cannot
audit is a dictionary you stop trusting, so each row says what fired and what
it changed, and washes the changed spans in the transcript itself.

Highlight spans are found by locating each replacement in the delivered text
rather than by carrying offsets through. Offsets recorded during the correction
pass refer to the raw transcript, and the post-processing that runs afterwards
moves them; searching for the replacement is approximate in the rare case where
the same phrase appears twice, and correct the rest of the time.
"""

from __future__ import annotations

from typing import Any, Dict, List

import AppKit

from .. import history
from . import components as C
from . import tokens as T
from .formatting import describe_when, highlight_spans, summarise

#: More than this and the list stops being something you scan.
VISIBLE_LIMIT = 200


class HistoryView:
    """Builds and refreshes the history pane."""

    def __init__(self, on_copy=None) -> None:
        self._keeper: list = []
        self._entries: List[Dict[str, Any]] = []
        self._query = ""
        self._on_copy = on_copy

        self.search = C.search_field("Search dictations", self._search_changed, self._keeper)
        self.count_label = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY)
        self.list_stack = C.stack([], spacing=T.SPACE["lg"])
        self.list_stack.setAlignment_(AppKit.NSLayoutAttributeLeading)

        scroll = C.scroller(self.list_stack)
        header = C.stack(
            [self.search, self.count_label],
            vertical=False, spacing=T.SPACE["lg"],
        )
        self.search.setContentHuggingPriority_forOrientation_(
            1, AppKit.NSLayoutConstraintOrientationHorizontal
        )

        self.view = C.stack([header, scroll], spacing=T.SPACE["xl"])
        self.view.setAlignment_(AppKit.NSLayoutAttributeLeading)
        scroll.leadingAnchor().constraintEqualToAnchor_(self.view.leadingAnchor()).setActive_(True)
        scroll.trailingAnchor().constraintEqualToAnchor_(self.view.trailingAnchor()).setActive_(True)
        header.leadingAnchor().constraintEqualToAnchor_(self.view.leadingAnchor()).setActive_(True)
        header.trailingAnchor().constraintEqualToAnchor_(self.view.trailingAnchor()).setActive_(True)

    # -- data --------------------------------------------------------------

    def reload(self) -> None:
        self._entries = history.recent(VISIBLE_LIMIT)
        self.render()

    def _search_changed(self, sender) -> None:
        self._query = sender.stringValue().strip().lower()
        self.render()

    def _matching(self) -> List[Dict[str, Any]]:
        if not self._query:
            return self._entries
        return [
            entry for entry in self._entries
            if self._query in str(entry.get("text", "")).lower()
            or self._query in str(entry.get("raw", "")).lower()
        ]

    # -- rendering ---------------------------------------------------------

    def render(self) -> None:
        C.clear(self.list_stack)
        rows = self._matching()

        total = len(self._entries)
        if self._query:
            self.count_label.setStringValue_(f"{len(rows)} of {total}")
        else:
            self.count_label.setStringValue_(f"{total} dictation{'' if total == 1 else 's'}")

        if not rows:
            self.list_stack.addArrangedSubview_(
                C.pad(self._empty_state(), T.INSET["card"])
            )
            return

        for entry in rows:
            self.list_stack.addArrangedSubview_(self._row(entry))
            self.list_stack.arrangedSubviews()[-1].widthAnchor().constraintEqualToAnchor_(
                self.list_stack.widthAnchor()
            ).setActive_(True)

    def _empty_state(self):
        if self._query:
            return C.empty_state("No matches", f"Nothing in your history contains “{self._query}”.")
        return C.empty_state(
            "Nothing dictated yet",
            "Hold your hotkey anywhere, speak, and release. What you said lands "
            "in whichever app has focus, and shows up here.",
        )

    def _row(self, entry: Dict[str, Any]) -> AppKit.NSView:
        text = str(entry.get("text", ""))
        corrections = entry.get("corrections") or []

        meta_bits = [describe_when(str(entry.get("at", "")))]
        engine = str(entry.get("engine", ""))
        if engine:
            meta_bits.append(engine)
        seconds = entry.get("seconds")
        if isinstance(seconds, (int, float)):
            meta_bits.append(f"{seconds:.1f}s")
        meta = C.label(" · ".join(meta_bits), T.TYPE_CAPTION, T.TEXT_TERTIARY)

        copy_button = C.icon_button(
            "doc.on.doc", "Copy this dictation",
            lambda _sender, value=text: self._copy(value), self._keeper,
        )

        header = C.stack([meta, C.spacer(), copy_button], vertical=False, spacing=T.SPACE["md"])

        body: List[AppKit.NSView] = [header]
        if corrections:
            body.append(C.highlighted_text(text, highlight_spans(text, corrections)))
            badge = C.pill(summarise(corrections), T.STATUS_WARNING)
            body.append(C.stack([badge, C.spacer()], vertical=False, spacing=T.SPACE["md"]))
        else:
            body.append(C.selectable_text(text))

        content = C.stack(body, spacing=T.SPACE["md"])
        content.setAlignment_(AppKit.NSLayoutAttributeLeading)

        row = C.card(shadow=T.SHADOW["raised"])
        C.pad(content, T.INSET["card"], container=row)
        header.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)
        return row

    def _copy(self, text: str) -> None:
        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)
        if self._on_copy is not None:
            self._on_copy(text)
