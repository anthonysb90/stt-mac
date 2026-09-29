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

import itertools
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
from .engines.base import Segment, TranscriptionEngine
from .feedback import Feedback
from .injector import TextInjector
from .postprocess import defer_to_engine, process

log = logging.getLogger(__name__)


class State(Enum):
    IDLE = "Idle"
    RECORDING = "Recording"
    TRANSCRIBING = "Transcribing"
    ERROR = "Error"


_JOB_IDS = itertools.count(1)


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
    #: Convert to 16 kHz mono WAV on the worker before transcribing. Set for
    #: imported files, which arrive in whatever format they arrive in; a
    #: dictation is already recorded in the target format.
    prepare: bool = False
    cleanup: Optional[Any] = None
    #: The file as the person chose it. ``audio`` is replaced by the converted
    #: WAV during preparation; this keeps where it really came from.
    original: Optional[Path] = None
    #: Identifies the job to observers, so each file's window hears about its
    #: own file -- two queued imports used to share one window slot.
    id: int = field(default_factory=lambda: next(_JOB_IDS))
    #: Set to stop this job. Per job, so Cancel pressed while a file is still
    #: queued is not wiped out when the job ahead of it starts.
    cancel: threading.Event = field(default_factory=threading.Event)
    #: A dictation transcribed while it was spoken (aloud.live.DictationStream),
    #: and the raw audio it needs to finish. None for the whole-file path.
    live: Optional[Any] = None
    pcm: bytes = b""


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
    #: Timed pieces of ``text``, corrected the same way. Files only; empty
    #: when the engine gave no timings, which limits saving to plain text.
    segments: List[Segment] = field(default_factory=list)
    #: The human-readable engine name, for the transcript window.
    engine_label: str = ""
    #: Which job produced this, so a window can tell its result from another's.
    job_id: int = 0
    #: Something the person should know about an otherwise good result, such
    #: as a cloud engine that stopped part-way and returned what it had.
    warning: str = ""
    #: The saved library copy of a file transcription (aloud.library.Record),
    #: or None for dictation, or when saving it failed.
    record: Optional[Any] = None

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

    #: How often to re-check whether Accessibility has been granted, while it
    #: has not been. Seconds. Frequent enough to feel immediate when someone
    #: flips the switch and comes back, cheap enough not to matter.
    ACCESSIBILITY_POLL_SECONDS = 2.0

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
        #: Built on the first file that needs it; see files_engine().
        self._file_engine: Optional[TranscriptionEngine] = None
        self._file_engine_lock = threading.Lock()
        self.dictionary = dictionary if dictionary is not None else Dictionary.load()
        self._ruleset = corrections.ruleset_for(self.dictionary)

        self.listener: Optional[hotkey_mod.HotkeyListener] = None
        self._observers: List[Any] = []
        #: Dictations and files have separate queues and separate workers.
        #: With one queue, a dictation made while an hour-long sermon was
        #: transcribing waited for the whole sermon: the hotkey recorded, and
        #: nothing appeared until the file was done.
        self._jobs: "queue.Queue[object]" = queue.Queue()
        self._file_jobs: "queue.Queue[object]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._file_worker: Optional[threading.Thread] = None
        #: The Dictionary is read and updated from both workers (and the Quick
        #: Dictate session); its rules and hit counts are not thread-safe.
        self._dictionary_lock = threading.RLock()
        self._max_duration_timer: Optional[threading.Timer] = None
        self._last: Optional[Dictation] = None
        self._stopping = threading.Event()
        #: Guards every state transition. The worker finishing a job and the
        #: hotkey starting a recording happen on different threads, and the
        #: worker's "back to idle" used to land on top of RECORDING -- after
        #: which the key release was ignored and the microphone stayed open.
        self._state_lock = threading.RLock()
        self._running: Optional[Job] = None
        self._running_file: Optional[Job] = None
        #: The Quick Dictate session, while one is listening.
        self.live = None
        self._last_short_tap = 0.0
        #: The hotkey dictation being transcribed as it is spoken.
        self._stream = None

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
                target=self._drain_jobs, args=(self._jobs,), name="aloud-dictation", daemon=True
            )
            self._worker.start()
        if self._file_worker is None:
            self._file_worker = threading.Thread(
                target=self._drain_jobs, args=(self._file_jobs,), name="aloud-files", daemon=True
            )
            self._file_worker.start()
        self.install_hotkey()
        self._watch_for_accessibility()
        threading.Thread(target=self.engine.warm_up, daemon=True).start()

    def shutdown(self) -> None:
        self._stopping.set()
        if self.live is not None:
            self.live.cancel()
        self._disarm_max_duration()
        self.recorder.cancel()
        if self.listener is not None:
            self.listener.uninstall()
            self.listener = None
        self._jobs.put(_STOP)
        for job in list(self._file_jobs.queue):
            if isinstance(job, Job):
                job.cancel.set()
        if self._running_file is not None:
            self._running_file.cancel.set()
        self._file_jobs.put(_STOP)

    def _watch_for_accessibility(self) -> None:
        """Reinstall the hotkey the moment Accessibility is granted.

        The tap has to exist before the grant does -- the app starts listening
        at launch -- and a tap built while untrusted stays deaf to other apps
        for the life of the process. Until now that meant granting access did
        nothing visible and the only cure was quitting and reopening, which is
        a thing to know rather than a thing to notice.

        So: poll the trust flag, and rebuild the tap when it flips. The check
        is a cheap local lookup, and the thread stops the moment it succeeds.
        """
        from .permissions import accessibility_trusted

        if accessibility_trusted():
            return

        def watch() -> None:
            while not self._stopping.is_set():
                if self._stopping.wait(self.ACCESSIBILITY_POLL_SECONDS):
                    return
                if not accessibility_trusted():
                    continue
                log.info("Accessibility granted; reinstalling the hotkey")
                self._emit("on_accessibility_granted")
                return

        threading.Thread(target=watch, name="aloud-permissions", daemon=True).start()

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
                on_chord=self.discard_chord,
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
        if self.live is not None and self.live.active:
            # Quick Dictate is listening: the hotkey means "stop", not a
            # second recording on top of it. Reset the listener, or toggle
            # mode would take the next tap as a "stop" and swallow it.
            self.live.stop()
            if self.listener is not None:
                self.listener.reset()
            return
        try:
            self.recorder.start()
        except AudioError as exc:
            log.error("%s", exc)
            if self.listener is not None:
                # Otherwise toggle mode thinks it is still "on", and the next
                # tap is swallowed as a stop.
                self.listener.reset()
            self._fail("Microphone unavailable", str(exc))
            return
        self._set_state(State.RECORDING)
        self.feedback.recording_started()
        self._arm_max_duration()
        self._stream = self._start_stream()

    def _start_stream(self):
        """Transcribe while the key is held, when the recorder allows it."""
        if not self.config.get("dictation.live", True):
            return None
        if not hasattr(self.recorder, "read_new") or not hasattr(self.recorder, "stop_with_pcm"):
            return None
        try:
            from .live import DictationStream

            return DictationStream(self, self.recorder)
        except Exception:
            log.exception("Could not start live dictation; using the whole recording")
            return None

    def finish_recording(self) -> None:
        if self.state is not State.RECORDING:
            return
        self._disarm_max_duration()
        if self.listener is not None:
            # Stopped from the button or the menu in toggle mode: without this
            # the next hotkey tap is taken as "stop" and does nothing.
            self.listener.reset()
        stream, self._stream = self._stream, None
        pcm = b""
        if stream is not None:
            stream.stop()  # returns at once: this runs in the hotkey callback
            wav_path, pcm = self.recorder.stop_with_pcm()
        else:
            wav_path = self.recorder.stop()
        self.feedback.recording_stopped()

        if wav_path is None:
            self._set_state(self._resting_state())
            self._note_short_tap()  # a tap too quick to capture any audio
            return

        minimum = float(self.config.get("audio.min_seconds", 0.35))
        if wav_duration(wav_path) < minimum:
            log.debug("Discarding recording shorter than %.2fs", minimum)
            wav_path.unlink(missing_ok=True)
            self._set_state(self._resting_state())
            self._note_short_tap()
            return

        with self._state_lock:
            self._jobs.put(Job(audio=wav_path, deliver=True, source="microphone",
                               live=stream, pcm=pcm))
            self._set_state(State.TRANSCRIBING)

    def discard_chord(self) -> None:
        """The hotkey was used to type (Option+E, say): throw the recording away.

        Before, holding Right Option to type an accented letter recorded the
        keystrokes' clatter and, if held long enough, pasted a transcription
        of it. Now nothing is transcribed, and the press does not count as
        half of a double tap.
        """
        self._last_short_tap = 0.0
        if self.state is State.RECORDING:
            log.debug("Hotkey used as a modifier; discarding the recording")
            self.cancel_recording()

    #: Two taps of the hotkey this close together open Quick Dictate.
    DOUBLE_TAP_SECONDS = 0.6

    def _note_short_tap(self) -> None:
        """A tap too short to be dictation. Two in a row open Quick Dictate.

        Hold mode only: in toggle mode a tap already means start or stop.
        Works from any app, because it rides on the hotkey's own event tap.
        """
        if not self.config.get("quick_dictate.double_tap", True):
            return
        if str(self.config.get("hotkey.mode", "hold")) != "hold":
            return
        now = time.monotonic()
        if now - self._last_short_tap <= self.DOUBLE_TAP_SECONDS:
            self._last_short_tap = 0.0
            log.info("Double tap: opening Quick Dictate")
            self._emit("on_quick_dictate_requested")
        else:
            self._last_short_tap = now

    def start_live(self, on_update=None, on_finished=None, on_error=None):
        """Start a Quick Dictate session. Raises RuntimeError if it cannot."""
        from .live import LiveSession

        if self.live is not None and self.live.active:
            self.live.cancel()

        def finished(result) -> None:
            self._record_live(result)
            if on_finished is not None:
                on_finished(result)

        session = LiveSession(self, on_update=on_update, on_finished=finished,
                              on_error=on_error)
        session.start()
        self.live = session
        return session

    def _record_live(self, result) -> None:
        """Quick Dictate results go in the history, like any dictation."""
        if not result.text or not self.config.get("history.enabled", True):
            return
        try:
            history.record(result.text, result.engine, result.seconds,
                           int(self.config.get("history.max_entries", 500)), raw=result.raw)
        except Exception:
            log.exception("Could not write the history entry")

    def cancel_recording(self) -> None:
        """Abandon the current recording without transcribing it."""
        if self.state is not State.RECORDING:
            return
        self._disarm_max_duration()
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
        self.recorder.cancel()
        if self.listener is not None:
            self.listener.reset()
        self._set_state(self._resting_state())

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

    def _drain_jobs(self, jobs: "queue.Queue[object]") -> None:
        while True:
            job = jobs.get()
            if job is _STOP:
                return
            assert isinstance(job, Job)
            self._run_job(job)
            jobs.task_done()

    def _run_job(self, job: "Job") -> None:
        """One job, start to finish, including tidying up after it."""
        is_file = job.source == "file"
        if is_file:
            self._running_file = job
        else:
            self._running = job
        try:
            if job.cancel.is_set():
                # Cancelled while it was still waiting in the queue.
                log.info("Skipping %s: cancelled before it started", job.label or "a job")
                self._job_done(job)
                self._emit("on_cancelled", job)
                return
            self._transcribe_and_deliver(job)
        except Exception as exc:
            log.exception("Transcription pipeline failed")
            self._fail("Transcription failed", str(exc), job=job)
        finally:
            if is_file:
                self._running_file = None
                self._emit("on_files_changed")
            else:
                self._running = None
                # A short tap landing between this job's _settle and the line
                # above saw it still running and chose TRANSCRIBING. Nothing
                # would ever clear that, so check once more now it is done.
                with self._state_lock:
                    stuck = self.state is State.TRANSCRIBING and self._jobs.qsize() == 0
                if stuck:
                    self._settle()
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

    def cancel_import(self, job: Optional["Job"] = None) -> None:
        """Stop a file transcription: ``job``, or whichever file is running.

        A running job stops at its next progress report; a queued one is
        skipped when its turn comes.
        """
        target = job if job is not None else self._running_file
        if target is not None and target.source == "file":
            target.cancel.set()

    def files_pending(self) -> int:
        """Files queued or being transcribed right now."""
        return self._file_jobs.qsize() + (1 if self._running_file is not None else 0)

    def _transcribe_and_deliver(self, job: Job) -> None:
        if job.original is None:
            job.original = job.audio
        if job.prepare:
            # Emitted first so the progress window exists while ffmpeg runs;
            # "Reading the file…" is honest about what this stage is.
            self._emit("on_job_preparing", job)
            prepared = media.prepare(job.audio, cancel=job.cancel)  # MediaError -> _run_job
            job.audio = prepared.path
            job.cleanup = prepared.cleanup
            log.info(
                "Importing %s (%s)", job.label,
                "converted to 16 kHz mono" if prepared.converted else "used as-is",
            )

        wav_path = job.audio
        engine = self.engine_for(job)
        started = time.monotonic()
        self._emit("on_job_started", job)

        # Only files stream. A dictation is a few seconds long, and reporting
        # progress on it would cost more than it tells anyone.
        def report(text: str, done: float, total: float) -> bool:
            self._emit("on_progress", job, text, done, total)
            return not job.cancel.is_set()

        streaming = job.source == "file" and getattr(engine, "supports_progress", False)
        transcript = None
        if job.live is not None:
            # Most of it was transcribed while the key was held; this does
            # the last phrase. None means it failed: use the WAV instead.
            engine = job.live.engine
            transcript = job.live.finish(job.pcm)
        if transcript is None:
            transcript = engine.transcribe(
                wav_path,
                bias_terms=self.bias_terms(engine),
                on_progress=report if streaming else None,
            )

        # Corrections run on the raw transcript, before any other cleanup, so
        # the offsets they report point at what the engine actually produced.
        options = self.postprocess_options(engine)
        result = self.corrections_for(transcript.text)
        text = process(result.text, options)
        segments = self._clean_segments(transcript.segments, options)
        elapsed = time.monotonic() - started

        if job.cancel.is_set() and job.source == "file":
            log.info("Import of %s cancelled", job.label)
            self._job_done(job)
            self._emit("on_cancelled", job)
            return

        if not text:
            log.info("Nothing recognised in %s", job.label or "the recording")
            self._job_done(job)
            self._emit("on_empty", job)
            return

        log.info(
            "Transcribed %d chars in %.2fs via %s (%d corrections) from %s",
            len(text), elapsed, transcript.engine, len(result.applied), job.source,
        )
        if job.deliver:
            self.injector.deliver(text)

        with self._dictionary_lock:
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
            segments=segments,
            engine_label=getattr(engine, "label", transcript.engine),
            job_id=job.id,
            warning=str(transcript.meta.get("warning", "") or ""),
        )
        if job.source == "file":
            dictation.record = self._save_to_library(job, dictation, transcript, wav_path)
        self._last = dictation

        if self.config.get("history.enabled", True):
            try:
                history.record(
                    text,
                    transcript.engine,
                    elapsed,
                    int(self.config.get("history.max_entries", 500)),
                    corrections=dictation.corrections,
                    raw=dictation.raw,
                )
            except Exception:
                # The history is a convenience; the transcript is the point.
                # A history-file problem used to discard a finished hour-long
                # file and report "Transcription failed".
                log.exception("Could not write the history entry")

        self._job_done(job)
        self._emit("on_result", dictation)

    def _save_to_library(self, job: "Job", dictation: "Dictation", transcript, wav_path: Path):
        """Keep a finished file transcription in the library. Never raises.

        A full disk or a permissions problem must cost the saved copy, not the
        transcript the person is waiting to see — so failure is logged and the
        window still shows the result, which can be exported from there.
        """
        if not self.config.get("library.enabled", True):
            return None
        from . import library

        try:
            seconds = wav_duration(wav_path) if wav_path.suffix.lower() == ".wav" else 0.0
            if not seconds:
                seconds = media.duration_of(job.original or wav_path)
            record = library.Record(
                title=library.title_from(job.label),
                text=dictation.text,
                raw_text=dictation.raw,
                segments=list(dictation.segments),
                source_name=job.label,
                source_path=str(job.original or ""),
                audio_seconds=seconds,
                engine=transcript.engine,
                engine_label=dictation.engine_label,
                language=transcript.language or "",
                transcribe_seconds=dictation.seconds,
                corrections=list(dictation.corrections),
            )
            library.save(record)
            log.info("Saved %s to the library at %s", job.label, record.folder)
            return record
        except Exception:
            log.exception("Could not save %s to the library", job.label)
            return None

    # -- importing a file --------------------------------------------------

    def transcribe_file(self, path: Path) -> Optional["Job"]:
        """Queue an audio or video file for transcription.

        Converted to 16 kHz mono WAV where ffmpeg allows -- on the worker, not
        here. This is called from the main thread, and ffmpeg on a long video
        can run for minutes; doing that here froze every window and starved
        the event tap. Here we only check the file exists, which is cheap and
        catches the common mistake immediately.

        The result is *not* typed anywhere — it goes to the history and to
        whoever is listening. Returns the queued job, so the caller can match
        events to it, or None when the file was refused.
        """
        path = Path(path).expanduser()
        if not path.is_file():
            self._fail("Could not read that file", f"No such file: {path}")
            return None

        # Files have their own queue and do not touch the app's state: the
        # menu bar's "Transcribing" means *your dictation* is on its way, and
        # the hotkey stays live however long a file takes. A file's progress
        # lives in its own window. Queuing while recording is fine for the
        # same reason -- the recording is not disturbed.
        job = Job(audio=path, deliver=False, source="file", label=path.name, prepare=True)
        self._file_jobs.put(job)
        self._emit("on_files_changed")
        return job

    # -- cleanup and the dictionary ---------------------------------------

    def _clean_segments(self, segments: List[Segment], options: Dict[str, Any]) -> List[Segment]:
        """Give the timed pieces the same corrections as the text.

        Otherwise a saved subtitle file would say "cloud code" where the
        transcript beside it says "Claude Code". Each segment is corrected on
        its own, so a correction spanning a segment boundary is missed -- rare,
        since segments break at sentences and pauses, and harmless when it
        happens: the plain text still has it.
        """
        cleaned = []
        for segment in segments:
            text = process(self.corrections_for(segment.text).text, options)
            if text.strip():
                cleaned.append(Segment(segment.start, segment.end, text.strip(), segment.speaker))
        return cleaned

    def postprocess_options(self, engine: Optional[TranscriptionEngine] = None) -> Dict[str, Any]:
        """Local cleanup settings, narrowed when the engine did the work.

        Deepgram punctuates, capitalises and strips fillers server-side. Running
        our own pass over that output is not just redundant: our filler list and
        theirs disagree at the edges, and applying spoken-punctuation commands
        to already-punctuated text leaves stray line breaks.
        """
        options = self.config.get("postprocess", {}) or {}
        engine = engine if engine is not None else self.engine
        if getattr(engine, "handles_cleanup", False):
            return defer_to_engine(options)
        return dict(options)

    def refresh_dictionary(self) -> bool:
        """Pick up edits made to dictionary.json outside the app."""
        try:
            with self._dictionary_lock:
                changed = self.dictionary.reload_if_changed()
            if changed:
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
        with self._dictionary_lock:
            self._ruleset = corrections.ruleset_for(self.dictionary)
        self._emit("on_dictionary_changed")

    def save_dictionary(self) -> None:
        self.dictionary.save()
        self.reload_rules()

    def bias_terms(self, engine: Optional[TranscriptionEngine] = None) -> List[str]:
        engine = engine if engine is not None else self.engine
        if not self.config.get("dictionary.enabled", True):
            return []
        if not self.config.get("dictionary.bias.enabled", True):
            return []
        if not getattr(engine, "supports_bias", False):
            return []
        self.refresh_dictionary()
        return self.dictionary.bias_terms(int(self.config.get("dictionary.bias.max_terms", 12)))

    def corrections_for(self, text: str) -> CorrectionResult:
        if not self.config.get("dictionary.enabled", True):
            return CorrectionResult(original=text, text=text)
        self.refresh_dictionary()
        with self._dictionary_lock:
            return self._ruleset.apply(text)

    # -- engine ------------------------------------------------------------

    def engine_for(self, job: "Job") -> TranscriptionEngine:
        """Dictations use the dictation engine; files use the file engine."""
        return self.files_engine() if job.source == "file" else self.engine

    def file_engine_setting(self) -> str:
        return str(self.config.get("file_engine", engines.SAME) or engines.SAME)

    def files_engine(self) -> TranscriptionEngine:
        """The engine for files, built the first time one is transcribed.

        "same" -- or naming the engine dictation already resolved to -- hands
        back the dictation engine itself. Building a second instance would load
        the same model twice, which for Parakeet is several GB of memory for
        nothing.

        Otherwise the engine is built on demand and kept. Not at launch: a
        second resident model is only worth its memory once there is a file.
        An explicitly named engine is used even when it is not ready, the same
        rule dictation follows -- the file window then says why it failed
        rather than quietly transcribing with something else.
        """
        setting = self.file_engine_setting()
        if setting == engines.SAME:
            return self.engine
        resolved = engines.resolve(setting)
        if resolved == self.engine.name:
            return self.engine
        with self._file_engine_lock:
            if self._file_engine is None or self._file_engine.name != resolved:
                self._file_engine = engines.build(resolved, self.config.engine_options(resolved))
            return self._file_engine

    def use_file_engine(self, name: str) -> None:
        """Choose the engine for files. Takes effect on the next file."""
        self.config.set("file_engine", name)
        self.config.save()
        self.reset_file_engine()
        log.info("File engine set to %s", name)

    def reset_file_engine(self) -> None:
        """Drop the cached file engine so a changed model or key is re-read."""
        with self._file_engine_lock:
            old, self._file_engine = self._file_engine, None
        if old is not None and old is not self.engine:
            old.close()

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
        old = self.engine
        self.engine = engines.select(self.config)
        self.reset_file_engine()
        if old is not self.engine:
            # Otherwise every change in Settings left the previous model --
            # several GB for Parakeet -- in memory until the app quit.
            old.close()
        log.info("Switched engine to %s (%s)", name, self.engine.name)
        threading.Thread(target=self.engine.warm_up, daemon=True).start()
        self._emit("on_state", self.state)

    # -- state -------------------------------------------------------------

    def _set_state(self, state: State) -> None:
        with self._state_lock:
            self.state = state
        self._emit("on_state", state)

    def _job_done(self, job: "Job") -> None:
        """A job finished. Only dictation moves the app's state."""
        if job.source != "file":
            self._settle()

    def _resting_state(self) -> State:
        """IDLE, unless there is still dictation queued behind this."""
        busy = self._jobs.qsize() > 0 or self._running is not None
        return State.TRANSCRIBING if busy else State.IDLE

    def _settle(self) -> None:
        """Leave TRANSCRIBING once a job is done -- and never leave RECORDING.

        The worker calls this when it finishes. If the hotkey went down while
        it worked, the recording owns the state now: overwriting RECORDING was
        how the key release came to be ignored and the microphone left open.
        With more jobs queued it stays TRANSCRIBING, which is what the menu bar
        should say while they run.
        """
        with self._state_lock:
            if self.state is State.RECORDING:
                return
            state = State.TRANSCRIBING if self._jobs.qsize() > 0 else State.IDLE
            self.state = state
        self._emit("on_state", state)

    def _fail(self, title: str, message: str, job: Optional["Job"] = None) -> None:
        """Report a failure without ever disturbing a live recording.

        A file's failure belongs to that file's window (``on_job_failed``); it
        is not an alert, and it does not flash the app into ERROR.
        """
        if job is not None and job.source == "file":
            self.feedback.error()
            self._emit("on_job_failed", job, title, message)
            return
        if job is not None:
            self._job_done(job)
        self.feedback.error()
        with self._state_lock:
            recording = self.state is State.RECORDING
            if not recording:
                self.state = State.ERROR
        if not recording:
            self._emit("on_state", State.ERROR)
        self._emit("on_error", title, message)
        if recording:
            return
        recover = threading.Timer(3.0, self._recover_from_error)
        recover.daemon = True
        recover.start()

    def _recover_from_error(self) -> None:
        """Clear ERROR after its three seconds -- and only ERROR.

        Setting IDLE unconditionally was a bug with a long fuse: press the
        hotkey again inside the three-second window and the timer fired *over*
        RECORDING. finish_recording() then saw the wrong state and returned
        without stopping the recorder, leaving the microphone capturing until
        the app quit.
        """
        with self._state_lock:
            if self.state is not State.ERROR:
                return
            self.state = self._resting_state()
            state = self.state
        self._emit("on_state", state)
