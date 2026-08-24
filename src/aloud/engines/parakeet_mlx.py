"""Local transcription with NVIDIA Parakeet via Apple's MLX framework.

Apple Silicon only, and the fastest option there by a wide margin — the TDT
architecture emits blank frames during silence instead of forcing a token, so
it avoids the hallucinated text Whisper produces on quiet audio.

Unlike the whisper.cpp engine, this runs **in-process** and keeps the model
resident. That removes the per-dictation model load, which was the single
largest term in the latency budget. The cost is a one-off warm-up at startup
and a few GB of RAM held for the life of the app.

Weights come from Hugging Face on first use (~2.4 GB) and are cached under
``~/.cache/huggingface``; ``aloud warm`` pre-fetches them.
"""

from __future__ import annotations

import array
import importlib.util
import logging
import platform
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence, Tuple

from .base import EngineError, Transcript, TranscriptionEngine

log = logging.getLogger(__name__)

#: 25 European languages. `parakeet-tdt-0.6b-v2` is English-only but smaller.
DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

MIN_PYTHON = (3, 10)

#: How much audio to hand the streaming decoder at a time, in seconds. Smaller
#: means the words on screen move more often and a little accuracy is lost at
#: the seams; larger is the reverse. Ten seconds keeps a long import visibly
#: alive without making the seams matter.
DEFAULT_STREAM_CHUNK_SECONDS = 10.0

#: Every sample is a 16-bit signed integer, so this maps to -1.0..1.0.
FULL_SCALE = 32768.0


class _StreamingUnavailable(RuntimeError):
    """The installed parakeet-mlx does not expose the streaming API we use.

    Raised only for a shape mismatch -- a missing module, a missing attribute,
    the wrong arguments -- never for a genuine decoding failure. It means "fall
    back to one pass", not "this file cannot be transcribed".
    """


def supported() -> bool:
    """MLX requires Apple Silicon; there is no x86_64 path."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


class ParakeetMLXEngine(TranscriptionEngine):
    name = "parakeet_mlx"
    label = "Parakeet · MLX (Apple Silicon)"
    #: Parakeet's TDT decoder has no prompt to condition on, so Dictionary
    #: biasing is a no-op here and the correction pass does all the work.
    supports_bias = False
    #: Through ``transcribe_stream``, which yields a transcript that grows as
    #: audio is fed in. Falls back to a single pass if the installed
    #: parakeet-mlx has no streaming API; see :meth:`_streamed`.
    supports_progress = True

    def __init__(self, options=None) -> None:
        super().__init__(options)
        self._model = None
        self._load_lock = threading.Lock()
        self._load_error: str = ""

    # -- contract ----------------------------------------------------------

    def check(self) -> Tuple[bool, str]:
        if not supported():
            return False, "Requires Apple Silicon; MLX has no Intel build."
        if sys.version_info < MIN_PYTHON:
            have = "%d.%d" % sys.version_info[:2]
            return False, f"Needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ (running {have})."
        if importlib.util.find_spec("parakeet_mlx") is None:
            return False, "parakeet-mlx is not installed. Run scripts/bootstrap.sh."
        if not self._ffmpeg():
            return False, (
                "ffmpeg not found (parakeet-mlx decodes audio with it). "
                "Install it with `brew install ffmpeg`. If it is already "
                "installed, its directory is missing from PATH — an app "
                "launched from the Dock does not inherit your shell — so add "
                "it under tools.path_extra in config.json."
            )
        if self._load_error:
            return False, self._load_error
        state = "loaded" if self._model is not None else "not loaded yet"
        return True, f"{self._model_id()} ({state})"

    def warm_up(self) -> None:
        """Load the model ahead of the first dictation, downloading if needed."""
        try:
            self._load()
        except EngineError as exc:
            log.warning("Parakeet warm-up failed: %s", exc)

    def transcribe(
        self,
        wav_path: Path,
        *,
        bias_terms: Sequence[str] = (),
        on_progress: Optional[Callable[[str, float, float], bool]] = None,
    ) -> Transcript:
        """One pass for dictation, streamed when someone is watching.

        Dictation is a few seconds long and nobody reads a progress bar for it,
        so it takes the single-pass path, which is the most accurate thing this
        model can do. An imported file is the opposite: it can run for minutes,
        and a bar with no words next to it is indistinguishable from a hang. So
        when ``on_progress`` is supplied the audio is fed through the streaming
        decoder instead and the transcript is reported as it forms.
        """
        del bias_terms  # unsupported by this decoder; see `supports_bias`
        model = self._load()
        started = time.monotonic()
        mode = "whole"

        if on_progress is None:
            text = self._whole(model, wav_path)
        else:
            try:
                text = self._streamed(model, wav_path, on_progress)
                mode = "streamed"
            except _StreamingUnavailable as exc:
                # Never a reason to fail the transcription: the one-pass call
                # is the same call this engine has always made. The user loses
                # the live words, not the result.
                log.info("Parakeet streaming unavailable (%s); using one pass", exc)
                on_progress("", 0.0, 0.0)
                text = self._whole(model, wav_path)

        return Transcript(
            text=text.strip(),
            engine=self.name,
            duration=time.monotonic() - started,
            language=str(self.options.get("language", "")),
            meta={"model": self._model_id(), "mode": mode},
        )

    # -- decoding ----------------------------------------------------------

    def _whole(self, model, wav_path: Path) -> str:
        try:
            result = model.transcribe(str(wav_path))
        except Exception as exc:
            raise EngineError(f"Parakeet transcription failed: {exc}") from exc
        return str(getattr(result, "text", "") or "")

    def _streamed(self, model, wav_path: Path, on_progress) -> str:
        """Feed the file through ``transcribe_stream`` a chunk at a time.

        Anything that says the installed parakeet-mlx is not shaped the way
        this expects -- no mlx, no ``transcribe_stream``, different arguments
        -- becomes :class:`_StreamingUnavailable` so the caller can fall back.
        A decoding failure is a real error and is left to propagate.
        """
        try:
            import mlx.core as mx
        except ImportError as exc:  # pragma: no cover - needs Apple Silicon
            raise _StreamingUnavailable(f"mlx is not importable: {exc}") from exc

        samples, rate = self._read_wav(wav_path)
        total = len(samples) / float(rate) if rate else 0.0
        step = max(1, int(rate * self._chunk_seconds()))
        text = ""

        try:
            stream_context = model.transcribe_stream()
        except (AttributeError, TypeError) as exc:  # pragma: no cover
            raise _StreamingUnavailable(f"transcribe_stream: {exc}") from exc

        try:
            with stream_context as stream:
                for index, chunk in enumerate(self._chunks(samples, step), start=1):
                    try:
                        stream.add_audio(mx.array(chunk))
                    except (AttributeError, TypeError) as exc:  # pragma: no cover
                        raise _StreamingUnavailable(f"add_audio: {exc}") from exc
                    text = self._result_text(stream)
                    done = min(total, index * step / float(rate)) if rate else 0.0
                    if not on_progress(text, done, total):
                        log.info("Transcription cancelled after %.1fs of audio", done)
                        break
        except _StreamingUnavailable:
            raise
        except Exception as exc:
            raise EngineError(f"Parakeet transcription failed: {exc}") from exc

        return text

    @staticmethod
    def _result_text(stream) -> str:
        """The transcript so far. Older builds hand back a bare string."""
        try:
            result = stream.result
        except AttributeError as exc:  # pragma: no cover
            raise _StreamingUnavailable(f"stream.result: {exc}") from exc
        if isinstance(result, str):
            return result
        return str(getattr(result, "text", "") or "")

    def _chunk_seconds(self) -> float:
        try:
            value = float(
                self.options.get("stream_chunk_seconds", DEFAULT_STREAM_CHUNK_SECONDS)
            )
        except (TypeError, ValueError):
            return DEFAULT_STREAM_CHUNK_SECONDS
        return value if value > 0 else DEFAULT_STREAM_CHUNK_SECONDS

    @staticmethod
    def _chunks(samples, step: int) -> Iterator[list]:
        for start in range(0, len(samples), step):
            yield samples[start:start + step]

    @staticmethod
    def _read_wav(wav_path: Path):
        """16 kHz mono float samples in -1.0..1.0, plus the sample rate.

        Everything reaching an engine has been through ``media.prepare``, which
        makes 16-bit mono WAV, so this does not need to be a general decoder --
        but it says so out loud rather than misreading a file it was not given.
        """
        try:
            with wave.open(str(wav_path), "rb") as handle:
                channels = handle.getnchannels()
                width = handle.getsampwidth()
                rate = handle.getframerate()
                frames = handle.readframes(handle.getnframes())
        except (OSError, wave.Error) as exc:
            raise _StreamingUnavailable(f"cannot read {wav_path.name}: {exc}") from exc

        if width != 2 or channels != 1:
            raise _StreamingUnavailable(
                f"expected 16-bit mono, got {width * 8}-bit {channels}-channel"
            )

        pcm = array.array("h")
        pcm.frombytes(frames)
        if sys.byteorder == "big":  # pragma: no cover - WAV is little-endian
            pcm.byteswap()
        return [sample / FULL_SCALE for sample in pcm], rate

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _ffmpeg() -> Optional[str]:
        """Where ffmpeg is, or None.

        Deliberately not a bare ``shutil.which``. That is what this used to be,
        and it reported "not found" inside the app on a machine where the
        terminal found it immediately: a Dock launch gets launchd's PATH, which
        has no Homebrew prefix on it. ``media.ffmpeg_path`` looks in the
        prefixes as well, and :mod:`aloud.toolpath` puts them on PATH at
        startup so parakeet-mlx's own subprocess call finds it too.
        """
        from ..media import ffmpeg_path

        return ffmpeg_path()

    def _model_id(self) -> str:
        return str(self.options.get("model") or DEFAULT_MODEL)

    def _load(self):
        """Load the model once. Safe to call from several threads."""
        if self._model is not None:
            return self._model

        with self._load_lock:
            if self._model is not None:
                return self._model

            ok, detail = self.check()
            if not ok:
                raise EngineError(detail)

            model_id = self._model_id()
            log.info("Loading Parakeet model %s (first run downloads it)", model_id)
            started = time.monotonic()
            try:
                # Absolute import: the third-party package, not this module.
                from parakeet_mlx import from_pretrained

                self._model = from_pretrained(model_id)
            except Exception as exc:
                self._load_error = f"Could not load {model_id}: {exc}"
                raise EngineError(self._load_error) from exc

            log.info("Parakeet ready in %.1fs", time.monotonic() - started)
            return self._model
