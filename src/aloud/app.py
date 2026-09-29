"""The application: a real Mac app, with a menu bar item as a second surface.

Aloud is a regular ``NSApplication`` — Dock icon, application menu, a window
you can close and reopen — rather than the accessory it started as. The menu
bar item stays, because when you are dictating *into* another app that is the
only surface you can see, but it is no longer the whole interface.

This module is the seam between :class:`aloud.core.DictationController`, which
knows nothing about AppKit, and the views, which know nothing about audio. Its
one real job is threading: the controller emits events from the event tap's run
loop and from the transcription worker, and every one of them is bounced to the
main thread here before a view touches it. Views therefore never have to think
about it.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import threading

import AppKit
import Foundation
import objc

from . import APP_NAME, __version__, learn, toolpath, updates
from .config import Config
from .core import DictationController, State
from .mainthread import run_on_main
from .paths import LOG_FILE
from .ui import app_menu
from .ui import dock
from .ui import transcript_window
from .ui import main_window as main_window_module
from .ui.main_window import MainWindow
from .ui.menu_bar import MenuBarItem
from .ui.models_window import ModelsWindow
from .ui.quick_dictate_window import QuickDictateWindow
from .ui.settings_window import SettingsWindow

log = logging.getLogger(__name__)


class AloudDelegate(Foundation.NSObject):
    """Application delegate, menu target, and controller observer."""

    def initWithConfig_(self, config: Config):
        self = objc.super(AloudDelegate, self).init()
        if self is None:
            return None
        self.config = config
        self.controller = DictationController(config)
        self.main_window = None
        self.settings = None
        self.models_window = None
        self.quick = None
        self.menu_bar = None
        self._transcript_windows = []
        #: job id -> the window watching that file
        self._import_windows = {}
        #: job id -> job, for files in the queue (batches and watch folders)
        self._queued_jobs = {}
        #: job id -> source path, for files found in a watch folder
        self._watch_paths = {}
        self.watcher = None
        return self

    # -- lifecycle ---------------------------------------------------------

    def applicationDidFinishLaunching_(self, _notification):
        """PyObjC swallows exceptions raised in a delegate callback.

        It logs them somewhere you will not look and lets the run loop carry on,
        so a failure here produces an app with no window, no menu bar item and
        no output — indistinguishable from nothing happening at all. Everything
        goes through the launch reporter instead.
        """
        try:
            self._finish_launching()
        except BaseException as exc:  # noqa: BLE001 - reporting it is the point
            from .launch import report

            report(exc)
            AppKit.NSApplication.sharedApplication().terminate_(None)

    @objc.python_method
    def _finish_launching(self):
        app_menu.build({}, self)

        self.main_window = MainWindow(
            self.controller,
            on_settings=self.showSettings_,
            on_transcribe=self._begin_import,
            on_open_record=self._open_record,
            on_quick=lambda: self.quick.show(start=True),
            on_batch=self._begin_batch,
            on_cancel_job=self._cancel_queued,
        )
        self.quick = QuickDictateWindow(self.controller, on_saved=self._library_changed)
        self.models_window = ModelsWindow(self.controller, on_changed=self._models_changed)
        self.settings = SettingsWindow(
            self.controller,
            on_hotkey_changed=self._hotkey_changed,
            on_manage_models=lambda: self.models_window.show(),
            on_watch_changed=self._restart_watcher,
            watcher=lambda: self.watcher,
        )
        self.menu_bar = MenuBarItem(
            self.controller,
            {
                "toggle": self.controller.toggle,
                "open_main": lambda: self.main_window.show(),
                "quick": lambda: self.quick.show(start=True),
                "open_settings": lambda: self.settings.show(),
                "quit": lambda: AppKit.NSApplication.sharedApplication().terminate_(None),
            },
        )
        self._hotkey_changed()

        self.controller.add_observer(self)
        self.controller.start()
        self._restart_watcher()

        # A menu-bar-only app that throws a window up at login is not a
        # menu-bar-only app. With the Dock icon on, the window is the app.
        if bool(self.config.get("interface.dock_icon", True)):
            self.main_window.show()
        self._warn_about_permissions()

    def applicationShouldTerminateAfterLastWindowClosed_(self, _sender):
        # Closing the window must not quit: the hotkey works with no window open.
        return False

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _sender, has_windows):
        if not has_windows:
            self.main_window.show()
        return True

    def applicationWillTerminate_(self, _notification):
        log.info("Quitting")
        self.controller.shutdown()
        if self.watcher is not None:
            self.watcher.stop()
        if self.menu_bar is not None:
            self.menu_bar.remove()

    @objc.python_method
    def _warn_about_permissions(self):
        from . import permissions

        granted, status = permissions.microphone_authorized()
        if status == "not determined":
            permissions.request_microphone()
        elif not granted:
            log.warning("Microphone access is %s", status)

        if not permissions.accessibility_trusted():
            permissions.accessibility_trusted(prompt=True)
            self._alert(
                "Accessibility access required",
                f"{APP_NAME} needs Accessibility access to see the hotkey and to "
                f"paste your text into other apps.\n\n"
                + permissions.accessibility_advice(),
            )
            permissions.open_accessibility_settings()

    # -- controller observer (any thread -> main thread) -------------------
    #
    # Every method below is @objc.python_method. PyObjC turns each method of an
    # NSObject subclass into an Objective-C selector, mapping underscores to
    # colons -- so `on_error(self, title, message)` becomes the one-argument
    # selector `on:error`, and PyObjC rejects the class at definition time with
    # BadPrototypeError. The module then fails to import, which is why this
    # surfaced as a bare "Launch error" with no traceback.
    #
    # Marking them keeps them ordinary Python methods. Only the AppKit
    # callbacks and menu actions below are meant to be selectors.

    @objc.python_method
    def on_accessibility_granted(self) -> None:
        """Rebuild the hotkey now that the grant exists, without a restart."""
        def reinstall() -> None:
            self.controller.install_hotkey()
            self._hotkey_changed()
            log.info("Hotkey reinstalled after Accessibility was granted")

        run_on_main(reinstall)

    @objc.python_method
    def on_quick_dictate_requested(self) -> None:
        """The hotkey was tapped twice: open Quick Dictate, listening."""
        run_on_main(lambda: self.quick.show(start=True))

    @objc.python_method
    def on_state(self, state: State) -> None:
        run_on_main(lambda: self._apply_state(state))

    @objc.python_method
    def _begin_import(self, path) -> None:
        """Open the progress window first, then start the work.

        In that order because preparing the file can itself fail, and a failure
        with nowhere to appear is how you get a silent no-op. Each job gets its
        own window, matched by the job's id: with one shared slot, a second
        file started while the first was running took over the first one's
        window and left it spinning on "Reading the file…" for good.
        """
        holder = {}
        window = self._new_transcript_window(
            path.name, on_cancel=lambda: self.controller.cancel_import(holder.get("job"))
        )
        window.show()
        window.begin("Waiting for the work ahead of it to finish…"
                     if self.controller.state is State.TRANSCRIBING else "Reading the file…")
        job = self.controller.transcribe_file(path)
        if job is None:
            # Refused (recording, or no such file); the reason is in an alert.
            window.window.close()
            self._transcript_windows.remove(window)
            return
        holder["job"] = job
        self._import_windows[job.id] = window

    @objc.python_method
    def _new_transcript_window(self, title, on_cancel=None):
        """Make a transcript window, and let go of the ones already closed."""
        self._prune_windows()
        window = transcript_window.TranscriptWindow(
            title, on_cancel=on_cancel, on_changed=self._library_changed,
            teacher=learn.Teacher(self.controller),
        )
        self._transcript_windows.append(window)
        return window

    @objc.python_method
    def _prune_windows(self) -> None:
        """Let go of transcript windows that are finished and closed.

        Minimised windows are kept: they are off screen but not closed, and
        dropping one freed its button targets, so clicking a button after
        restoring it could crash.
        """
        self._transcript_windows = [
            w for w in self._transcript_windows
            if not w.finished or w.window.isVisible() or w.window.isMiniaturized()
        ]

    @objc.python_method
    def _open_record(self, record) -> None:
        """Open a saved transcript from the Transcripts pane."""
        for existing in self._transcript_windows:
            if existing.record is not None and existing.record.id == record.id:
                existing.window.makeKeyAndOrderFront_(None)
                return
        self._prune_windows()
        window = transcript_window.TranscriptWindow.for_record(
            record, on_changed=self._library_changed, teacher=learn.Teacher(self.controller),
        )
        self._transcript_windows.append(window)
        window.show()

    @objc.python_method
    def _library_changed(self) -> None:
        if self.main_window is not None:
            self.main_window.on_library_changed()

    # Every handler below looks its window up *on the main thread*, inside
    # the deferred block. The worker can start -- and fail -- a job before
    # _begin_import has registered its window (the job is queued first), and
    # a lookup made on the worker thread then found nothing: a stray alert,
    # and a window left on "Reading the file…" for good. _begin_import runs
    # on the main thread too, so by the time a block runs, the window is there.

    @objc.python_method
    def _with_window(self, job, action, pop: bool = False, queued=None) -> None:
        """Send a file's news to its window -- or, with none, to its queue row."""
        if getattr(job, "source", "") != "file":
            return
        job_id = job.id

        def run() -> None:
            windows = self._import_windows
            window = windows.pop(job_id, None) if pop else windows.get(job_id)
            if window is not None:
                action(window)
                return
            queue = self._queue()
            if queued is not None and queue is not None and queue.has(job_id):
                queued(queue, job_id)

        run_on_main(run)

    @objc.python_method
    def _queue(self):
        transcribe = getattr(self.main_window, "transcribe", None)
        return getattr(transcribe, "queue", None)

    @objc.python_method
    def on_job_preparing(self, job) -> None:
        self._with_window(job, lambda w: w.begin("Converting with ffmpeg…"),
                          queued=lambda q, j: q.status(j, "Converting…"))

    @objc.python_method
    def on_job_started(self, job) -> None:
        engine = self.controller.engine_for(job)
        streaming = getattr(engine, "supports_progress", False)
        detail = f"Transcribing with {engine.label}…" if streaming else (
            f"Transcribing with {engine.label} — "
            f"this engine reports no progress until it finishes."
        )
        self._with_window(job, lambda w: w.begin(detail),
                          queued=lambda q, j: q.status(j, f"Transcribing with {engine.label}…",
                                                       0.0 if streaming else None))

    @objc.python_method
    def on_job_stage(self, job, message: str) -> None:
        """A step after transcription, such as identifying speakers."""
        self._with_window(job, lambda w: w.begin(message),
                          queued=lambda q, j: q.status(j, message))

    @objc.python_method
    def on_progress(self, job, text, done, total) -> None:
        from .export import clock

        where = f"{clock(done)} of {clock(total)}" if total > 0 else f"{clock(done)} so far"
        self._with_window(job, lambda w: w.update(text, done, total),
                          queued=lambda q, j: q.status(
                              j, f"Transcribing — {where}", done / total if total > 0 else None))

    @objc.python_method
    def on_cancelled(self, job) -> None:
        self._watch_done(job, False, "stopped")
        self._with_window(job, lambda w: w.cancelled(), pop=True,
                          queued=lambda q, j: q.failed(j, "Stopped"))

    @objc.python_method
    def on_result(self, dictation) -> None:
        if dictation.source == "file":
            self._watch_done_id(dictation.job_id, True)
        run_on_main(lambda: self._deliver_result(dictation))

    @objc.python_method
    def _deliver_result(self, dictation) -> None:
        self.main_window.on_result()
        if dictation.source != "file":
            return
        window = self._import_windows.pop(dictation.job_id, None)
        queue = self._queue()
        if window is None and queue is not None and queue.has(dictation.job_id):
            words = len(dictation.text.split())
            detail = f"Done · {words:,} words"
            if dictation.exported:
                detail += " · saved " + ", ".join(p.name for p in dictation.exported)
            queue.done(dictation.job_id, dictation.record, detail)
            self._queued_jobs.pop(dictation.job_id, None)
            return
        if window is None:
            # Started from somewhere without a window — give it one.
            window = self._new_transcript_window(dictation.label or "Transcript")
            window.show()
        window.finish(dictation)

    @objc.python_method
    def on_empty(self, job) -> None:
        self._watch_done(job, False, "no speech")
        self._with_window(
            job, lambda w: w.fail(f"No speech was recognised in {job.label}."), pop=True,
            queued=lambda q, j: q.failed(j, "No speech was recognised"))

    @objc.python_method
    def on_job_failed(self, job, title: str, message: str) -> None:
        """A file failed: say so in its own window or queue row, not an alert."""
        self._watch_done(job, False, message)
        job_id = job.id

        def run() -> None:
            window = self._import_windows.pop(job_id, None)
            queue = self._queue()
            if window is not None:
                window.fail(f"{title}: {message}")
            elif queue is not None and queue.has(job_id):
                queue.failed(job_id, f"{title}: {message}")
            else:
                self._alert(title, message)

        run_on_main(run)

    # -- batches and watch folders -------------------------------------------

    @objc.python_method
    def _begin_batch(self, paths, origin: str = "batch") -> None:
        """Several files at once: one queue row each, not one window each."""
        from . import media

        self.main_window.show_transcribe()
        for path in paths:
            if not media.is_supported(path):
                continue
            job = self.controller.transcribe_file(path, origin=origin)
            if job is not None:
                self._queued_jobs[job.id] = job
                self.main_window.transcribe.queue.add(job.id, path.name, origin)

    @objc.python_method
    def _cancel_queued(self, job_id) -> None:
        job = self._queued_jobs.get(job_id)
        if job is not None:
            self.controller.cancel_import(job)

    @objc.python_method
    def _queue_watched(self, path, exports):
        """Called by the watcher, on its own thread, for each new recording."""
        job = self.controller.transcribe_file(path, origin="watch", exports=exports)
        if job is None:
            return False
        self._watch_paths[job.id] = path
        self._queued_jobs[job.id] = job
        run_on_main(lambda: self.main_window.transcribe.queue.add(job.id, path.name, "watch"))
        return True

    @objc.python_method
    def _watch_done(self, job, ok: bool, detail: str = "") -> None:
        self._watch_done_id(getattr(job, "id", None), ok, detail)

    @objc.python_method
    def _watch_done_id(self, job_id, ok: bool, detail: str = "") -> None:
        path = self._watch_paths.pop(job_id, None)
        if path is not None and self.watcher is not None:
            self.watcher.finished(path, ok, detail)

    @objc.python_method
    def _restart_watcher(self) -> None:
        """(Re)start watching whatever folders the config names now."""
        from . import watch

        if self.watcher is not None:
            self.watcher.stop()
        self.watcher = watch.Watcher(watch.Settings.from_config(self.config),
                                     self._queue_watched)
        self.watcher.start()

    @objc.python_method
    def on_error(self, title: str, message: str) -> None:
        # Dictation and app-wide problems only; a file's failure arrives as
        # on_job_failed and goes to that file's window.
        run_on_main(lambda: self._alert(title, message))

    @objc.python_method
    def on_dictionary_changed(self) -> None:
        run_on_main(lambda: self.main_window.on_dictionary_changed())

    @objc.python_method
    def _apply_state(self, state: State) -> None:
        if self.menu_bar is not None:
            self.menu_bar.set_state(state)
        if self.main_window is not None:
            self.main_window.set_state(state)

    @objc.python_method
    def _models_changed(self) -> None:
        """A model was chosen or deleted in the Models window."""
        if self.settings is not None and self.settings.window is not None:
            self.settings._refresh_model_section()

    @objc.python_method
    def _hotkey_changed(self) -> None:
        key = str(self.config.get("hotkey.key", "right_option"))
        mode = str(self.config.get("hotkey.mode", "hold"))
        if self.menu_bar is not None:
            self.menu_bar.set_hotkey(key, mode)
        if self.main_window is not None:
            self.main_window.refresh_hotkey()

    # -- menu actions ------------------------------------------------------

    def showMainWindow_(self, _sender):
        self.main_window.show()

    def showSettings_(self, _sender):
        self.settings.show()

    def showQuickDictate_(self, _sender):
        self.quick.show(start=True)

    def showModels_(self, _sender):
        self.models_window.show()

    def showHistory_(self, _sender):
        self.main_window.show()
        self.main_window.show_pane(main_window_module.HISTORY)

    def showTranscripts_(self, _sender):
        self.main_window.show()
        self.main_window.show_pane(main_window_module.TRANSCRIPTS)

    def showDictionary_(self, _sender):
        self.main_window.show()
        self.main_window.show_pane(main_window_module.DICTIONARY)

    def transcribeFile_(self, _sender):
        """File > Transcribe Audio Files… — one to look at first, or several to queue."""
        self.main_window.show_transcribe()
        paths = transcript_window.open_files_panel(multiple=True)
        if len(paths) > 1:
            self._begin_batch(paths)
        elif paths:
            self.main_window.transcribe.select(paths[0])

    def startDictation_(self, _sender):
        self.controller.begin_recording()

    def stopDictation_(self, _sender):
        self.controller.finish_recording()

    def cancelDictation_(self, _sender):
        self.controller.cancel_recording()

    def copyLast_(self, _sender):
        last = self.controller.last
        if last is None:
            return
        pasteboard = AppKit.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(last.text, AppKit.NSPasteboardTypeString)

    def openDictionaryFile_(self, _sender):
        self.controller.dictionary.save()
        subprocess.run(["open", "-t", str(self.controller.dictionary.path)], check=False)

    def openLog_(self, _sender):
        subprocess.run(["open", "-t", str(LOG_FILE)], check=False)

    def checkForUpdates_(self, _sender):
        """Aloud > Check for Updates… — asks the remote, then offers to apply."""
        self._run_off_main(self._check_for_updates)

    @objc.python_method
    def _check_for_updates(self) -> None:
        status = updates.check()
        run_on_main(lambda: self._present_update(status))

    @objc.python_method
    def _present_update(self, status) -> None:
        if not status.checked and not status.is_git:
            if self._confirm("Connect Aloud to its repository?", status.detail,
                             confirm="Connect"):
                ok, detail = updates.adopt()
                self._alert("Connected" if ok else "Could not connect", detail)
                if ok:
                    self._run_off_main(self._check_for_updates)
            return

        if not status.checked:
            self._alert("Could not check for updates", status.detail)
            return

        if not status.available:
            self._alert(status.headline, f"You are on {status.current}.")
            return

        detail = f"You are on {status.current}; {status.latest} is available."
        if status.summary:
            detail += "\n\n" + "\n".join(f"• {line}" for line in status.summary)
        if status.dirty:
            detail += (
                "\n\nThere are uncommitted changes in the Aloud folder, so "
                "updating would overwrite them. Nothing will be changed."
            )
            self._alert(status.headline, detail)
            return

        if not self._confirm(status.headline, detail, confirm="Update"):
            return

        ok, message = updates.apply()
        if not ok:
            self._alert("Update failed", message)
            return

        rebuild = updates.needs_rebuild(status)
        note = message + (
            "\n\nThis update changed the build, so run `make install` in the "
            "Aloud folder afterwards." if rebuild else ""
        )
        if self._confirm("Updated", note + "\n\nRestart Aloud now?", confirm="Restart"):
            self._restart()

    @objc.python_method
    def _restart(self) -> None:
        bundle = AppKit.NSBundle.mainBundle().bundlePath()
        if bundle and bundle.endswith(".app"):
            # A beat, so this process is gone before the new one starts.
            subprocess.Popen(["/bin/sh", "-c", f"sleep 1; open -n {shlex.quote(bundle)}"])
        else:
            self._alert(
                "Restart Aloud",
                "Quit and run it again to pick up the update.",
            )
            return
        AppKit.NSApplication.sharedApplication().terminate_(None)

    @objc.python_method
    def _run_off_main(self, work) -> None:
        """Network and git calls must not block the UI thread."""
        threading.Thread(target=work, daemon=True).start()

    @objc.python_method
    def _confirm(self, title: str, message: str, confirm: str = "OK") -> bool:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_(confirm)
        alert.addButtonWithTitle_("Cancel")
        return alert.runModal() == AppKit.NSAlertFirstButtonReturn

    def showAbout_(self, _sender):
        ok, detail = self.controller.engine.check()
        AppKit.NSApplication.sharedApplication().orderFrontStandardAboutPanelWithOptions_({
            "ApplicationName": APP_NAME,
            "ApplicationVersion": __version__,
            "Version": "",
            "Credits": Foundation.NSAttributedString.alloc().initWithString_(
                f"Push-to-talk dictation.\n\nEngine: {self.controller.engine.label}\n"
                f"{'' if ok else '⚠ '}{detail}"
            ),
        })

    # -- menu validation ---------------------------------------------------

    def validateMenuItem_(self, item):
        selector = item.action()
        state = self.controller.state
        if selector == b"startDictation:":
            return state is State.IDLE
        if selector in (b"stopDictation:", b"cancelDictation:"):
            return state is State.RECORDING
        if selector == b"copyLast:":
            return self.controller.last is not None
        if selector == b"transcribeFile:":
            # Files queue now, each in its own window; only a live recording
            # refuses one.
            return True
        return True

    # -- helpers -----------------------------------------------------------

    @objc.python_method
    def _alert(self, title: str, message: str) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.runModal()


def run(config: Config) -> int:
    """Start the app. Blocks until the user quits."""
    # First, before an engine can be built or a tool looked for. A Dock launch
    # inherits none of the shell's PATH, so without this Homebrew's ffmpeg is
    # invisible -- to us and to the libraries that shell out to it themselves.
    toolpath.repair(config.get("tools.path_extra", []))
    toolpath.repair_locale()
    app = AppKit.NSApplication.sharedApplication()
    # Regular by default -- Dock icon, app menu, a place in the switcher. Set
    # interface.dock_icon to false for the menu-bar-only shape; see ui/dock.py
    # for what that policy actually costs.
    app.setActivationPolicy_(
        dock.policy_for(bool(config.get("interface.dock_icon", True)))
    )
    delegate = AloudDelegate.alloc().initWithConfig_(config)
    app.setDelegate_(delegate)
    # Keep a strong reference; NSApplication's delegate is weak.
    globals()["_delegate"] = delegate
    app.activateIgnoringOtherApps_(True)
    app.run()
    return 0
