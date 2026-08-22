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

import importlib.util
import logging
import platform
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

from .base import EngineError, Transcript, TranscriptionEngine

log = logging.getLogger(__name__)

#: 25 European languages. `parakeet-tdt-0.6b-v2` is English-only but smaller.
DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v3"

MIN_PYTHON = (3, 10)


def supported() -> bool:
    """MLX requires Apple Silicon; there is no x86_64 path."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


class ParakeetMLXEngine(TranscriptionEngine):
    name = "parakeet_mlx"
    label = "Parakeet · MLX (Apple Silicon)"
    #: Parakeet's TDT decoder has no prompt to condition on, so Dictionary
    #: biasing is a no-op here and the correction pass does all the work.
    supports_bias = False

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
        if shutil.which("ffmpeg") is None:
            return False, "ffmpeg not found (parakeet-mlx decodes audio with it). brew install ffmpeg"
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
        del bias_terms  # unsupported by this decoder; see `supports_bias`
        model = self._load()
        started = time.monotonic()
        try:
            result = model.transcribe(str(wav_path))
        except Exception as exc:
            raise EngineError(f"Parakeet transcription failed: {exc}") from exc

        text = getattr(result, "text", "") or ""
        return Transcript(
            text=text.strip(),
            engine=self.name,
            duration=time.monotonic() - started,
            language=str(self.options.get("language", "")),
            meta={"model": self._model_id()},
        )

    # -- internals ---------------------------------------------------------

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
