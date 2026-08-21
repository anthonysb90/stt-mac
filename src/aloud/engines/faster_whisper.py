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
from typing import Tuple

from .base import EngineError, Transcript, TranscriptionEngine

log = logging.getLogger(__name__)

DEFAULT_MODEL = "base.en"


class FasterWhisperEngine(TranscriptionEngine):
    name = "faster_whisper"
    label = "faster-whisper (local, CPU)"

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

    def warm_up(self) -> None:
        try:
            self._load()
        except EngineError as exc:
            log.warning("faster-whisper warm-up failed: %s", exc)

    def transcribe(self, wav_path: Path) -> Transcript:
        model = self._load()
        started = time.monotonic()
        language = str(self.options.get("language", "") or "") or None
        try:
            # `segments` is a generator; iterating it is what does the work.
            segments, info = model.transcribe(
                str(wav_path),
                language=language,
                beam_size=int(self.options.get("beam_size", 1)),
                vad_filter=bool(self.options.get("vad_filter", True)),
                condition_on_previous_text=False,  # avoids run-on hallucinations
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            raise EngineError(f"faster-whisper transcription failed: {exc}") from exc

        return Transcript(
            text=text,
            engine=self.name,
            duration=time.monotonic() - started,
            language=getattr(info, "language", "") or (language or ""),
            meta={"model": self._model_id()},
        )

    # -- internals ---------------------------------------------------------

    def _model_id(self) -> str:
        return str(self.options.get("model") or DEFAULT_MODEL)

    def _load(self):
        if self._model is not None:
            return self._model

        with self._load_lock:
            if self._model is not None:
                return self._model

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
