"""A backend that returns canned text.

Lets the hotkey -> record -> inject loop be exercised end to end without a
model, an API key, or a network connection.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Sequence, Tuple

from .base import Transcript, TranscriptionEngine


class MockEngine(TranscriptionEngine):
    name = "mock"
    label = "Mock (no model)"
    supports_bias = True

    def transcribe(self, wav_path: Path, *, bias_terms: Sequence[str] = ()) -> Transcript:
        started = time.monotonic()
        text = self.options.get("text", "This is mock transcription output.")
        return Transcript(
            text=text,
            engine=self.name,
            duration=time.monotonic() - started,
            meta={"wav": str(wav_path), "bias": list(bias_terms)},
        )

    def check(self) -> Tuple[bool, str]:
        return True, "Always available; returns fixed text."
