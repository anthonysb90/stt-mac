"""Transcription backends, and the logic that picks one per machine.

Aloud targets two very different Macs, so "which engine" is a per-machine
question rather than a global setting. ``engine: "auto"`` in the config asks
this module to answer it:

* **Apple Silicon** → Parakeet on MLX. Fastest by a wide margin, and its TDT
  decoder does not hallucinate over silence the way Whisper does.
* **Intel** → faster-whisper on CPU. MLX has no x86_64 build at all, and
  CTranslate2's int8 kernels are the best CPU-only option available.

Both keep the model resident in-process. whisper.cpp stays as the last resort:
it needs no Python ML stack, so it is what still works when the preferred
engine has not been installed yet.

The cloud and mock engines are never selected automatically — sending audio off
the machine has to be a deliberate choice.
"""

from __future__ import annotations

import logging
import platform
import sys
from typing import Any, Dict, List, Type

from .base import EngineError, Transcript, TranscriptionEngine
from .faster_whisper import FasterWhisperEngine
from .mock import MockEngine
from .openai_api import OpenAIEngine
from .parakeet_mlx import ParakeetMLXEngine
from .whisper_cpp import WhisperCppEngine

log = logging.getLogger(__name__)

REGISTRY: Dict[str, Type[TranscriptionEngine]] = {
    engine.name: engine
    for engine in (
        ParakeetMLXEngine,
        FasterWhisperEngine,
        WhisperCppEngine,
        OpenAIEngine,
        MockEngine,
    )
}

AUTO = "auto"

#: Preference order for ``engine: "auto"``, best first, per architecture.
AUTO_PREFERENCE: Dict[str, List[str]] = {
    "arm64": [ParakeetMLXEngine.name, FasterWhisperEngine.name, WhisperCppEngine.name],
    "x86_64": [FasterWhisperEngine.name, WhisperCppEngine.name],
}

DEFAULT_ENGINE = WhisperCppEngine.name


def preferences() -> List[str]:
    """The auto-selection order for this machine, best first."""
    if sys.platform != "darwin":
        return [WhisperCppEngine.name]
    return AUTO_PREFERENCE.get(platform.machine(), [WhisperCppEngine.name])


def resolve(name: str) -> str:
    """Turn a configured engine name into a concrete one, without checking it."""
    if name != AUTO:
        return name
    order = preferences()
    return order[0] if order else DEFAULT_ENGINE


def build(name: str, options: Dict[str, Any] | None = None) -> TranscriptionEngine:
    """Instantiate an engine by name. ``auto`` resolves by architecture."""
    resolved = resolve(name)
    engine_class = REGISTRY.get(resolved)
    if engine_class is None:
        raise EngineError(
            f"Unknown engine {name!r}. Available: {', '.join(sorted(REGISTRY))}, {AUTO}"
        )
    return engine_class(options or {})


def select(config) -> TranscriptionEngine:
    """Build the engine the config asks for, falling back when it isn't ready.

    An explicitly named engine is always honoured, even when broken — the menu
    bar surfaces the reason, and silently substituting one would be worse than
    saying so. Only ``auto`` walks the preference list, which is what lets a
    machine that has not finished bootstrapping still dictate through
    whisper.cpp.
    """
    configured = str(config.get("engine", AUTO))
    if configured != AUTO:
        return build(configured, config.engine_options(configured))

    attempted = []
    for name in preferences():
        engine = build(name, config.engine_options(name))
        ok, detail = engine.check()
        if ok:
            if attempted:
                log.info("auto: chose %s (skipped %s)", name, ", ".join(attempted))
            return engine
        attempted.append(f"{name}: {detail}")

    # Nothing is ready. Return the preferred engine anyway so the UI can
    # explain what to install rather than pretending there is no engine.
    fallback = resolve(AUTO)
    log.warning("auto: no engine is ready (%s)", "; ".join(attempted))
    return build(fallback, config.engine_options(fallback))


def names() -> list[str]:
    return list(REGISTRY)


__all__ = [
    "AUTO",
    "AUTO_PREFERENCE",
    "DEFAULT_ENGINE",
    "EngineError",
    "REGISTRY",
    "Transcript",
    "TranscriptionEngine",
    "build",
    "names",
    "preferences",
    "resolve",
    "select",
]
