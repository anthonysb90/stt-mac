"""The dictation pipeline, with no user interface attached.

Everything from "the hotkey went down" to "the text is in the other app" lives
here. It imports no AppKit, so it can be driven from a window, a menu bar item,
a test, or a headless script without changing.

Observers
---------
The controller announces what it is doing to any object that implements some
of :class:`Observer`'s methods; missing methods are simply not called. Events
are emitted **on whichever thread produced them** — the hotkey tap's thread for
state changes, the worker thread for results. AppKit is not thread-safe, so UI
observers are responsible for hopping to the main thread themselves (see
:mod:`aloud.mainthread`). Keeping that at the boundary is what lets this module
stay free of UI concerns.

Threading
---------
* The hotkey callback runs on the event tap's run loop and must return
  quickly — it only flips state and starts or stops the audio stream. A tap
  that blocks gets disabled by the system.
* One worker thread drains a queue of finished recordings. Serialising through
  a single worker means two quick dictations can never interleave their pastes.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import corrections, engines, history, hotkey as hotkey_mod, media
from . import audio
from .audio import AudioError, Recorder, wav_duration
from .config import Config
from .corrections import CorrectionResult
from .dictionary import Dictionary
from .feedback import Feedback
from .injector import TextInjector
from .postprocess import defer_to_engine, process

log = logging.getLogger(__name__)


class State(Enum):
    IDLE = "Idle"
    RECORDING = "Recording"
    TRANSCRIBING = "Transcribing"
    ERROR = "Error"


@dataclass
class Job:
    """One unit of work for the transcription thread.

    ``deliver`` is the whole reason this is a class rather than a path. A
    dictation is typed into whatever app you are looking at; a file you
    imported must not be, or transcribing an hour-long recording would dump
    it into the document you happen to have open.
    """

    audio: Path
    deliver: bool = True
    source: str = "microphone"
    label: str = ""
    cleanup: Optional[Any] = None


@dataclass
class Dictation:
    """One completed dictation, as the UI and the history file see it."""

    text: str
    raw: str
    engine: str
    seconds: float
    at: float = field(default_factory=time.time)
    corrections: List[Dict[str, Any]] = field(default_factory=list)
    #: "microphone" for a dictation, "file" for something you imported.
    source: str = "microphone"
    #: The file's name, when this came from one.
    label: str = ""

    @property
    def was_corrected(self) -> bool:
        return bool(self.corrections)

    @property
    def correction_summary(self) -> str:
        if not self.corrections:
            return ""
        if len(self.corrections) == 1:
            only = self.corrections[0]
            return f"{only['from']} → {only['to']}"
        return f"{len(self.corrections)} corrections"


class Observer:
    """What a view may implement. Every method is optional."""

    def on_state(self, state: State) -> None: ...
    def on_result(self, dictation: Dictation) -> None: ...
    def on_progress(self, job: "Job", text: str, done: float, total: float) -> None: ...
    def on_job_started(self, job: "Job") -> None: ...
    def on_devices_changed(self) -> None: ...
    def on_error(self, title: str, message: str) -> None: ...
    def on_dictionary_changed(self) -> None: ...


_STOP = object()


class DictationController:
    """Owns the pipeline, the state machine, and the dictionary."""

    def __init__(self, config: Config, dictionary: Optional[Dictionary] = None) -> None:
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
        self.dictionary = dictionary if dictionary is not None else Dictionary.load()
        self._ruleset = corrections.ruleset_for(self.dictionary)

        self.listener: Optional[hotkey_mod.HotkeyListener] = None
        self._observers: List[Any] = []
        self._jobs: "queue.Queue[object]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._max_duration_timer: Optional[threading.Timer] = None
        self._last: Optional[Dictation] = None
        self._cancel_current = threading.Event()

    # -- observers ---------------------------------------------------------

    def add_observer(self, observer: Any) -> None:
        self._observers.append(observer)

    def remove_observer(self, observer: Any) -> None:
        if observer in self._observers:
            self._observers.remove(observer)

    def _emit(self, event: str, *args: Any) -> None:
        for observer in list(self._observers):
            handler = getattr(observer, event, None)
            if handler is None:
                continue
            try:
                handler(*args)
            except Exception:
                log.exception("Observer %r failed handling %s", observer, event)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Begin listening. Safe to call once."""
        if self._worker is None:
            self._worker = threading.Thread(
                target=self._drain_jobs, name="aloud-transcribe", daemon=True
            )
            self._worker.start()
        self.install_hotkey()
        threading.Thread(target=self.engine.warm_up, daemon=True).start()

    def shutdown(self) -> None:
        self._disarm_max_duration()
        self.recorder.cancel()
        if self.listener is not None:
            self.listener.uninstall()
            self.listener = None
        self._jobs.put(_STOP)

    def install_hotkey(self) -> None:
        """(Re)install the global hotkey from the current config."""
        if self.listener is not None:
            self.listener.uninstall()
            self.listener = None
        try:
            self.listener = hotkey_mod.HotkeyListener(
                key=str(self.config.get("hotkey.key", "right_option")),
                on_press=self.begin_recording,
                on_release=self.finish_recording,
                mode=str(self.config.get("hotkey.mode", "hold")),
            )
            self.listener.install()
        except hotkey_mod.HotkeyError as exc:
            log.error("%s", exc)
            self._set_state(State.ERROR)
            self._emit("on_error", "Aloud cannot listen for the hotkey", str(exc))

    # -- recording ---------------------------------------------------------

    @property
    def level(self) -> float:
        """Current input level, 0.0-1.0. Polled by the meter."""
        return self.recorder.level

    @property
    def last(self) -> Optional[Dictation]:
        return self._last

    def toggle(self) -> None:
        """Start or stop, whichever applies. Used by the buttons and the menu."""
        if self.state is State.RECORDING:
            self.finish_recording()
        elif self.state is State.IDLE:
            self.begin_recording()

    def begin_recording(self) -> None:
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

    def finish_recording(self) -> None:
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
        self._jobs.put(Job(audio=wav_path, deliver=True, source="microphone"))

    def cancel_recording(self) -> None:
        """Abandon the current recording without transcribing it."""
        if self.state is not State.RECORDING:
            return
        self._disarm_max_duration()
        self.recorder.cancel()
        if self.listener is not None:
            self.listener.reset()
        self._set_state(State.IDLE)

    def _arm_max_duration(self) -> None:
        seconds = float(self.config.get("audio.max_seconds", 300))
        timer = threading.Timer(seconds, self._auto_stop)
        timer.daemon = True
        self._max_duration_timer = timer
        timer.start()

    def _disarm_max_duration(self) -> None:
        if self._max_duration_timer is not None:
            self._max_duration_timer.cancel()
            self._max_duration_timer = None

    def _auto_stop(self) -> None:
        log.warning("Hit the maximum recording length; stopping")
        if self.listener is not None:
            self.listener.reset()
        self.finish_recording()

    # -- the worker --------------------------------------------------------

    def _drain_jobs(self) -> None:
        while True:
            job = self._jobs.get()
            if job is _STOP:
                return
            assert isinstance(job, Job)
            self._run_job(job)
            self._jobs.task_done()

    def _run_job(self, job: "Job") -> None:
        """One job, start to finish, including tidying up after it."""
        try:
            self._transcribe_and_deliver(job)
        except Exception as exc:
            log.exception("Transcription pipeline failed")
            self._fail("Transcription failed", str(exc))
        finally:
            if job.cleanup is not None:
                try:
                    job.cleanup()
                except Exception:
                    log.exception("Could not clean up after %s", job.audio)
            elif job.source == "microphone":
                # An imported file belongs to the user; a recording is ours.
                job.audio.unlink(missing_ok=True)

    #: Tests drive one job at a time rather than starting the worker thread.
    _drain_one_for_test = _run_job

    def cancel_import(self) -> None:
        """Ask a running file transcription to stop at the next segment."""
        self._cancel_current.set()

    def _transcribe_and_deliver(self, job: Job) -> None:
        wav_path = job.audio
        started = time.monotonic()
        self._cancel_current.clear()
        self._emit("on_job_started", job)

        # Only files stream. A dictation is a few seconds long, and reporting
        # progress on it would cost more than it tells anyone.
        def report(text: str, done: float, total: float) -> bool:
            self._emit("on_progress", job, text, done, total)
            return not self._cancel_current.is_set()

        streaming = job.source == "file" and getattr(self.engine, "supports_progress", False)
        transcript = self.engine.transcribe(
            wav_path,
            bias_terms=self.bias_terms(),
            on_progress=report if streaming else None,
        )

        # Corrections run on the raw transcript, before any other cleanup, so
        # the offsets they report point at what the engine actually produced.
        result = self.corrections_for(transcript.text)
        text = process(result.text, self.postprocess_options())
        elapsed = time.monotonic() - started

        if self._cancel_current.is_set() and job.source == "file":
            log.info("Import of %s cancelled", job.label)
            self._set_state(State.IDLE)
            self._emit("on_cancelled", job)
            return

        if not text:
            log.info("Nothing recognised in %s", job.label or "the recording")
            self._set_state(State.IDLE)
            self._emit("on_empty", job)
            return

        log.info(
            "Transcribed %d chars in %.2fs via %s (%d corrections) from %s",
            len(text), elapsed, transcript.engine, len(result.applied), job.source,
        )
        if job.deliver:
            self.injector.deliver(text)

        for applied in result.applied:
            self.dictionary.record_hit(applied.entry_id)

        dictation = Dictation(
            text=text,
            raw=result.original,
            engine=transcript.engine,
            seconds=elapsed,
            corrections=[applied.to_dict() for applied in result.applied],
            source=job.source,
            label=job.label,
        )
        self._last = dictation

        if self.config.get("history.enabled", True):
            history.record(
                text,
                transcript.engine,
                elapsed,
                int(self.config.get("history.max_entries", 500)),
                corrections=dictation.corrections,
                raw=dictation.raw,
            )

        self._set_state(State.IDLE)
        self._emit("on_result", dictation)

    # -- importing a file --------------------------------------------------

    def transcribe_file(self, path: Path) -> None:
        """Queue an audio or video file for transcription.

        Converted to 16 kHz mono WAV first where ffmpeg allows, so the rest of
        the pipeline is identical to a dictation. The result is *not* typed
        anywhere — it goes to the history and to whoever is listening.
        """
        path = Path(path).expanduser()
        try:
            prepared = media.prepare(path)
        except media.MediaError as exc:
            log.error("%s", exc)
            self._fail("Could not read that file", str(exc))
            return

        log.info(
            "Importing %s (%s)", path.name,
            "converted to 16 kHz mono" if prepared.converted else "used as-is",
        )
        self._set_state(State.TRANSCRIBING)
        self._jobs.put(Job(
            audio=prepared.path,
            deliver=False,
            source="file",
            label=path.name,
            cleanup=prepared.cleanup,
        ))

    # -- cleanup and the dictionary ---------------------------------------

    def postprocess_options(self) -> Dict[str, Any]:
        """Local cleanup settings, narrowed when the engine did the work.

        Deepgram punctuates, capitalises and strips fillers server-side. Running
        our own pass over that output is not just redundant: our filler list and
        theirs disagree at the edges, and applying spoken-punctuation commands
        to already-punctuated text leaves stray line breaks.
        """
        options = self.config.get("postprocess", {}) or {}
        if getattr(self.engine, "handles_cleanup", False):
            return defer_to_engine(options)
        return dict(options)

    def refresh_dictionary(self) -> bool:
        """Pick up edits made to dictionary.json outside the app."""
        try:
            if self.dictionary.reload_if_changed():
                self.reload_rules()
                log.info("Reloaded the dictionary (%d entries)", len(self.dictionary))
                return True
        except Exception:
            log.exception("Could not reload the dictionary; keeping the current rules")
        return False

    def reload_feedback(self) -> None:
        """Apply a sound change from Settings immediately."""
        self.feedback.update(self.config.get("feedback", {}))

    def reload_rules(self) -> None:
        """Recompile after the dictionary is edited, from the UI or the file."""
        self._ruleset = corrections.ruleset_for(self.dictionary)
        self._emit("on_dictionary_changed")

    def save_dictionary(self) -> None:
        self.dictionary.save()
        self.reload_rules()

    def bias_terms(self) -> List[str]:
        if not self.config.get("dictionary.enabled", True):
            return []
        if not self.config.get("dictionary.bias.enabled", True):
            return []
        if not getattr(self.engine, "supports_bias", False):
            return []
        self.refresh_dictionary()
        return self.dictionary.bias_terms(int(self.config.get("dictionary.bias.max_terms", 12)))

    def corrections_for(self, text: str) -> CorrectionResult:
        if not self.config.get("dictionary.enabled", True):
            return CorrectionResult(original=text, text=text)
        self.refresh_dictionary()
        return self._ruleset.apply(text)

    # -- engine ------------------------------------------------------------

    # -- input device ------------------------------------------------------

    def input_device(self):
        """Whatever the config says to record from. None means system default."""
        return self.config.get("audio.device")

    def set_input_device(self, device) -> None:
        """Switch microphones. Takes effect on the next recording, not this one."""
        self.config.set("audio.device", device)
        self.config.save()
        self.recorder.device = device
        log.info("Input device set to %s", audio.describe_device(device))
        self._emit("on_devices_changed")

    def use_engine(self, name: str) -> None:
        """Switch engines and pay the load cost now rather than mid-dictation."""
        self.config.set("engine", name)
        self.config.save()
        self.engine = engines.select(self.config)
        log.info("Switched engine to %s (%s)", name, self.engine.name)
        threading.Thread(target=self.engine.warm_up, daemon=True).start()
        self._emit("on_state", self.state)

    # -- state -------------------------------------------------------------

    def _set_state(self, state: State) -> None:
        self.state = state
        self._emit("on_state", state)

    def _fail(self, title: str, message: str) -> None:
        self.feedback.error()
        self._set_state(State.ERROR)
        self._emit("on_error", title, message)
        recover = threading.Timer(3.0, lambda: self._set_state(State.IDLE))
        recover.daemon = True
        recover.start()
