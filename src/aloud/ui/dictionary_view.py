"""The Dictionary pane: teach it words, and correct what it keeps mishearing.

The editor validates as you type rather than on save. An entry that would
rewrite an everyday word is the failure mode that costs the most trust — you
notice it days later, in text you already sent — so the warning has to arrive
before the entry exists, not after.

The file itself stays first-class: an "Open dictionary.json" button sits next to
the add buttons, because editing thirty entries is a text-editor job and
pretending otherwise would make the UI the bottleneck.
"""

from __future__ import annotations

import subprocess
from typing import List, Optional

import AppKit
import Foundation
import objc

from ..corrections import RISK_CAUTION, RISK_OK, RISK_RISKY, analyse
from ..dictionary import CORRECTION, TERM, Dictionary, DictionaryError, Entry
from . import components as C
from . import tokens as T

RISK_COLOURS = {
    RISK_OK: T.STATUS_SUCCESS,
    RISK_CAUTION: T.STATUS_WARNING,
    RISK_RISKY: T.STATUS_ERROR,
}


class _EditorDelegate(Foundation.NSObject):
    """Re-analyses the entry on every keystroke."""

    def initWithHandler_(self, handler):
        self = objc.super(_EditorDelegate, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def controlTextDidChange_(self, _notification):
        self._handler()


class DictionaryView:
    """Builds and refreshes the dictionary pane."""

    def __init__(self, dictionary: Dictionary, on_changed=None) -> None:
        self.dictionary = dictionary
        self._on_changed = on_changed
        self._keeper: list = []
        self._query = ""
        self._editing: Optional[Entry] = None
        self._draft_kind = TERM

        self.search = C.search_field("Search the dictionary", self._search_changed, self._keeper)
        self.count_label = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY)

        add_term = C.button("Add Word", lambda _s: self.begin_add(TERM), self._keeper)
        add_correction = C.button("Add Correction", lambda _s: self.begin_add(CORRECTION), self._keeper)
        open_file = C.button("Open File", lambda _s: self._open_file(), self._keeper)
        open_file.setToolTip_(str(self.dictionary.path))

        controls = C.stack(
            [self.search, self.count_label, C.spacer(), add_term, add_correction, open_file],
            vertical=False, spacing=T.SPACE["md"],
        )
        self.search.widthAnchor().constraintGreaterThanOrEqualToConstant_(T.METRIC["search_width_min"]).setActive_(True)

        self.editor_host = C.stack([], spacing=T.SPACE["md"])
        self.editor_host.setAlignment_(AppKit.NSLayoutAttributeLeading)

        self.list_stack = C.stack([], spacing=T.SPACE["md"])
        self.list_stack.setAlignment_(AppKit.NSLayoutAttributeLeading)
        scroll = C.scroller(self.list_stack)

        self.view = C.stack([controls, self.editor_host, scroll], spacing=T.SPACE["xl"])
        self.view.setAlignment_(AppKit.NSLayoutAttributeLeading)
        for child in (controls, self.editor_host, scroll):
            child.leadingAnchor().constraintEqualToAnchor_(self.view.leadingAnchor()).setActive_(True)
            child.trailingAnchor().constraintEqualToAnchor_(self.view.trailingAnchor()).setActive_(True)

        # Editor field references, populated by _build_editor.
        self._field_a = None
        self._field_b = None
        self._notes = None
        self._warning_host = None
        self._delegate = None

    # -- data --------------------------------------------------------------

    def reload(self) -> None:
        self.dictionary.reload_if_changed()
        self.render()

    def _search_changed(self, sender) -> None:
        self._query = sender.stringValue()
        self.render()

    def _changed(self) -> None:
        self.dictionary.save()
        if self._on_changed is not None:
            self._on_changed()
        self.render()

    def _open_file(self) -> None:
        self.dictionary.save()  # make sure the file exists before opening it
        subprocess.run(["open", "-t", str(self.dictionary.path)], check=False)

    # -- the editor --------------------------------------------------------

    def begin_add(self, kind: str) -> None:
        self._editing = None
        self._draft_kind = kind
        self._build_editor()

    def begin_edit(self, entry: Entry) -> None:
        self._editing = entry
        self._draft_kind = entry.kind
        self._build_editor()

    def cancel_edit(self) -> None:
        self._editing = None
        C.clear(self.editor_host)

    def _build_editor(self) -> None:
        C.clear(self.editor_host)
        entry = self._editing
        is_term = self._draft_kind == TERM

        title = ("Edit" if entry else "New") + (" word" if is_term else " correction")
        heading = C.label(title, T.TYPE_TITLE_3)

        if is_term:
            self._field_a = C.text_field(entry.term if entry else "", "Anthropic")
            self._field_b = None
            fields = C.stack(
                [self._labelled("Word or phrase", self._field_a)],
                vertical=False, spacing=T.SPACE["lg"],
                alignment=AppKit.NSLayoutAttributeTop,
            )
        else:
            self._field_a = C.text_field(entry.heard if entry else "", "cloud code")
            self._field_b = C.text_field(entry.write if entry else "", "Claude Code")
            fields = C.stack(
                [
                    self._labelled("When you hear", self._field_a),
                    self._labelled("Write", self._field_b),
                ],
                vertical=False, spacing=T.SPACE["lg"],
                alignment=AppKit.NSLayoutAttributeTop,
            )

        self._notes = C.text_field(entry.notes if entry else "", "Optional note")
        self._delegate = _EditorDelegate.alloc().initWithHandler_(self._refresh_warning)
        for field in (self._field_a, self._field_b):
            if field is not None:
                field.setDelegate_(self._delegate)
                field.widthAnchor().constraintGreaterThanOrEqualToConstant_(T.METRIC["field_width_min"]).setActive_(True)

        self._warning_host = C.stack([], spacing=T.SPACE["xs"])
        self._warning_host.setAlignment_(AppKit.NSLayoutAttributeLeading)

        save = C.button("Save", lambda _s: self._save(), self._keeper, prominent=True)
        cancel = C.button("Cancel", lambda _s: self.cancel_edit(), self._keeper)
        buttons: List[AppKit.NSView] = [C.spacer(), cancel, save]
        if entry is not None:
            buttons.insert(0, C.button(
                "Delete", lambda _s, e=entry: self._delete(e), self._keeper, destructive=True
            ))
        button_row = C.stack(buttons, vertical=False, spacing=T.SPACE["md"])

        content = C.stack(
            [heading, fields, self._labelled("Note", self._notes),
             self._warning_host, button_row],
            spacing=T.SPACE["lg"],
        )
        content.setAlignment_(AppKit.NSLayoutAttributeLeading)

        panel = C.card(fill=T.BG_RAISED, border=T.BORDER_DEFAULT, shadow=T.SHADOW["overlay"])
        C.pad(content, T.INSET["card"], container=panel)
        self.editor_host.addArrangedSubview_(panel)
        panel.widthAnchor().constraintEqualToAnchor_(self.editor_host.widthAnchor()).setActive_(True)
        for row in (fields, button_row, content):
            row.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)

        self._refresh_warning()
        self._field_a.window() and self._field_a.window().makeFirstResponder_(self._field_a)

    def _labelled(self, caption: str, field: AppKit.NSView) -> AppKit.NSView:
        stack = C.stack(
            [C.label(caption, T.TYPE_LABEL, T.TEXT_TERTIARY), field], spacing=T.SPACE["xs"]
        )
        stack.setAlignment_(AppKit.NSLayoutAttributeLeading)
        field.widthAnchor().constraintEqualToAnchor_(stack.widthAnchor()).setActive_(True)
        return stack

    def _draft(self) -> Optional[Entry]:
        """Build an entry from the fields, or None if it is not valid yet."""
        try:
            if self._draft_kind == TERM:
                return Entry(kind=TERM, term=self._field_a.stringValue(),
                             notes=self._notes.stringValue())
            return Entry(kind=CORRECTION, heard=self._field_a.stringValue(),
                         write=self._field_b.stringValue(), notes=self._notes.stringValue())
        except DictionaryError:
            return None

    def _refresh_warning(self) -> None:
        C.clear(self._warning_host)
        draft = self._draft()
        if draft is None:
            return
        others = [e for e in self.dictionary if self._editing is None or e.id != self._editing.id]
        risk = analyse(draft, others)
        if risk.ok:
            return
        colour = RISK_COLOURS[risk.level]
        for message in risk.messages:
            row = C.label(message, T.TYPE_CALLOUT, colour, wraps=True)
            self._warning_host.addArrangedSubview_(row)
            row.widthAnchor().constraintEqualToAnchor_(
                self._warning_host.widthAnchor()
            ).setActive_(True)

    def _save(self) -> None:
        draft = self._draft()
        if draft is None:
            self._show_error("That entry is incomplete.",
                             "A word needs text; a correction needs both halves.")
            return
        try:
            if self._editing is None:
                self.dictionary.add(draft)
            elif self._draft_kind == TERM:
                self.dictionary.update(self._editing.id, term=draft.term, notes=draft.notes)
            else:
                self.dictionary.update(self._editing.id, heard=draft.heard,
                                       write=draft.write, notes=draft.notes)
        except DictionaryError as exc:
            self._show_error("That entry cannot be saved.", str(exc))
            return
        self.cancel_edit()
        self._changed()

    def _delete(self, entry: Entry) -> None:
        self.dictionary.remove(entry.id)
        self.cancel_edit()
        self._changed()

    def _toggle(self, entry: Entry, sender) -> None:
        self.dictionary.update(entry.id, enabled=sender.state() == AppKit.NSControlStateValueOn)
        self._changed()

    def _show_error(self, title: str, detail: str) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(detail)
        alert.runModal()

    # -- rendering ---------------------------------------------------------

    def render(self) -> None:
        C.clear(self.list_stack)
        entries = self.dictionary.search(self._query)
        total = len(self.dictionary)

        if self._query:
            self.count_label.setStringValue_(f"{len(entries)} of {total}")
        else:
            self.count_label.setStringValue_(f"{total} entr{'y' if total == 1 else 'ies'}")

        if not entries:
            self.list_stack.addArrangedSubview_(C.pad(self._empty_state(), T.INSET["card"]))
            return

        for entry in entries:
            row = self._row(entry)
            self.list_stack.addArrangedSubview_(row)
            row.widthAnchor().constraintEqualToAnchor_(self.list_stack.widthAnchor()).setActive_(True)

    def _empty_state(self):
        if self._query:
            return C.empty_state("No matches", f"Nothing in the dictionary contains “{self._query}”.")
        return C.empty_state(
            "An empty dictionary",
            "Add a word it keeps getting wrong — a name, a product, someone you "
            "work with — or a correction pair for a phrase it always mishears.",
        )

    def _row(self, entry: Entry) -> AppKit.NSView:
        toggle = C.checkbox("", entry.enabled,
                            lambda sender, e=entry: self._toggle(e, sender), self._keeper)
        toggle.setToolTip_("Disable without deleting")

        kind_pill = C.pill("Word" if entry.kind == TERM else "Correction",
                           T.TEXT_TERTIARY)
        title = C.label(entry.label, T.TYPE_BODY_STRONG,
                        T.TEXT_PRIMARY if entry.enabled else T.TEXT_TERTIARY)

        detail_bits = []
        if entry.hits:
            detail_bits.append(f"fired {entry.hits}×")
        if entry.notes:
            detail_bits.append(entry.notes)
        detail = C.label(" · ".join(detail_bits), T.TYPE_CAPTION, T.TEXT_TERTIARY)

        risk = analyse(entry, [e for e in self.dictionary if e.id != entry.id])
        left: List[AppKit.NSView] = [C.stack([kind_pill, title], vertical=False, spacing=T.SPACE["md"])]
        if detail_bits:
            left.append(detail)
        if not risk.ok:
            left.append(C.label(risk.headline, T.TYPE_CAPTION, RISK_COLOURS[risk.level], wraps=True))

        body = C.stack(left, spacing=T.SPACE["xs"])
        body.setAlignment_(AppKit.NSLayoutAttributeLeading)

        edit = C.icon_button("pencil", "Edit this entry",
                             lambda _s, e=entry: self.begin_edit(e), self._keeper)
        delete = C.icon_button("trash", "Delete this entry",
                               lambda _s, e=entry: self._delete(e), self._keeper)

        content = C.stack([toggle, body, C.spacer(), edit, delete],
                          vertical=False, spacing=T.SPACE["lg"],
                          alignment=AppKit.NSLayoutAttributeCenterY)
        row = C.card()
        C.pad(content, T.INSET["row"], container=row)
        return row
