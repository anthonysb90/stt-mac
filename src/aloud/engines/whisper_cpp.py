"""Local transcription via the whisper.cpp CLI — the offline fallback.

Runs on both architectures (Metal + Neural Engine on Apple Silicon, AVX and
Accelerate on Intel) and, uniquely among the local engines, needs no Python ML
stack at all: just a binary and a model file. That is what makes it the last
resort in the `auto` chain — it is the engine that still works on a machine
where the preferred one has not been installed yet.

It is a fallback rather than a default because shelling out to a CLI means
paying a process spawn *and* a full model load on every single dictation. The
resident engines (`parakeet_mlx`, `faster_whisper`) pay neither.

The trade is deliberate: no compile step, no ML dependency, and a crash inside
the model cannot take the UI down with it.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from ..corrections import bias_prompt
from ..paths import MODELS_DIR, VENDOR_DIR
from .base import EngineError, Transcript, TranscriptionEngine

log = logging.getLogger(__name__)

#: Binary names, newest first. Upstream renamed `main` to `whisper-cli` in 2024.
BINARY_NAMES = ("whisper-cli", "whisper-cpp", "main")

#: Extra places to look beyond $PATH.
SEARCH_DIRS = (
    VENDOR_DIR / "whisper.cpp" / "build" / "bin",
    VENDOR_DIR / "whisper.cpp",
    Path("/opt/homebrew/bin"),  # Apple Silicon Homebrew
    Path("/usr/local/bin"),  # Intel Homebrew
)

#: whisper.cpp prefixes each segment with a timestamp unless -nt is passed;
#: strip any that survive (older builds ignore the flag in some modes).
_TIMESTAMP_RE = re.compile(r"^\s*\[[\d:.\s\->]+\]\s*")

#: Non-speech annotations Whisper emits for silence, music, and noise.
_ANNOTATION_RE = re.compile(r"[\(\[][A-Z_ ]{2,}[\)\]]|\*[a-z ]+\*")


def find_binary(configured: str = "") -> Optional[Path]:
    """Locate the whisper.cpp CLI: config value, then $PATH, then known dirs."""
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None

    for name in BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)

    for directory in SEARCH_DIRS:
        for name in BINARY_NAMES:
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def find_model(configured: str = "") -> Optional[Path]:
    """Locate a ggml model file, preferring the configured path."""
    if configured:
        candidate = Path(configured).expanduser()
        return candidate if candidate.is_file() else None

    if not MODELS_DIR.is_dir():
        return None
    models = sorted(
        MODELS_DIR.glob("ggml-*.bin"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return models[0] if models else None


def clean_output(raw: str) -> str:
    """Strip timestamps and non-speech annotations from CLI output."""
    lines = []
    for line in raw.splitlines():
        line = _TIMESTAMP_RE.sub("", line)
        line = _ANNOTATION_RE.sub("", line)
        line = line.strip()
        if line:
            lines.append(line)
    return " ".join(lines).strip()


class WhisperCppEngine(TranscriptionEngine):
    name = "whisper_cpp"
    label = "whisper.cpp (local)"
    supports_bias = True

    def __init__(self, options=None) -> None:
        super().__init__(options)
        self.binary = find_binary(self.options.get("binary", ""))
        self.model = find_model(self.options.get("model", ""))

    # -- contract ----------------------------------------------------------

    def check(self) -> Tuple[bool, str]:
        # Re-resolve each time: the user may have installed things since startup.
        self.binary = find_binary(self.options.get("binary", ""))
        self.model = find_model(self.options.get("model", ""))
        if self.binary is None:
            return False, "whisper-cli not found. Run scripts/bootstrap.sh --with-whisper-cpp."
        if self.model is None:
            return False, f"No ggml-*.bin model in {MODELS_DIR}. Run scripts/fetch_model.sh base.en."
        return True, f"{self.binary.name} + {self.model.name}"

    def transcribe(
        self,
        wav_path: Path,
        *,
        bias_terms: Sequence[str] = (),
        on_progress: Optional[Callable[[str, float, float], bool]] = None,
    ) -> Transcript:
        ok, detail = self.check()
        if not ok:
            raise EngineError(detail)

        command = self._build_command(wav_path, bias_terms)
        log.debug("Running: %s", " ".join(command))
        started = time.monotonic()
        timeout = self._timeout_for(wav_path)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise EngineError(
                f"whisper.cpp took longer than {timeout:.0f}s and was stopped"
            ) from exc
        except OSError as exc:
            raise EngineError(f"Could not run {self.binary}: {exc}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else f"exit code {completed.returncode}"
            raise EngineError(f"whisper.cpp failed: {tail}")

        return Transcript(
            text=clean_output(completed.stdout),
            engine=self.name,
            duration=time.monotonic() - started,
            language=self.options.get("language", "en"),
            meta={"model": self.model.name if self.model else ""},
        )

    # -- internals ---------------------------------------------------------

    def _timeout_for(self, wav_path: Path) -> float:
        """A ceiling scaled to the audio, not a flat 120 seconds.

        The flat value was tuned for dictation and killed legitimate long
        imports: an hour of audio on CPU takes far more than two minutes while
        making steady progress. Allow generous multiples of real time -- this
        exists to catch a hang, not to police slowness -- and keep a floor so
        a three-second dictation is not timed out by model-load overhead.
        An explicit config value still wins.
        """
        configured = self.options.get("timeout")
        if configured:
            return float(configured)
        try:
            from ..audio import wav_duration

            seconds = wav_duration(wav_path)
        except Exception:
            seconds = 0.0
        return max(120.0, seconds * 4.0)

    def _build_command(self, wav_path: Path, bias_terms: Sequence[str] = ()) -> List[str]:
        assert self.binary is not None and self.model is not None
        command = [
            str(self.binary),
            "-m", str(self.model),
            "-f", str(wav_path),
            "--no-timestamps",
            "--no-prints",
        ]
        language = self.options.get("language", "en")
        if language:
            command += ["-l", language]
        threads = int(self.options.get("threads", 0) or 0)
        if threads > 0:
            command += ["-t", str(threads)]
        prompt = bias_prompt(bias_terms)
        if prompt:
            command += ["--prompt", prompt]
        extra = self.options.get("extra_args") or []
        command += [str(arg) for arg in extra]
        return command
