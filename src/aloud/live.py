"""Transcribing while you speak.

Hotkey dictation records everything, then transcribes everything, so the wait
after you let go grows with how long you talked. This module works the other
way round, and it is what drives the Quick Dictate window:

1. **Listen for pauses.** A simple level detector marks each 30 ms frame as
   speech or quiet, against a noise floor it learns as it goes.
2. **Commit at each pause.** When a phrase ends, that stretch of audio is
   transcribed once, for good, and its text never changes again.
3. **Preview the phrase in progress.** About once a second the unfinished
   phrase is transcribed too, and shown greyed out until it is committed.
4. **On stop, only the last phrase is left.** However long you spoke, the text
   is ready a fraction of a second after you stop.

It works with every engine, because it only ever hands an engine a short,
complete phrase — which is also the shape of audio every engine is best at.
Stretches with no speech are never sent at all, so Whisper has nothing to
invent words over.

:class:`LiveTranscriber` is the logic and has no threads, microphone or engine
of its own, so it is tested directly. :class:`LiveSession` wires it to a
microphone, an engine and the Dictionary on a background thread.
"""

from __future__ import annotations

import array
import logging
import sys
import tempfile
import threading
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .engines.base import Segment

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2
FRAME_SAMPLES = 480  # 30 ms at 16 kHz

#: Quiet this long ends a phrase. Long enough not to split a sentence at a
#: breath, short enough that text appears while you are still talking.
DEFAULT_PAUSE = 0.7
#: A phrase with no pause is cut here anyway, at its quietest moment, so a
#: fast talker still sees text commit and no single request grows unbounded.
DEFAULT_MAX_PIECE = 20.0
#: How often the phrase in progress is re-transcribed for the grey preview.
DEFAULT_PREVIEW_EVERY = 1.0
#: Audio kept either side of detected speech, so first and last syllables
#: are not clipped.
PAD_SECONDS = 0.25
#: Levels below this are never speech, however quiet the room (int16 RMS;
#: about -40 dBFS).
ABSOLUTE_FLOOR = 330.0
#: Speech must be this many times louder than the room's noise floor.
SPEECH_RATIO = 3.0

TranscribeFn = Callable[[bytes], Tuple[str, List[Segment]]]


def frame_rms(pcm: bytes) -> float:
    """Root-mean-square level of one frame of 16-bit mono audio."""
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":  # pragma: no cover - WAV/PortAudio are little-endian
        samples.byteswap()
    if not samples:
        return 0.0
    return (sum(s * s for s in samples) / len(samples)) ** 0.5


@dataclass
class Piece:
    """One committed phrase."""

    start: float  # seconds from the start of the session
    end: float
    text: str
    segments: List[Segment] = field(default_factory=list)


class LiveTranscriber:
    """Turns a growing recording into committed phrases plus a live preview.

    Feed it audio with :meth:`feed`, then call :meth:`step` regularly; each step
    does at most one transcription (a commit or a preview) and reports whether
    anything changed. :meth:`finish` transcribes whatever is left.
    """

    def __init__(
        self,
        transcribe: TranscribeFn,
        pause: float = DEFAULT_PAUSE,
        max_piece: float = DEFAULT_MAX_PIECE,
        preview_every: float = DEFAULT_PREVIEW_EVERY,
        previews: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transcribe = transcribe
        self.pause = pause
        self.max_piece = max_piece
        self.preview_every = preview_every
        self.previews = previews
        self._clock = clock

        self.audio = bytearray()
        self.pieces: List[Piece] = []
        self.preview = ""

        self._levels: List[float] = []   # one per complete frame
        self._speech: List[bool] = []
        self._floor: Optional[float] = None
        self._start_frame = 0            # first frame not yet committed
        self._last_speech_frame = -1     # last frame that was speech
        self._preview_frames = 0         # uncommitted frames at the last preview
        self._preview_at = 0.0

    # -- input -------------------------------------------------------------

    def feed(self, pcm: bytes) -> None:
        """Append newly recorded audio and classify its complete frames."""
        self.audio.extend(pcm)
        frame_bytes = FRAME_SAMPLES * BYTES_PER_SAMPLE
        while (len(self._levels) + 1) * frame_bytes <= len(self.audio):
            index = len(self._levels)
            level = frame_rms(bytes(self.audio[index * frame_bytes:(index + 1) * frame_bytes]))
            self._levels.append(level)
            speech = self._is_speech(level)
            self._speech.append(speech)
            if speech:
                self._last_speech_frame = index

    def _is_speech(self, level: float) -> bool:
        """Compare against a noise floor that follows the room, slowly.

        The floor drops at once to anything quieter, and creeps up only very
        slowly, so a long sentence cannot teach it that speech is the room.
        It starts by assuming a quiet room rather than learning from the first
        frame: people start talking the moment the window opens, and a floor
        seeded from their voice heard the whole first phrase as background.
        """
        if self._floor is None:
            self._floor = ABSOLUTE_FLOOR / SPEECH_RATIO
        if level < self._floor:
            self._floor = level
        else:
            self._floor += (level - self._floor) * 0.002
        return level > max(ABSOLUTE_FLOOR, self._floor * SPEECH_RATIO)

    # -- the work ----------------------------------------------------------

    @property
    def frames(self) -> int:
        return len(self._levels)

    def _seconds(self, frames: int) -> float:
        return frames * FRAME_SAMPLES / float(SAMPLE_RATE)

    def _frames(self, seconds: float) -> int:
        return int(round(seconds * SAMPLE_RATE / FRAME_SAMPLES))

    def _has_speech(self, start: int, end: int) -> bool:
        return any(self._speech[start:end])

    def step(self) -> bool:
        """Commit a finished phrase, or refresh the preview. True if changed."""
        start, now = self._start_frame, self.frames
        if now <= start:
            return False

        if not self._has_speech(start, now):
            # Only silence since the last commit: let it go, keeping a little
            # lead-in so the next phrase's first syllable is not clipped.
            keep = self._frames(PAD_SECONDS)
            if now - start > keep:
                self._start_frame = now - keep
            return False

        phrase_end = self._first_pause(start, now)
        if phrase_end is not None:
            cut = min(now, phrase_end + self._frames(PAD_SECONDS))
            self._commit(cut)
            return True

        if now - start >= self._frames(self.max_piece):
            self._commit(self._quietest_cut(start, now))
            return True

        if self.previews and self._clock() - self._preview_at >= self.preview_every \
                and now > self._preview_frames:
            self._preview_at = self._clock()
            self._preview_frames = now
            text, _segments = self._transcribe(self._slice(start, now))
            if text.strip() != self.preview:
                self.preview = text.strip()
                return True
        return False

    def _first_pause(self, start: int, end: int) -> Optional[int]:
        """Where the first finished phrase after ``start`` ends, if one has.

        The *first*, not the latest: when transcription falls behind -- a slow
        engine, a burst of audio -- looking only at the end of the buffer
        missed the pauses in between, and the phrases ran together into one
        long piece, which is exactly the wait this module exists to avoid.
        """
        needed = self._frames(self.pause)
        seen_speech = False
        quiet = 0
        for index in range(start, end):
            if self._speech[index]:
                seen_speech = True
                quiet = 0
            elif seen_speech:
                quiet += 1
                if quiet >= needed:
                    return index - quiet + 1  # the frame after the last speech
        return None

    def finish(self) -> None:
        """Commit everything left. Call once recording has stopped."""
        if self.frames > self._start_frame and self._has_speech(self._start_frame, self.frames):
            self._commit(self.frames)
        self.preview = ""

    def _quietest_cut(self, start: int, end: int) -> int:
        """The quietest frame in the last three seconds: least likely mid-word."""
        window = self._frames(3.0)
        low = max(start + 1, end - window)
        return min(range(low, end), key=lambda i: self._levels[i]) + 1

    def _slice(self, start: int, end: int) -> bytes:
        frame_bytes = FRAME_SAMPLES * BYTES_PER_SAMPLE
        return bytes(self.audio[start * frame_bytes:end * frame_bytes])

    def _commit(self, cut: int) -> None:
        start = self._start_frame
        # Trim silence before the first word (keeping a little lead-in): less
        # audio to transcribe, and timestamps that start where speech does.
        first = next((i for i in range(start, cut) if self._speech[i]), start)
        start = max(start, first - self._frames(PAD_SECONDS))
        text, segments = self._transcribe(self._slice(start, cut))
        offset = self._seconds(start)
        end = self._seconds(cut)
        text = text.strip()
        if text:
            shifted = [s.shifted(offset) for s in segments] or [Segment(offset, end, text)]
            self.pieces.append(Piece(offset, end, text, shifted))
        self._start_frame = cut
        self._preview_frames = 0
        self.preview = ""

    # -- output ------------------------------------------------------------

    @property
    def text(self) -> str:
        return " ".join(p.text for p in self.pieces)

    @property
    def segments(self) -> List[Segment]:
        return [s for p in self.pieces for s in p.segments]

    @property
    def seconds(self) -> float:
        return len(self.audio) / float(SAMPLE_RATE * BYTES_PER_SAMPLE)


# ---------------------------------------------------------------------------
# A session: microphone + engine + Dictionary, on its own thread
# ---------------------------------------------------------------------------


@dataclass
class LiveResult:
    text: str        # corrected and cleaned, ready to use
    raw: str         # what the engine heard
    segments: List[Segment]
    seconds: float   # length of the recording
    engine: str
    engine_label: str


class LiveSession:
    """One Quick Dictate recording.

    Callbacks run on the session's own thread; a UI must hop to the main
    thread itself. ``on_update(committed, preview)`` fires as text appears,
    ``on_finished(result)`` once after :meth:`stop`, ``on_error(message)`` if
    the microphone or engine fails (the session then ends).
    """

    TICK = 0.2

    def __init__(self, controller, on_update=None, on_finished=None, on_error=None,
                 recorder=None) -> None:
        self.controller = controller
        self.engine = controller.engine
        self._on_update = on_update or (lambda *_: None)
        self._on_finished = on_finished or (lambda *_: None)
        self._on_error = on_error or (lambda *_: None)
        config = controller.config
        if recorder is None:
            from .audio import Recorder

            recorder = Recorder(sample_rate=SAMPLE_RATE, channels=1,
                                device=config.get("audio.device"))
        self.recorder = recorder
        previews = config.get("quick_dictate.previews", "auto")
        if previews == "auto":
            # Every preview of a cloud engine is an upload, and a bill.
            previews = not getattr(self.engine, "cloud", False)
        self.transcriber = LiveTranscriber(
            self._transcribe,
            pause=float(config.get("quick_dictate.pause_seconds", DEFAULT_PAUSE)),
            previews=bool(previews),
        )
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cursor = 0
        self.started_at = 0.0

    # -- control -----------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def level(self) -> float:
        return self.recorder.level

    def start(self) -> None:
        from .audio import AudioError

        ok, detail = self.engine.check()
        if not ok:
            raise RuntimeError(detail)
        try:
            self.recorder.start()
        except AudioError as exc:
            raise RuntimeError(str(exc)) from exc
        self.started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="aloud-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop listening and finish the transcript. Returns at once."""
        self._stop.set()

    def cancel(self) -> None:
        """Stop and throw the recording away."""
        self._cancel.set()
        self._stop.set()

    # -- the thread --------------------------------------------------------

    def _read(self) -> None:
        pcm, self._cursor = self.recorder.read_new(self._cursor)
        if pcm:
            self.transcriber.feed(pcm)

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.TICK):
                self._read()
                if self.transcriber.step():
                    self._publish()
            self.recorder.halt()
            self._read()
            self.recorder.discard()
            if self._cancel.is_set():
                return
            self.transcriber.finish()
            self._on_finished(self._result())
        except Exception as exc:
            log.exception("Quick Dictate failed")
            self.recorder.halt()
            self.recorder.discard()
            self._on_error(str(exc))

    def _publish(self) -> None:
        committed = self._clean(self.transcriber.text)
        self._on_update(committed, self.transcriber.preview)

    def _clean(self, raw: str) -> str:
        from .postprocess import process

        if not raw:
            return ""
        result = self.controller.corrections_for(raw)
        return process(result.text, self.controller.postprocess_options(self.engine))

    def _result(self) -> LiveResult:
        raw = self.transcriber.text
        return LiveResult(
            text=self._clean(raw),
            raw=raw,
            segments=self.controller._clean_segments(
                self.transcriber.segments, self.controller.postprocess_options(self.engine)),
            seconds=self.transcriber.seconds,
            engine=self.engine.name,
            engine_label=getattr(self.engine, "label", self.engine.name),
        )

    def _transcribe(self, pcm: bytes) -> Tuple[str, List[Segment]]:
        return transcribe_pcm(self.controller, self.engine, pcm)


def transcribe_pcm(controller, engine, pcm: bytes) -> Tuple[str, List[Segment]]:
    """Hand one phrase of raw audio to an engine."""
    handle = tempfile.NamedTemporaryFile(prefix="aloud-live-", suffix=".wav", delete=False)
    handle.close()
    path = Path(handle.name)
    try:
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(BYTES_PER_SAMPLE)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(pcm)
        transcript = engine.transcribe(path, bias_terms=controller.bias_terms(engine))
        return transcript.text, list(transcript.segments)
    finally:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Hotkey dictation, transcribed while the key is held
# ---------------------------------------------------------------------------


class DictationStream:
    """Transcribes a hotkey dictation phrase by phrase while you speak.

    Without it, the wait after releasing the key grew with how long you
    talked: a minute of speech meant a minute of audio to transcribe after
    the fact. With it, each phrase is done at the pause after it, and release
    leaves only the last phrase -- the same speed-up Quick Dictate has, with
    no preview (nobody is watching one).

    The stream thread reads the recorder and commits phrases. On release the
    controller stops it without waiting (the hotkey callback must return at
    once); the dictation worker then calls :meth:`finish`, which waits for the
    thread, adds the audio it had not reached yet, and transcribes the rest.
    Any failure returns None, and the worker transcribes the WAV the old way.
    """

    TICK = 0.15

    def __init__(self, controller, recorder) -> None:
        self.controller = controller
        self.engine = controller.engine
        self.recorder = recorder
        self.transcriber = LiveTranscriber(
            lambda pcm: transcribe_pcm(controller, self.engine, pcm),
            pause=float(controller.config.get("dictation.pause_seconds", DEFAULT_PAUSE)),
            previews=False,
        )
        self.failed = False
        self._stop = threading.Event()
        self._cursor = 0
        self._thread = threading.Thread(target=self._run, name="aloud-dictation-live",
                                        daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.TICK):
                pcm, self._cursor = self.recorder.read_new(self._cursor)
                if pcm:
                    self.transcriber.feed(pcm)
                self.transcriber.step()
        except Exception:
            log.exception("Live dictation failed; the whole recording will be used")
            self.failed = True

    def stop(self) -> None:
        """Stop reading. Returns at once; safe from the hotkey callback."""
        self._stop.set()

    def finish(self, full_pcm: bytes, timeout: float = 120.0):
        """The finished transcript, or None to fall back to the whole WAV."""
        from .engines.base import Transcript

        self._stop.set()
        self._thread.join(timeout)
        if self.failed or self._thread.is_alive():
            return None
        started = time.monotonic()
        try:
            rest = full_pcm[len(self.transcriber.audio):]
            if rest:
                self.transcriber.feed(rest)
            self.transcriber.finish()
        except Exception:
            log.exception("Live dictation could not finish; using the whole recording")
            return None
        return Transcript(
            text=self.transcriber.text,
            engine=self.engine.name,
            duration=time.monotonic() - started,
            segments=self.transcriber.segments,
            meta={"mode": "live", "phrases": len(self.transcriber.pieces)},
        )
