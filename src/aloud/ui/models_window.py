"""The Models window: see, download, delete and choose local models.

One list, grouped by engine, one row per model. Each row answers the questions
you have before committing gigabytes: how big it is, which languages it
covers, what it is good for, and whether it is already on this Mac. Engines
this Mac cannot run are still listed, greyed out with the reason, so "why is
there no Parakeet here?" has an answer on screen.

Downloads run on their own threads (see :class:`aloud.models.Download`) and
this window polls them from a main-thread timer while any are active, so the
bars move without a callback ever touching AppKit off the main thread. Closing
the window does not stop a download; reopening it shows where it got to.

Every rule about which button is enabled lives in
:func:`aloud.models.row_view`, where it is tested.
"""

from __future__ import annotations

from typing import Dict, Optional

import AppKit
import Foundation

from .. import APP_NAME, models
from . import components as C
from . import tokens as T

#: How often the bars move while something is downloading. Seconds.
POLL_SECONDS = 0.25

TONE_COLORS = {
    "error": T.STATUS_ERROR,
    "success": T.STATUS_SUCCESS,
    "": T.TEXT_TERTIARY,
}


class ModelsWindow:
    def __init__(self, controller, on_changed=None) -> None:
        self.controller = controller
        self.config = controller.config
        self._on_changed = on_changed
        self._keeper: list = []
        self.window: Optional[AppKit.NSWindow] = None
        #: model key -> the controls in its row
        self._rows: Dict[str, dict] = {}
        #: model key -> its download, kept after it finishes to show errors
        self._jobs: Dict[str, models.Download] = {}
        self._timer = None
        #: downloads whose completion has already been acted on
        self._adopted: set = set()

    # -- presentation ------------------------------------------------------

    def show(self) -> None:
        if self.window is None:
            self._build()
        self.refresh()
        self.window.center()
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)

    def _build(self) -> None:
        intro = C.label(
            "Models run on this Mac, so nothing you transcribe with them leaves "
            "it. Download one, then press Use to make it the model for its "
            "engine. Which engine handles dictation and which handles files is "
            "chosen in Settings.",
            T.TYPE_CALLOUT, T.TEXT_SECONDARY, wraps=True,
        )
        sections = [intro]
        for engine in (models.PARAKEET, models.FASTER_WHISPER, models.WHISPER_CPP):
            sections.append(C.separator())
            sections.append(self._engine_section(engine))

        content = C.stack(sections, spacing=T.INSET["section"])
        for view in content.arrangedSubviews():
            view.widthAnchor().constraintEqualToAnchor_(content.widthAnchor()).setActive_(True)

        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (T.METRIC["models_width"], T.METRIC["models_height"])),
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setTitle_(f"{APP_NAME} Models")
        window.setReleasedWhenClosed_(False)
        window.setContentMinSize_((T.METRIC["models_width_min"], T.METRIC["models_height_min"]))
        window.setFrameAutosaveName_(f"{APP_NAME}ModelsWindow")

        scroller = C.scroller(C.pad(content, T.INSET["window"]))
        host = AppKit.NSView.alloc().init()
        window.setContentView_(host)
        host.addSubview_(scroller)
        AppKit.NSLayoutConstraint.activateConstraints_([
            scroller.leadingAnchor().constraintEqualToAnchor_(host.leadingAnchor()),
            scroller.trailingAnchor().constraintEqualToAnchor_(host.trailingAnchor()),
            scroller.topAnchor().constraintEqualToAnchor_(host.topAnchor()),
            scroller.bottomAnchor().constraintEqualToAnchor_(host.bottomAnchor()),
        ])
        self.window = window

    def _engine_section(self, engine: str) -> AppKit.NSView:
        available, reason = models.engine_available(engine)
        heading = C.label(models.ENGINE_TITLES[engine], T.TYPE_TITLE_2)
        rows = [heading]
        if not available:
            rows.append(C.label(reason, T.TYPE_CALLOUT, T.STATUS_WARNING, wraps=True))
        for model in models.for_engine(engine):
            rows.append(self._row(model, available))

        section = C.stack(rows, spacing=T.SPACE["lg"])
        for view in rows:
            view.widthAnchor().constraintEqualToAnchor_(section.widthAnchor()).setActive_(True)
        return section

    def _row(self, model: models.Model, available: bool) -> AppKit.NSView:
        key = model.key
        title_parts = [C.label(model.title, T.TYPE_TITLE_3)]
        # Advice for *this* Mac: what suits Apple Silicon is wrong on Intel.
        tip = models.advice(model) or ("Recommended" if model.recommended else "")
        if tip:
            title_parts.append(C.pill(tip, T.BRAND_PRIMARY))
        title_parts.append(C.spacer())
        title_line = C.stack(title_parts, vertical=False, spacing=T.SPACE["md"])

        facts = C.label(
            f"{model.size_label} · {model.languages}", T.TYPE_CALLOUT, T.TEXT_SECONDARY
        )
        note = C.label(model.note, T.TYPE_CALLOUT, T.TEXT_TERTIARY, wraps=True)
        status = C.label("", T.TYPE_CAPTION, T.TEXT_TERTIARY, wraps=True)
        bar = C.progress_bar(indeterminate=False)

        delete = C.button("Delete", lambda _s, k=key: self._delete(k), self._keeper,
                          destructive=True)
        use = C.button("Use", lambda _s, k=key: self._use(k), self._keeper)
        primary = C.button("Download", lambda _s, k=key: self._primary(k), self._keeper,
                           prominent=False)
        buttons = C.stack([C.spacer(), delete, use, primary], vertical=False,
                          spacing=T.SPACE["md"])

        parts = [title_line, facts, note, status, bar, buttons]
        row = C.stack(parts, spacing=T.SPACE["sm"])
        for view in parts:
            view.widthAnchor().constraintEqualToAnchor_(row.widthAnchor()).setActive_(True)

        self._rows[key] = {
            "model": model, "available": available, "status": status, "bar": bar,
            "delete": delete, "use": use, "primary": primary,
        }
        return row

    # -- state -------------------------------------------------------------

    def refresh(self) -> None:
        """Bring every row up to date with the disk, the config and downloads."""
        self._adopt_finished_downloads()
        for key, row in self._rows.items():
            model = row["model"]
            view = models.row_view(
                model,
                models.status(model),
                self._jobs.get(key),
                str(self.config.get(f"engines.{model.engine}.model", "") or ""),
                row["available"],
                default=_default_model(model.engine),
            )
            row["status"].setStringValue_(view.status)
            row["status"].setTextColor_(T.ns_color(TONE_COLORS.get(view.tone, T.TEXT_TERTIARY)))
            row["primary"].setTitle_(view.action_title)
            row["primary"].setEnabled_(bool(view.action))
            row["delete"].setEnabled_(view.can_delete)
            row["use"].setEnabled_(view.can_use)
            row["use"].setTitle_("In Use" if view.in_use else "Use")
            row["bar"].setHidden_(view.progress is None)
            if view.progress is not None:
                row["bar"].setDoubleValue_(view.progress)

        if not any(not job.finished for job in self._jobs.values()):
            self._stop_polling()

    def _adopt_finished_downloads(self) -> None:
        """Point the engine at a model it already names, once it is downloaded.

        On a fresh Mac the Parakeet setting already names v3 by its repository
        id. Downloading v3 here and leaving the setting alone would make the
        engine look in the Hugging Face cache, find nothing, and download the
        same 2.5 GB a second time. So a finished download of the model an
        engine is set to use switches that setting to the copy just fetched.
        """
        for key, job in list(self._jobs.items()):
            if not job.finished or job.error or job.cancelled or key in self._adopted:
                continue
            self._adopted.add(key)
            model = job.model
            configured = str(self.config.get(f"engines.{model.engine}.model", "") or "")
            wanted = models.setting_for(model)
            if wanted != configured and models.in_use(model, configured, _default_model(model.engine)):
                self.config.set(f"engines.{model.engine}.model", wanted)
                self.config.save()
                self._after_model_change(model.engine, refresh=False)

    def _start_polling(self) -> None:
        if self._timer is not None:
            return
        target = getattr(self, "_poll_target", None)
        if target is None:  # one target, reused each time polling starts
            target = C.action(lambda _sender: self.refresh())
            self._keeper.append(target)
            self._poll_target = target
        self._timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_SECONDS, target, b"invoke:", None, True
        )
        Foundation.NSRunLoop.currentRunLoop().addTimer_forMode_(
            self._timer, Foundation.NSRunLoopCommonModes
        )

    def _stop_polling(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    # -- actions -----------------------------------------------------------

    def _primary(self, key: str) -> None:
        job = self._jobs.get(key)
        if job is not None and not job.finished:
            job.cancel()
            return
        model = self._rows[key]["model"]
        self._adopted.discard(key)
        self._jobs[key] = models.Download(model).start()
        self._start_polling()
        self.refresh()

    def _delete(self, key: str) -> None:
        model = self._rows[key]["model"]
        state = models.status(model)
        detail = f"This frees {state.label.split('·')[-1].strip()} on this Mac."
        if state.where == "cache":
            detail += (
                " It is in the shared Hugging Face cache, so other apps that "
                "use the same model will download it again."
            )
        if not _confirm(f"Delete {model.title}?", detail, "Delete"):
            return
        models.delete(model)
        self._jobs.pop(key, None)
        self._after_model_change(model.engine)

    def _use(self, key: str) -> None:
        model = self._rows[key]["model"]
        self.config.set(f"engines.{model.engine}.model", models.setting_for(model))
        self.config.save()
        self._after_model_change(model.engine)

    def _after_model_change(self, engine: str, refresh: bool = True) -> None:
        """Reload whichever running engine was affected, and nothing else."""
        if self.controller.engine.name == engine:
            self.controller.use_engine(str(self.config.get("engine", "auto")))
        self.controller.reset_file_engine()
        if refresh:
            self.refresh()
        if self._on_changed is not None:
            self._on_changed()


def _default_model(engine: str) -> str:
    """What the engine uses when its model setting is blank."""
    if engine == models.PARAKEET:
        from ..engines.parakeet_mlx import DEFAULT_MODEL

        return DEFAULT_MODEL
    if engine == models.FASTER_WHISPER:
        from ..engines.faster_whisper import DEFAULT_MODEL

        return DEFAULT_MODEL
    if engine == models.WHISPER_CPP:
        from ..engines.whisper_cpp import find_model

        found = find_model()
        return str(found) if found else ""
    return ""


def _confirm(title: str, message: str, confirm: str) -> bool:
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.addButtonWithTitle_(confirm)
    alert.addButtonWithTitle_("Cancel")
    return alert.runModal() == AppKit.NSAlertFirstButtonReturn
