"""The contract every transcription backend implements."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple


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
    #: Whether the backend already punctuates, capitalises, strips fillers and
    #: honours spoken punctuation. When true the local post-processing steps
    #: that would duplicate that work are skipped.
    handles_cleanup: bool = False
    #: Whether this backend can be primed with vocabulary before decoding.
    #: Whisper-family models take an initial prompt; Parakeet's CTC/TDT decoder
    #: has nowhere to put one. The Dictionary's correction pass is what covers
    #: the difference, which is why it is not optional.
    supports_bias: bool = False

    def __init__(self, options: Dict[str, Any] | None = None) -> None:
        self.options = options or {}

    @abc.abstractmethod
    def transcribe(self, wav_path: Path, *, bias_terms: Sequence[str] = ()) -> Transcript:
        """Turn a 16 kHz mono WAV file into text.

        ``bias_terms`` is a short vocabulary list from the Dictionary. Backends
        that cannot use it ignore it; none may fail because of it.
        """

    @abc.abstractmethod
    def check(self) -> Tuple[bool, str]:
        """Report readiness as ``(ok, human-readable detail)``.

        Called at startup and whenever the user opens the status menu, so it
        must be cheap and must not raise.
        """

    def warm_up(self) -> None:
        """Optional hook to pay one-off setup costs before the first use."""
