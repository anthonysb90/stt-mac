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

from . import APP_NAME, __version__, updates
from .config import Config
from .core import DictationController, State
from .mainthread import run_on_main
from .paths import LOG_FILE
from .ui import app_menu
from .ui import transcript_window
from .ui.main_window import MainWindow
from .ui.menu_bar import MenuBarItem
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
        self.menu_bar = None
        self._transcript_windows = []
        self._import_window = None
        return self

    # -- lifecycle ---------------------------------------------------------

    def applicationDidFinishLaunching_(self, _notification):
        app_menu.build({}, self)

        self.main_window = MainWindow(
            self.controller,
            on_settings=self.showSettings_,
            on_transcribe=self._begin_import,
        )
        self.settings = SettingsWindow(self.controller, on_hotkey_changed=self._hotkey_changed)
        self.menu_bar = MenuBarItem(
            self.controller,
            {
                "toggle": self.controller.toggle,
                "open_main": lambda: self.main_window.show(),
                "open_settings": lambda: self.settings.show(),
                "quit": lambda: AppKit.NSApplication.sharedApplication().terminate_(None),
            },
        )
        self._hotkey_changed()

        self.controller.add_observer(self)
        self.controller.start()

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
                f"paste your text into other apps.\n\nAdd {APP_NAME} under Privacy "
                f"& Security → Accessibility, then quit and reopen it.",
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
    def on_state(self, state: State) -> None:
        run_on_main(lambda: self._apply_state(state))

    @objc.python_method
    def _begin_import(self, path) -> None:
        """Open the progress window first, then start the work.

        In that order because preparing the file can itself fail, and a failure
        with nowhere to appear is how you get a silent no-op.
        """
        window = transcript_window.TranscriptWindow(
            path.name, on_cancel=self.controller.cancel_import
        )
        self._import_window = window
        self._transcript_windows.append(window)
        window.show()
        window.begin("Reading the file…")
        self.controller.transcribe_file(path)

    @objc.python_method
    def on_job_started(self, job) -> None:
        if job.source != "file" or self._import_window is None:
            return
        streaming = getattr(self.controller.engine, "supports_progress", False)
        detail = "Transcribing…" if streaming else (
            f"Transcribing with {self.controller.engine.label} — "
            f"this engine reports no progress until it finishes."
        )
        run_on_main(lambda: self._import_window.begin(detail))

    @objc.python_method
    def on_progress(self, job, text, done, total) -> None:
        if job.source != "file" or self._import_window is None:
            return
        window = self._import_window
        run_on_main(lambda: window.update(text, done, total))

    @objc.python_method
    def on_cancelled(self, job) -> None:
        window = self._import_window
        self._import_window = None
        if window is not None:
            run_on_main(window.cancelled)

    @objc.python_method
    def on_result(self, dictation) -> None:
        run_on_main(lambda: self._deliver_result(dictation))

    @objc.python_method
    def _deliver_result(self, dictation) -> None:
        self.main_window.on_result()
        if dictation.source != "file":
            return
        window, self._import_window = self._import_window, None
        if window is None:
            # Started from somewhere without a window — give it one.
            window = transcript_window.TranscriptWindow(dictation.label or "Transcript")
            self._transcript_windows.append(window)
            window.show()
        window.finish(dictation)

    @objc.python_method
    def on_empty(self, job) -> None:
        if job.source != "file":
            return
        window, self._import_window = self._import_window, None
        if window is not None:
            run_on_main(lambda: window.fail(f"No speech was recognised in {job.label}."))

    @objc.python_method
    def on_error(self, title: str, message: str) -> None:
        window, self._import_window = self._import_window, None
        if window is not None:
            # An import failure belongs in the window watching that import,
            # not in a modal alert on top of it.
            run_on_main(lambda: window.fail(f"{title}: {message}"))
            return
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

    def showHistory_(self, _sender):
        self.main_window.show()
        self.main_window.show_pane(0)

    def showDictionary_(self, _sender):
        self.main_window.show()
        self.main_window.show_pane(1)

    def transcribeFile_(self, _sender):
        """File > Transcribe Audio File… — the menu route into the same flow."""
        self.main_window.show_transcribe()
        path = transcript_window.open_panel()
        if path is not None:
            self.main_window.transcribe.select(path)

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
            return self.controller.state is not State.TRANSCRIBING
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
    app = AppKit.NSApplication.sharedApplication()
    # Regular, not Accessory: Dock icon, app menu, and a place in the switcher.
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    delegate = AloudDelegate.alloc().initWithConfig_(config)
    app.setDelegate_(delegate)
    # Keep a strong reference; NSApplication's delegate is weak.
    globals()["_delegate"] = delegate
    app.activateIgnoringOtherApps_(True)
    app.run()
    return 0
