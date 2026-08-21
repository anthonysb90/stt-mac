"""The menu bar shell: wires hotkey -> recorder -> engine -> injector.

Threading model
---------------
* The **main thread** runs the AppKit run loop. The event tap callback fires
  here, so its work is limited to flipping state and starting/stopping the
  audio stream -- a tap that blocks gets disabled by the system.
* One **worker thread** drains a queue of finished recordings, runs the engine,
  and posts the keystrokes. Serialising through a single worker means two quick
  dictations can never interleave their pastes.
* UI updates hop back to the main thread via :mod:`aloud.mainthread`.
"""

from __future__ import annotations

import logging
import queue
import threading
import subprocess
import time
from enum import Enum
from pathlib import Path
from typing import Optional

import rumps

from . import APP_NAME, __version__, corrections, engines, history, hotkey as hotkey_mod, permissions
from .audio import AudioError, Recorder, wav_duration
from .config import Config
from .dictionary import Dictionary
from .feedback import Feedback
from .injector import TextInjector
from .mainthread import run_on_main
from .paths import CONFIG_FILE, LOG_FILE
from .postprocess import process

log = logging.getLogger(__name__)


class State(Enum):
    IDLE = "Idle"
    RECORDING = "Recording"
    TRANSCRIBING = "Transcribing"
    ERROR = "Error"


#: Menu bar glyphs per state. Replaced by template images once branding lands.
GLYPHS = {
    State.IDLE: "◌",
    State.RECORDING: "●",
    State.TRANSCRIBING: "◍",
    State.ERROR: "⊘",
}

_STOP = object()  # sentinel that shuts the worker down


class AloudApp(rumps.App):
    def __init__(self, config: Config) -> None:
        super().__init__(APP_NAME, title=GLYPHS[State.IDLE], quit_button=None)
        self.config = config
        self.state = State.IDLE

        self.recorder = Recorder(
            sample_rate=int(config.get("audio.sample_rate", 16000)),
            channels=int(config.get("audio.channels", 1)),
            device=config.get("audio.device"),
            max_seconds=float(config.get("audio.max_seconds", 300)),
        )
        self.injector = TextInjector(
            mode=str(config.get("output.mode", "paste")),
            restore_clipboard=bool(config.get("output.restore_clipboard", True)),
            restore_delay=float(config.get("output.restore_delay", 0.8)),
            trailing_space=bool(config.get("output.trailing_space", True)),
        )
        self.feedback = Feedback(config.get("feedback", {}))
        self.engine = engines.select(config)

        # The Dictionary is advertised as hand-editable, so it is re-read
        # before each dictation rather than cached for the life of the app.
        self.dictionary = Dictionary.load()
        self._ruleset = corrections.ruleset_for(self.dictionary)
        self.listener: Optional[hotkey_mod.HotkeyListener] = None

        self._jobs: "queue.Queue[object]" = queue.Queue()
        self._worker = threading.Thread(
            target=self._drain_jobs, name="aloud-transcribe", daemon=True
        )
        self._max_duration_timer: Optional[threading.Timer] = None

        self._build_menu()

    # -- menu --------------------------------------------------------------

    def _build_menu(self) -> None:
        self.status_item = rumps.MenuItem("Idle")
        self.engine_item = rumps.MenuItem("Engine: …")
        self.hotkey_item = rumps.MenuItem("Hotkey: …")
        self.last_item = rumps.MenuItem("Last: —")

        engine_menu = rumps.MenuItem("Switch Engine")
        auto_label = f"Automatic ({engines.REGISTRY[engines.resolve(engines.AUTO)].label})"
        engine_menu.add(
            rumps.MenuItem(auto_label, callback=self._make_engine_switch(engines.AUTO))
        )
        for name in engines.names():
            engine_menu.add(
                rumps.MenuItem(
                    engines.REGISTRY[name].label, callback=self._make_engine_switch(name)
                )
            )

        self.menu = [
            self.status_item,
            self.engine_item,
            self.hotkey_item,
            self.last_item,
            None,
            engine_menu,
            rumps.MenuItem("Check Permissions…", callback=self.on_check_permissions),
            None,
            rumps.MenuItem("Open Config…", callback=self.on_open_config),
            rumps.MenuItem("Open Log…", callback=self.on_open_log),
            None,
            rumps.MenuItem(f"{APP_NAME} {__version__}"),
            rumps.MenuItem("Quit", callback=self.on_quit),
        ]
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        ok, detail = self.engine.check()
        prefix = "" if ok else "⚠ "
        auto = " (auto)" if self.config.get("engine") == engines.AUTO else ""
        self.engine_item.title = f"{prefix}Engine: {self.engine.label}{auto}"
        self.engine_item.set_callback(lambda _: rumps.alert(self.engine.label, detail))
        self.hotkey_item.title = (
            f"Hotkey: hold {hotkey_mod.describe(self.config.get('hotkey.key'))}"
            if self.config.get("hotkey.mode") == "hold"
            else f"Hotkey: tap {hotkey_mod.describe(self.config.get('hotkey.key'))}"
        )

    def _make_engine_switch(self, name: str):
        def switch(_sender) -> None:
            self.config.set("engine", name)
            self.config.save()
            self.engine = engines.select(self.config)
            self._refresh_labels()
            log.info("Switched engine to %s (%s)", name, self.engine.name)
            # Pay the model-load cost now rather than on the next dictation.
            threading.Thread(target=self.engine.warm_up, daemon=True).start()

        return switch

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Install the hotkey, start the worker, and enter the run loop."""
        self._worker.start()
        self._install_hotkey()
        self._warn_about_permissions()
        threading.Thread(target=self.engine.warm_up, daemon=True).start()
        self.run()

    def _install_hotkey(self) -> None:
        try:
            self.listener = hotkey_mod.HotkeyListener(
                key=str(self.config.get("hotkey.key", "right_option")),
                on_press=self.on_hotkey_press,
                on_release=self.on_hotkey_release,
                mode=str(self.config.get("hotkey.mode", "hold")),
            )
            self.listener.install()
        except hotkey_mod.HotkeyError as exc:
            log.error("%s", exc)
            self._set_state(State.ERROR)
            rumps.alert(
                f"{APP_NAME} cannot listen for the hotkey",
                f"{exc}\n\nGrant access, then quit and reopen {APP_NAME}.",
            )
            permissions.open_accessibility_settings()

    def _warn_about_permissions(self) -> None:
        granted, status = permissions.microphone_authorized()
        if status == "not determined":
            permissions.request_microphone()
        elif not granted:
            log.warning("Microphone access is %s", status)

    # -- hotkey handlers (main thread; keep these fast) ---------------------

    def on_hotkey_press(self) -> None:
        if self.state is State.RECORDING:
            return
        try:
            self.recorder.start()
        except AudioError as exc:
            log.error("%s", exc)
            self._fail("Microphone unavailable", str(exc))
            return

        self._set_state(State.RECORDING)
        self.feedback.recording_started()
        self._arm_max_duration()

    def on_hotkey_release(self) -> None:
        if self.state is not State.RECORDING:
            return
        self._disarm_max_duration()
        wav_path = self.recorder.stop()
        self.feedback.recording_stopped()

        if wav_path is None:
            self._set_state(State.IDLE)
            return

        minimum = float(self.config.get("audio.min_seconds", 0.35))
        if wav_duration(wav_path) < minimum:
            log.debug("Discarding recording shorter than %.2fs", minimum)
            wav_path.unlink(missing_ok=True)
            self._set_state(State.IDLE)
            return

        self._set_state(State.TRANSCRIBING)
        self._jobs.put(wav_path)

    def _arm_max_duration(self) -> None:
        seconds = float(self.config.get("audio.max_seconds", 300))
        self._max_duration_timer = threading.Timer(
            seconds, lambda: run_on_main(self._auto_stop)
        )
        self._max_duration_timer.daemon = True
        self._max_duration_timer.start()

    def _disarm_max_duration(self) -> None:
        if self._max_duration_timer is not None:
            self._max_duration_timer.cancel()
            self._max_duration_timer = None

    def _auto_stop(self) -> None:
        log.warning("Hit the maximum recording length; stopping")
        if self.listener is not None:
            self.listener.reset()
        self.on_hotkey_release()

    # -- worker ------------------------------------------------------------

    def _drain_jobs(self) -> None:
        while True:
            job = self._jobs.get()
            if job is _STOP:
                return
            wav_path = job
            assert isinstance(wav_path, Path)
            try:
                self._transcribe_and_deliver(wav_path)
            except Exception as exc:
                log.exception("Transcription pipeline failed")
                # Bind the message now: `exc` is unbound once the block exits.
                message = str(exc)
                run_on_main(lambda: self._fail("Transcription failed", message))
            finally:
                wav_path.unlink(missing_ok=True)
                self._jobs.task_done()

    def _transcribe_and_deliver(self, wav_path: Path) -> None:
        started = time.monotonic()
        transcript = self.engine.transcribe(wav_path, bias_terms=self._bias_terms())

        # Corrections run on the raw transcript, before any other cleanup, so
        # the offsets they report point at what the engine actually produced.
        result = self._corrections_for(transcript.text)
        text = process(result.text, self.config.get("postprocess", {}))
        elapsed = time.monotonic() - started

        if not text:
            log.info("Nothing recognised in %.2fs of audio", wav_duration(wav_path))
            run_on_main(lambda: self._set_state(State.IDLE))
            return

        log.info(
            "Transcribed %d chars in %.2fs via %s (%d corrections)",
            len(text), elapsed, transcript.engine, len(result.applied),
        )
        self.injector.deliver(text)

        for applied in result.applied:
            self.dictionary.record_hit(applied.entry_id)

        if self.config.get("history.enabled", True):
            history.record(
                text,
                transcript.engine,
                elapsed,
                int(self.config.get("history.max_entries", 500)),
                corrections=[applied.to_dict() for applied in result.applied],
                raw=result.original,
            )

        preview = text if len(text) <= 48 else text[:45] + "…"
        run_on_main(lambda: self._show_last(preview, elapsed))

    # -- dictionary --------------------------------------------------------

    def _refresh_dictionary(self) -> None:
        """Pick up edits made to dictionary.json outside the app."""
        try:
            if self.dictionary.reload_if_changed():
                self._ruleset = corrections.ruleset_for(self.dictionary)
                log.info("Reloaded the dictionary (%d entries)", len(self.dictionary))
        except Exception:
            log.exception("Could not reload the dictionary; keeping the current rules")

    def _bias_terms(self) -> list:
        """The short vocabulary list handed to the engine, if it can use one."""
        if not self.config.get("dictionary.enabled", True):
            return []
        if not self.config.get("dictionary.bias.enabled", True):
            return []
        if not getattr(self.engine, "supports_bias", False):
            return []
        self._refresh_dictionary()
        return self.dictionary.bias_terms(int(self.config.get("dictionary.bias.max_terms", 12)))

    def _corrections_for(self, text: str) -> corrections.CorrectionResult:
        if not self.config.get("dictionary.enabled", True):
            return corrections.CorrectionResult(original=text, text=text)
        self._refresh_dictionary()
        return self._ruleset.apply(text)

    # -- state -------------------------------------------------------------

    def _set_state(self, state: State) -> None:
        self.state = state
        run_on_main(lambda: self._apply_state(state))

    def _apply_state(self, state: State) -> None:
        self.title = GLYPHS[state]
        self.status_item.title = state.value

    def _show_last(self, preview: str, elapsed: float) -> None:
        self.last_item.title = f"Last: {preview} ({elapsed:.1f}s)"
        self._apply_state(State.IDLE)
        self.state = State.IDLE

    def _fail(self, title: str, message: str) -> None:
        self._apply_state(State.ERROR)
        self.state = State.ERROR
        self.feedback.error(title, message)
        # Drop back to idle so the next press still works.
        recover = threading.Timer(3.0, lambda: self._set_state(State.IDLE))
        recover.daemon = True
        recover.start()

    # -- menu callbacks ----------------------------------------------------

    def on_check_permissions(self, _sender) -> None:
        detail = permissions.summary()
        if permissions.accessibility_trusted():
            rumps.alert("Permissions", detail)
            return
        permissions.accessibility_trusted(prompt=True)
        rumps.alert(
            "Accessibility access required",
            f"{detail}\n\nEnable {APP_NAME} under Privacy & Security > "
            "Accessibility, then quit and reopen the app.",
        )
        permissions.open_accessibility_settings()

    def on_open_config(self, _sender) -> None:
        self.config.save()  # materialise defaults so there is something to edit
        subprocess.run(["open", "-t", str(CONFIG_FILE)], check=False)

    def on_open_log(self, _sender) -> None:
        subprocess.run(["open", "-t", str(LOG_FILE)], check=False)

    def on_quit(self, _sender) -> None:
        log.info("Quitting")
        self._disarm_max_duration()
        self.recorder.cancel()
        if self.listener is not None:
            self.listener.uninstall()
        self._jobs.put(_STOP)
        rumps.quit_application()
