"""The contract every transcription backend implements."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Tuple


@dataclass
class Transcript:
    """The result of one transcription."""

    text: str
    engine: str
    duration: float = 0.0  # wall-clock seconds spent transcribing
    language: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


class EngineError(RuntimeError):
    """Raised when a backend cannot produce a transcript."""


class TranscriptionEngine(abc.ABC):
    """A speech-to-text backend.

    Implementations must be safe to call from a worker thread and should never
    block indefinitely -- the UI reflects their state in real time.
    """

    #: Stable identifier used in config and the menu bar.
    name: str = "base"
    #: Shown in the UI.
    label: str = "Base"

    def __init__(self, options: Dict[str, Any] | None = None) -> None:
        self.options = options or {}

    @abc.abstractmethod
    def transcribe(self, wav_path: Path) -> Transcript:
        """Turn a 16 kHz mono WAV file into text."""

    @abc.abstractmethod
    def check(self) -> Tuple[bool, str]:
        """Report readiness as ``(ok, human-readable detail)``.

        Called at startup and whenever the user opens the status menu, so it
        must be cheap and must not raise.
        """

    def warm_up(self) -> None:
        """Optional hook to pay one-off setup costs before the first use."""
