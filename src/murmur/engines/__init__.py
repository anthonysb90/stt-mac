"""Transcription backends and the registry that selects between them."""

from __future__ import annotations

from typing import Any, Dict, Type

from .base import EngineError, Transcript, TranscriptionEngine
from .mock import MockEngine
from .openai_api import OpenAIEngine
from .whisper_cpp import WhisperCppEngine

REGISTRY: Dict[str, Type[TranscriptionEngine]] = {
    engine.name: engine for engine in (WhisperCppEngine, OpenAIEngine, MockEngine)
}

DEFAULT_ENGINE = WhisperCppEngine.name


def build(name: str, options: Dict[str, Any] | None = None) -> TranscriptionEngine:
    """Instantiate an engine by name, falling back to the default."""
    engine_class = REGISTRY.get(name)
    if engine_class is None:
        raise EngineError(
            f"Unknown engine {name!r}. Available: {', '.join(sorted(REGISTRY))}"
        )
    return engine_class(options or {})


def names() -> list[str]:
    return list(REGISTRY)


__all__ = [
    "REGISTRY",
    "DEFAULT_ENGINE",
    "EngineError",
    "Transcript",
    "TranscriptionEngine",
    "build",
    "names",
]
