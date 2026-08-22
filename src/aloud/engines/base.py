"""The contract every transcription backend implements."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple


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
    #: Whether transcribe() can report partial results while it works. Long
    #: files are unbearable without it -- a progress bar you cannot see moving
    #: is indistinguishable from a hang.
    supports_progress: bool = False
    #: Whether this backend needs an API key. Drives the credentials field in
    #: Settings, so a key can be pasted in rather than stored from a terminal.
    needs_api_key: bool = False
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
    def transcribe(
        self,
        wav_path: Path,
        *,
        bias_terms: Sequence[str] = (),
        on_progress: Optional[Callable[[str, float, float], bool]] = None,
    ) -> Transcript:
        """Turn a 16 kHz mono WAV file into text.

        ``bias_terms`` is a short vocabulary list from the Dictionary. Backends
        that cannot use it ignore it; none may fail because of it.

        ``on_progress(text_so_far, seconds_done, total_seconds)`` is called as
        the transcript accumulates, for backends that decode incrementally.
        Returning ``False`` from it asks the backend to stop early, which is
        how cancelling a long import works. ``total_seconds`` is 0.0 when the
        length is unknown, meaning the bar should be indeterminate.
        """

    @abc.abstractmethod
    def check(self) -> Tuple[bool, str]:
        """Report readiness as ``(ok, human-readable detail)``.

        Called at startup and whenever the user opens the status menu, so it
        must be cheap and must not raise.
        """

    def warm_up(self) -> None:
        """Optional hook to pay one-off setup costs before the first use."""

    @property
    def api_key_env(self) -> str:
        """The environment variable this backend reads, if any."""
        return str(self.options.get("api_key_env", ""))
