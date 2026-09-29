"""The window a file transcription lives in: progress, then the transcript.

One window with two phases rather than two windows, because a progress dialog
that vanishes and is replaced by a result dialog loses your place — and for a
long recording you have been watching that window for several minutes.

While it works: a progress bar, and the words arriving. Watching the transcript
build is the honest progress indicator; a bar alone cannot distinguish slow
from stuck, and on a CPU-only Mac an hour of audio is genuinely slow.

When it finishes, it becomes the transcript's own window — the same one the
Transcripts library opens later:

* **The title** is editable, and renames the saved transcript (and its folder).
* **View** picks the layout: plain text, manuscript, timestamps, timestamps and
  speakers, speaker names. What you see is what Copy copies, and what Export
  starts from.
* **Search** highlights every match and steps through them. ⌘F opens the
  standard find bar too.
* **Speakers…** names the voices the engine told apart; every layout and export
  uses the names.
* **Export…** saves any layout as text, Word, PDF, Markdown or HTML, or the
  whole transcript as JSON, CSV or subtitles.
* The text is ordinary selectable text: highlight any part and ⌘C.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import AppKit

from .. import APP_NAME, documents, export, library
from . import components as C
from . import tokens as T
from .formatting import clock, summarise


class TranscriptWindow:
    """Progress, then result, for one imported file or one saved transcript."""

    #: The layout the last window used, so the next one opens the same way.
    last_layout = export.MANUSCRIPT.key

    def __init__(self, title: str, on_cancel=None, on_changed=None) -> None:
        self.title = title
        self._on_cancel = on_cancel
        self._on_changed = on_changed
        self._keeper: list = []
        self.dictation = None
        self.record: Optional[library.Record] = None
        self._finished = False
        self._layouts: List[export.Layout] = [export.PLAIN]
        self._matches: List[Tuple[int, int]] = []
        self._match_index = -1

        self.heading = C.title_field(title, self._rename, self._keeper)
        self.heading.setEditable_(False)
        self.status = C.label("Preparing…", T.TYPE_CALLOUT, T.TEXT_SECONDARY, wraps=True)
        self.warning = C.label("", T.TYPE_CALLOUT, T.STATUS_WARNING, wraps=True)
        self.warning.setHidden_(True)
        self.bar = C.progress_bar(indeterminate=True)
        self.body = C.text_view("")
        self.scroll = C.text_scroller(self.body)

        # -- toolbar: view, search, speakers --------------------------------
        self.layout_popup = C.popup([export.PLAIN.label], export.PLAIN.label,
                                    lambda s: self._pick_layout(s.indexOfSelectedItem()),
                                    self._keeper)
        self.search = C.search_field("Search transcript", lambda s: self._search(s.stringValue()),
                                     self._keeper)
        self.match_label = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY)
        self.previous_match = C.icon_button("chevron.up", "Previous match",
                                            lambda _s: self._step(-1), self._keeper)
        self.next_match = C.icon_button("chevron.down", "Next match",
                                        lambda _s: self._step(1), self._keeper)
        self.speakers_button = C.button("Speakers…", lambda _s: self.edit_speakers(),
                                        self._keeper)
        self.toolbar = C.stack(
            [C.label("View", T.TYPE_BODY, T.TEXT_SECONDARY), self.layout_popup,
             self.search, self.match_label, self.previous_match, self.next_match,
             C.spacer(), self.speakers_button],
            vertical=False, spacing=T.SPACE["md"],
        )
        self.search.widthAnchor().constraintGreaterThanOrEqualToConstant_(
            T.METRIC["search_width_min"]).setActive_(True)
        self.toolbar.setHidden_(True)

        # -- buttons ---------------------------------------------------------
        self.reveal_button = C.button("Show in Finder", lambda _s: self.reveal(), self._keeper)
        self.cancel_button = C.button("Cancel", lambda _s: self.cancel(), self._keeper)
        self.copy_button = C.button("Copy All", lambda _s: self.copy(), self._keeper, prominent=True)
        self.save_button = C.button("Export…", lambda _s: self.save(), self._keeper)
        for finished_only in (self.copy_button, self.save_button, self.reveal_button):
            finished_only.setHidden_(True)

        self.buttons = C.stack(
            [self.reveal_button, C.spacer(), self.cancel_button, self.save_button, self.copy_button],
            vertical=False, spacing=T.SPACE["md"],
        )
        self.window = self._build()

    @classmethod
    def for_record(cls, record: library.Record, on_changed=None) -> "TranscriptWindow":
        """A window for a transcript from the library, opened straight to it."""
        window = cls(record.title, on_changed=on_changed)
        window._show_record(record, summary=_record_summary(record))
        return window

    # -- construction ------------------------------------------------------

    def _build(self) -> AppKit.NSWindow:
        rows = [self.heading, self.status, self.warning, self.bar, self.toolbar,
                self.scroll, self.buttons]
        content = C.stack(rows, spacing=T.SPACE["lg"])
        content.setAlignment_(AppKit.NSLayoutAttributeLeading)

        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (T.METRIC["window_width"], T.METRIC["window_height"])),
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setTitle_(f"{self.title} — {APP_NAME}")
        window.setReleasedWhenClosed_(False)
        window.setContentMinSize_((T.METRIC["window_width_min"], T.METRIC["window_height_min"]))
        window.setBackgroundColor_(T.ns_color(T.BG_WINDOW))

        host = AppKit.NSView.alloc().init()
        window.setContentView_(host)
        C.pad(content, T.INSET["window"], container=host)
        for row in rows:
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
        if not self._finished:
            self.bar.startAnimation_(None)

    @property
    def finished(self) -> bool:
        return self._finished

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
        self._set_body(text, follow=True)

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

    def _set_body(self, text: str, follow: bool = False) -> None:
        self.body.setString_(text)
        if follow:
            # Follow the text as it arrives, the way a log window does.
            self.body.scrollRangeToVisible_((len(text), 0))

    # -- phase two: finished -----------------------------------------------

    def finish(self, dictation) -> None:
        self.dictation = dictation
        record = getattr(dictation, "record", None)
        if record is None:
            # Not saved (library off, or the save failed): an unsaved record
            # still gives the window everything it needs to show and export.
            record = library.Record(
                title=library.title_from(dictation.label or self.title),
                text=dictation.text, raw_text=dictation.raw,
                segments=list(getattr(dictation, "segments", []) or []),
                source_name=dictation.label, engine=dictation.engine,
                engine_label=getattr(dictation, "engine_label", ""),
                transcribe_seconds=dictation.seconds,
            )
        words = len(dictation.text.split())
        bits = [
            f"{words:,} word{'' if words == 1 else 's'}",
            getattr(dictation, "engine_label", "") or dictation.engine,
            f"transcribed in {clock(dictation.seconds)}",
        ]
        if dictation.corrections:
            bits.append(summarise(dictation.corrections))
        self._show_record(record, summary=" · ".join(bits),
                          warning=getattr(dictation, "warning", ""))
        self.copy()

    def _show_record(self, record: library.Record, summary: str, warning: str = "") -> None:
        self.record = record
        self._finished = True
        self.title = record.title
        self.window.setTitle_(f"{record.title} — {APP_NAME}")
        self.heading.setStringValue_(record.title)
        self.heading.setEditable_(True)

        self.bar.stopAnimation_(None)
        self.bar.setHidden_(True)
        speakers = len(record.speaker_labels)
        if speakers:
            summary += f" · {speakers} speaker{'' if speakers == 1 else 's'}"
        self.status.setStringValue_(summary)
        self.status.setTextColor_(T.ns_color(T.TEXT_TERTIARY))
        if warning:
            self.warning.setStringValue_(f"⚠ {warning}")
            self.warning.setHidden_(False)

        doc = self._doc()
        self._layouts = export.available_layouts(doc)
        self.layout_popup.removeAllItems()
        self.layout_popup.addItemsWithTitles_([l.label for l in self._layouts])
        keys = [l.key for l in self._layouts]
        preferred = TranscriptWindow.last_layout if TranscriptWindow.last_layout in keys else keys[-1]
        self.layout_popup.selectItemAtIndex_(keys.index(preferred))
        self.speakers_button.setHidden_(not record.speaker_labels)
        self.toolbar.setHidden_(False)

        self.cancel_button.setHidden_(True)
        self.copy_button.setHidden_(False)
        self.save_button.setHidden_(False)
        self.reveal_button.setHidden_(record.folder is None)
        self._render()
        self.body.setSelectedRange_((0, 0))
        self.body.scrollRangeToVisible_((0, 0))

    def fail(self, message: str) -> None:
        self._finished = True
        self.bar.stopAnimation_(None)
        self.bar.setHidden_(True)
        self.status.setStringValue_(message)
        self.status.setTextColor_(T.ns_color(T.STATUS_ERROR))
        self.cancel_button.setTitle_("Close")
        self.cancel_button.setEnabled_(True)

    # -- the transcript ----------------------------------------------------

    def _doc(self) -> export.Doc:
        if self.record is not None:
            return export.doc_from_record(self.record)
        text = str(self.body.string())
        return export.Doc(self.title, text, [], {}, "")

    def _layout(self) -> export.Layout:
        index = max(0, self.layout_popup.indexOfSelectedItem())
        return self._layouts[min(index, len(self._layouts) - 1)]

    def _pick_layout(self, index: int) -> None:
        if 0 <= index < len(self._layouts):
            TranscriptWindow.last_layout = self._layouts[index].key
        self._render()

    def _render(self) -> None:
        self._set_body(export.as_text(self._layout(), self._doc()))
        self._search(str(self.search.stringValue()))

    # -- search ------------------------------------------------------------

    def _search(self, query: str) -> None:
        text = str(self.body.string())
        manager = self.body.layoutManager()
        everything = (0, len(text))
        manager.removeTemporaryAttribute_forCharacterRange_(
            AppKit.NSBackgroundColorAttributeName, everything)
        self._matches = library.find_all(text, query.strip())
        self._match_index = -1
        if not query.strip():
            self.match_label.setStringValue_("")
            return
        if not self._matches:
            self.match_label.setStringValue_("No matches")
            return
        colour = T.ns_color(T.HIGHLIGHT_CORRECTION)
        for match in self._matches:
            manager.addTemporaryAttribute_value_forCharacterRange_(
                AppKit.NSBackgroundColorAttributeName, colour, match)
        self._step(1)

    def _step(self, direction: int) -> None:
        if not self._matches:
            return
        self._match_index = (self._match_index + direction) % len(self._matches)
        match = self._matches[self._match_index]
        self.body.setSelectedRange_(match)
        self.body.scrollRangeToVisible_(match)
        self.body.showFindIndicatorForRange_(match)
        self.match_label.setStringValue_(f"{self._match_index + 1} of {len(self._matches)}")

    # -- editing the record --------------------------------------------------

    def _rename(self, sender) -> None:
        if self.record is None:
            return
        title = str(sender.stringValue()).strip()
        if not title or title == self.record.title:
            sender.setStringValue_(self.record.title)
            return
        try:
            if self.record.folder is not None:
                library.rename(self.record, title)
            else:
                self.record.title = title
        except OSError as exc:
            _alert("Could not rename the transcript", str(exc))
            sender.setStringValue_(self.record.title)
            return
        self.title = self.record.title
        self.window.setTitle_(f"{self.title} — {APP_NAME}")
        self._changed()

    def edit_speakers(self) -> None:
        """Name the speakers. Every layout and export uses the names."""
        record = self.record
        if record is None or not record.speaker_labels:
            return
        fields = {}
        rows = []
        for label in record.speaker_labels:
            default = library.default_speaker_name(label, record.speaker_labels)
            field = C.text_field(record.speakers.get(label, ""), default)
            fields[label] = field
            rows.append(C.stack([C.label(default, T.TYPE_BODY, T.TEXT_SECONDARY, align="right"),
                                 field], vertical=False, spacing=T.SPACE["md"]))
            rows[-1].arrangedSubviews()[0].widthAnchor().constraintEqualToConstant_(
                T.METRIC["form_label_width"]).setActive_(True)
            field.widthAnchor().constraintGreaterThanOrEqualToConstant_(
                T.METRIC["field_width_min"]).setActive_(True)
        accessory = C.fit_to_content(C.stack(rows, spacing=T.SPACE["md"]))

        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Name the speakers")
        alert.setInformativeText_(
            "Leave a name blank to keep the numbered label. Sample the text to "
            "tell voices apart: the first lines each speaker says are in the "
            "Speaker Names view.")
        alert.setAccessoryView_(accessory)
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
            return
        record.speakers = {
            label: str(field.stringValue()).strip()
            for label, field in fields.items() if str(field.stringValue()).strip()
        }
        if record.folder is not None:
            try:
                library.save(record)
            except OSError as exc:
                _alert("Could not save the speaker names", str(exc))
        self._render()
        self._changed()

    def _changed(self) -> None:
        if self._on_changed is not None:
            self._on_changed()

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
        """Everything in the current view. To copy part, select it and ⌘C."""
        text = str(self.body.string())
        if not text:
            return
        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString)

    def reveal(self) -> None:
        if self.record is not None and self.record.folder is not None:
            subprocess.run(["open", "-R", str(self.record.folder / library.DATA_FILE)],
                           check=False)

    def save(self) -> None:
        """Export: pick a format and a layout, then where to put it."""
        doc = self._doc()
        formats = export.available(doc)
        layouts = export.available_layouts(doc)
        chosen = {"format": formats[0], "layout": self._layout()}
        if chosen["layout"] not in layouts:
            chosen["layout"] = layouts[0]

        panel = AppKit.NSSavePanel.savePanel()
        panel.setNameFieldStringValue_(f"{_safe_stem(doc.title)}.{formats[0].extension}")
        panel.setAllowedFileTypes_([formats[0].extension])
        panel.setExtensionHidden_(False)
        panel.setCanCreateDirectories_(True)

        note = C.label("", T.TYPE_CAPTION, T.STATUS_WARNING, wraps=True)
        layout_popup = C.popup([l.label for l in layouts], chosen["layout"].label,
                               lambda s: chosen.update(layout=layouts[s.indexOfSelectedItem()]),
                               self._keeper)

        def refresh_note() -> None:
            fmt = chosen["format"]
            layout_popup.setEnabled_(fmt.uses_layout)
            if fmt.key == "pdf" and not documents.pdf_can_render(doc.text):
                note.setStringValue_(
                    "This transcript has characters PDF's standard fonts cannot show; "
                    "they will appear as “?”. Export Word or HTML instead, then "
                    "print to PDF from there.")
            elif not fmt.uses_layout:
                note.setStringValue_(f"{fmt.label} has one fixed layout.")
            else:
                note.setStringValue_("")

        def pick_format(sender) -> None:
            fmt = formats[sender.indexOfSelectedItem()]
            chosen["format"] = fmt
            panel.setAllowedFileTypes_([fmt.extension])
            stem = Path(str(panel.nameFieldStringValue())).stem or "transcript"
            panel.setNameFieldStringValue_(f"{stem}.{fmt.extension}")
            refresh_note()

        format_popup = C.popup([f.label for f in formats], formats[0].label, pick_format,
                               self._keeper)
        grid = C.stack([
            C.stack([C.label("Format", T.TYPE_BODY, T.TEXT_SECONDARY), format_popup,
                     C.label("Layout", T.TYPE_BODY, T.TEXT_SECONDARY), layout_popup],
                    vertical=False, spacing=T.SPACE["md"]),
            note,
        ], spacing=T.SPACE["sm"])
        note.widthAnchor().constraintEqualToAnchor_(grid.widthAnchor()).setActive_(True)
        refresh_note()
        panel.setAccessoryView_(C.fit_to_content(C.pad(grid, T.INSET["control"])))

        if panel.runModal() != AppKit.NSModalResponseOK:
            return
        url = panel.URL()
        if url is None:
            return
        target = Path(url.path())
        try:
            target.write_bytes(export.render(chosen["format"], chosen["layout"], doc))
        except (OSError, ValueError) as exc:
            _alert("Could not export the transcript", str(exc))
            return
        subprocess.run(["open", "-R", str(target)], check=False)


def _record_summary(record: library.Record) -> str:
    words = len(record.text.split())
    bits = [f"{words:,} word{'' if words == 1 else 's'}"]
    if record.audio_seconds:
        bits.append(clock(record.audio_seconds))
    if record.engine_label or record.engine:
        bits.append(record.engine_label or record.engine)
    return " · ".join(bits)


def _safe_stem(title: str) -> str:
    stem = "".join("-" if c in '/\\:*?"<>|' else c for c in title).strip(" .")
    return stem or "transcript"


def _alert(title: str, message: str) -> None:
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.runModal()


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
