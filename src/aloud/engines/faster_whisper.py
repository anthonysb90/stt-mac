"""Local transcription with faster-whisper (CTranslate2).

This is the Intel answer. Like the Parakeet engine it runs **in-process** and
keeps the model resident, so the Intel Mac gets the same no-cold-start
behaviour as the M1 — which matters more there, since there is no GPU to make
up the difference. CTranslate2's int8 CPU kernels are well tuned for AVX2, and
it ships x86_64 wheels, so nothing has to be compiled.

It works on Apple Silicon too (it just won't beat Parakeet), which makes it a
useful second opinion when a transcript looks wrong.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

from ..corrections import bias_prompt
from .base import EngineError, Segment, Transcript, TranscriptionEngine

log = logging.getLogger(__name__)

DEFAULT_MODEL = "base.en"


class FasterWhisperEngine(TranscriptionEngine):
    name = "faster_whisper"
    label = "faster-whisper (local, CPU)"
    supports_bias = True
    #: `segments` is a generator — iterating it *is* the decoding, so the
    #: partial transcript and the position in the audio are free.
    supports_progress = True

    def __init__(self, options=None) -> None:
        super().__init__(options)
        self._model = None
        self._load_lock = threading.Lock()
        self._load_error: str = ""

    # -- contract ----------------------------------------------------------

    def check(self) -> Tuple[bool, str]:
        if importlib.util.find_spec("faster_whisper") is None:
            return False, "faster-whisper is not installed. Run scripts/bootstrap.sh."
        if self._load_error:
            return False, self._load_error
        state = "loaded" if self._model is not None else "not loaded yet"
        return True, f"{self._model_id()} · {self.options.get('compute_type', 'int8')} ({state})"

    def close(self) -> None:
        """Drop the model. A transcription in progress holds its own reference."""
        self._model = None
        self._pipeline = None
        self._pipeline_model = None

    def warm_up(self) -> None:
        try:
            self._load()
        except EngineError as exc:
            log.warning("faster-whisper warm-up failed: %s", exc)

    def transcribe(
        self,
        wav_path: Path,
        *,
        bias_terms: Sequence[str] = (),
        on_progress: Optional[Callable[[str, float, float], bool]] = None,
    ) -> Transcript:
        model = self._load()
        started = time.monotonic()
        language = str(self.options.get("language", "") or "") or None
        prompt = bias_prompt(bias_terms) or None
        audio = _samples(wav_path)
        try:
            # `segments` is a generator; iterating it is what does the work.
            segments, info = self._run(model, audio if audio is not None else str(wav_path),
                                       language, prompt, batched=on_progress is not None)
            total = float(getattr(info, "duration", 0.0) or 0.0)
            pieces = []
            timed = []
            for segment in segments:
                piece = segment.text.strip()
                if piece:
                    pieces.append(piece)
                    timed.append(Segment(
                        float(getattr(segment, "start", 0.0) or 0.0),
                        float(getattr(segment, "end", 0.0) or 0.0),
                        piece,
                    ))
                if on_progress is not None:
                    keep_going = on_progress(
                        " ".join(pieces), float(getattr(segment, "end", 0.0) or 0.0), total
                    )
                    if not keep_going:
                        log.info("Transcription cancelled after %.1fs of audio",
                                 getattr(segment, "end", 0.0))
                        break
            text = " ".join(pieces).strip()
        except Exception as exc:
            raise EngineError(f"faster-whisper transcription failed: {exc}") from exc

        return Transcript(
            text=text,
            engine=self.name,
            duration=time.monotonic() - started,
            language=getattr(info, "language", "") or (language or ""),
            meta={"model": self._model_id()},
            segments=timed,
        )

    # -- internals ---------------------------------------------------------

    def _run(self, model, audio, language, prompt, batched: bool):
        """Sequential for dictation; batched for files when available.

        faster-whisper's BatchedInferencePipeline splits long audio at pauses
        and decodes several pieces at once -- several times faster on a long
        recording on CPU, which is the Intel Mac's whole situation. Dictation
        is one short piece, where batching has nothing to batch.
        """
        options = dict(
            language=language,
            beam_size=int(self.options.get("beam_size", 1)),
            initial_prompt=prompt,
        )
        if batched and self.options.get("batched", True):
            pipeline = self._batched_pipeline(model)
            if pipeline is not None:
                try:
                    return pipeline.transcribe(
                        audio, batch_size=int(self.options.get("batch_size", 8)), **options)
                except TypeError:
                    log.info("This faster-whisper's batched mode takes other options; "
                             "transcribing sequentially")
        return model.transcribe(
            audio,
            vad_filter=bool(self.options.get("vad_filter", True)),
            condition_on_previous_text=False,  # avoids run-on hallucinations
            **options,
        )

    def _batched_pipeline(self, model):
        if getattr(self, "_pipeline", None) is not None and self._pipeline_model is model:
            return self._pipeline
        try:
            from faster_whisper import BatchedInferencePipeline
        except ImportError:  # faster-whisper older than 1.1
            return None
        self._pipeline = BatchedInferencePipeline(model=model)
        self._pipeline_model = model
        return self._pipeline

    def _model_id(self) -> str:
        return str(self.options.get("model") or DEFAULT_MODEL)

    def _load(self):
        if self._model is not None:
            return self._model

        with self._load_lock:
            if self._model is not None:
                return self._model

            # A previous failure is a report, not a verdict. Left in place it
            # made check() refuse forever: one launch with no network bricked
            # the engine until the app was restarted, even after the network
            # came back. Each attempt starts clean and re-diagnoses.
            self._load_error = ""

            ok, detail = self.check()
            if not ok:
                raise EngineError(detail)

            model_id = self._model_id()
            log.info("Loading faster-whisper model %s (first run downloads it)", model_id)
            started = time.monotonic()
            try:
                # Absolute import: the third-party package, not this module.
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    model_id,
                    device=str(self.options.get("device", "cpu")),
                    compute_type=str(self.options.get("compute_type", "int8")),
                    cpu_threads=int(self.options.get("threads", 0) or 0),
                )
            except Exception as exc:
                self._load_error = f"Could not load {model_id}: {exc}"
                raise EngineError(self._load_error) from exc

            log.info("faster-whisper ready in %.1fs", time.monotonic() - started)
            return self._model


def _samples(wav_path: Path):
    """16 kHz mono WAV as float32 samples, skipping faster-whisper's decoder.

    Every dictation is already in exactly the format the model wants, so
    decoding it again through PyAV is wasted time. Anything else -- another
    rate, stereo, not a WAV -- returns None and goes through the path.
    """
    try:
        import wave

        import numpy as np

        with wave.open(str(wav_path), "rb") as handle:
            if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (16000, 1, 2):
                return None
            frames = handle.readframes(handle.getnframes())
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    except Exception:
        return None
