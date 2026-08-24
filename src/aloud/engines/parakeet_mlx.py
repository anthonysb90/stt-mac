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

#: The only two files parakeet-mlx reads out of a model repository. Fetching
#: just these skips the tokenizer and card files in some of the repos.
MODEL_FILES = ("config.json", "model.safetensors")


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
        if self._model is not None:
            state = "loaded"
        elif Path(self._model_id()).expanduser().is_dir():
            state = "local directory, not loaded yet"
        elif self._cached():
            state = "downloaded, not loaded yet"
        else:
            state = "not downloaded yet — ~2.4 GB on first use"
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

    def _resolve(self, model_id: str) -> str:
        """A local directory holding the model, downloading it if needed.

        parakeet-mlx's ``from_pretrained`` wraps its Hugging Face download in a
        bare ``except Exception`` and falls back to reading the id as a path on
        disk. So *every* download failure -- no network, a 404, a gated repo, a
        full disk -- arrives as::

            [Errno 2] No such file or directory:
            'mlx-community/parakeet-tdt-0.6b-v3/config.json'

        which names neither the cause nor the fix, and reads like a bug in this
        app. Downloading first means the real exception is the one that reaches
        the user, and the path handed on is local so that fallback never fires.
        """
        path = Path(model_id).expanduser()
        if path.is_dir():
            return str(path)

        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise EngineError(
                "huggingface_hub is not installed, so the model cannot be "
                "downloaded. Run scripts/bootstrap.sh."
            ) from exc

        try:
            return str(snapshot_download(model_id, allow_patterns=list(MODEL_FILES)))
        except Exception as exc:
            raise EngineError(self._download_failure(model_id, exc)) from exc

    @staticmethod
    def _download_failure(model_id: str, exc: BaseException) -> str:
        """Turn a Hugging Face failure into something worth reading.

        Matching on the exception's class name rather than importing
        huggingface_hub's error types: they move between versions, and a broken
        import here would replace a bad message with a worse one.
        """
        kind = type(exc).__name__
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else kind

        if "RepositoryNotFound" in kind or "EntryNotFound" in kind or "404" in detail:
            return (
                f"Hugging Face has no model called {model_id!r}. Check "
                "engines.parakeet_mlx.model in config.json, or set it in "
                "Settings → Model."
            )
        if "GatedRepo" in kind or "401" in detail or "403" in detail:
            return (
                f"{model_id} is gated: accept its licence on huggingface.co "
                "and store a token with `huggingface-cli login`."
            )
        if "ConnectionError" in kind or "Timeout" in kind or "offline" in detail.lower():
            return (
                f"Could not reach Hugging Face to download {model_id}. The "
                "weights are ~2.4 GB and are only fetched once; check the "
                "network and try again."
            )
        if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
            return f"No disk space left to download {model_id} (~2.4 GB)."
        return f"Could not download {model_id}: {kind}: {detail}"

    def _cached(self) -> bool:
        """Whether the weights are already on disk. Never raises."""
        try:
            from huggingface_hub import snapshot_download

            snapshot_download(
                self._model_id(),
                allow_patterns=list(MODEL_FILES),
                local_files_only=True,
            )
            return True
        except Exception:
            return False

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

            # Fetched here rather than left to from_pretrained; see _resolve.
            source = self._resolve(model_id)
            try:
                # Absolute import: the third-party package, not this module.
                from parakeet_mlx import from_pretrained

                self._model = from_pretrained(source)
            except Exception as exc:
                self._load_error = f"Could not load {model_id}: {exc}"
                raise EngineError(self._load_error) from exc

            log.info("Parakeet ready in %.1fs", time.monotonic() - started)
            return self._model
