"""Configuration: layered defaults, user overrides, and live reload.

The config file is plain JSON at ``~/Library/Application Support/Aloud/config.json``.
Anything the user omits falls back to ``DEFAULTS``, so a partial file is always
valid and new options gain sensible values on upgrade.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict

from .paths import CONFIG_FILE, ensure_dirs

log = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    "hotkey": {
        # "hold"   -> record while the key is down (true push-to-talk)
        # "toggle" -> tap once to start, tap again to stop (hands-free)
        "mode": "hold",
        # See aloud.hotkey.MODIFIER_KEYS for the full list. "right_option" is
        # the default because, unlike "fn", it has no built-in system action to
        # collide with.
        "key": "right_option",
    },
    "engine": "whisper_cpp",
    "engines": {
        "whisper_cpp": {
            # Leave blank to auto-discover: config -> $PATH -> vendored build.
            "binary": "",
            # Leave blank to pick the newest *.bin under the models directory.
            "model": "",
            "language": "en",
            # 0 -> let the engine choose based on core count.
            "threads": 0,
            "extra_args": [],
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "model": "whisper-1",
            "api_key_env": "OPENAI_API_KEY",
            "language": "en",
            "timeout": 30,
        },
        "mock": {"text": "This is mock transcription output."},
    },
    "audio": {
        "sample_rate": 16000,
        "channels": 1,
        # null -> system default input device; otherwise an index or name.
        "device": None,
        "max_seconds": 300,
        # Ignore accidental taps of the hotkey.
        "min_seconds": 0.35,
    },
    "output": {
        # "paste" -> pasteboard + synthetic Cmd-V (fast, works nearly everywhere)
        # "type"  -> synthesize the characters directly (slower, no clipboard use)
        # "clipboard" -> copy only, do not deliver keystrokes
        "mode": "paste",
        "restore_clipboard": True,
        "restore_delay": 0.8,
        "trailing_space": True,
    },
    "postprocess": {
        "strip_fillers": True,
        "fillers": ["um", "uh", "erm", "hmm", "mhm", "you know"],
        "capitalize_first": True,
        "collapse_whitespace": True,
        # Literal replacements applied after transcription, case-insensitive on
        # the key. Useful for names and jargon Whisper keeps getting wrong.
        "dictionary": {},
        # Spoken phrases replaced with literal characters.
        "commands": {
            "new line": "\n",
            "new paragraph": "\n\n",
        },
    },
    "feedback": {
        "sounds": True,
        "start_sound": "Tink",
        "stop_sound": "Pop",
        "error_sound": "Basso",
        "notify_on_error": True,
    },
    "history": {"enabled": True, "max_entries": 500},
    "logging": {"level": "INFO"},
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively overlay ``override`` onto a copy of ``base``."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """A read-mostly view of the merged configuration."""

    def __init__(self, data: Dict[str, Any] | None = None) -> None:
        self._data = data if data is not None else copy.deepcopy(DEFAULTS)

    @classmethod
    def load(cls) -> "Config":
        ensure_dirs()
        if not CONFIG_FILE.exists():
            cls(copy.deepcopy(DEFAULTS)).save()
            return cls(copy.deepcopy(DEFAULTS))
        try:
            user = json.loads(CONFIG_FILE.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read %s (%s); using defaults", CONFIG_FILE, exc)
            return cls(copy.deepcopy(DEFAULTS))
        if not isinstance(user, dict):
            log.warning("%s is not a JSON object; using defaults", CONFIG_FILE)
            return cls(copy.deepcopy(DEFAULTS))
        return cls(_deep_merge(DEFAULTS, user))

    def save(self) -> None:
        ensure_dirs()
        CONFIG_FILE.write_text(json.dumps(self._data, indent=2) + "\n")

    def get(self, path: str, default: Any = None) -> Any:
        """Fetch a nested value with a dotted path, e.g. ``audio.sample_rate``."""
        node: Any = self._data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any) -> None:
        parts = path.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def engine_options(self, name: str | None = None) -> Dict[str, Any]:
        name = name or self.get("engine", "whisper_cpp")
        options = self.get(f"engines.{name}", {})
        return dict(options) if isinstance(options, dict) else {}

    @property
    def data(self) -> Dict[str, Any]:
        return self._data
