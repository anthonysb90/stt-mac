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

#: Bumped when a stored config needs adjusting rather than merely extending.
#: Adding a key never needs this -- anything absent falls back to a default.
#: It is for the other case: a default that was *wrong* and has to be corrected
#: in files that already exist.
SCHEMA_VERSION = 2

#: (path, old default, new default). Applied only when the stored value still
#: equals the old default, which is the closest we can get to "the user never
#: chose this". A value they picked themselves is never overwritten.
MIGRATIONS = {
    2: [
        # "Pop" is a hollow thunk and read as an error rather than as "done".
        ("feedback.start_sound", "Tink", "Bottle"),
        ("feedback.stop_sound", "Pop", "Glass"),
    ],
}

DEFAULTS: Dict[str, Any] = {
    "version": SCHEMA_VERSION,
    "hotkey": {
        # "hold"   -> record while the key is down (true push-to-talk)
        # "toggle" -> tap once to start, tap again to stop (hands-free)
        "mode": "hold",
        # See aloud.hotkey.MODIFIER_KEYS for the full list. "right_option" is
        # the default because, unlike "fn", it has no built-in system action to
        # collide with.
        "key": "right_option",
    },
    # "auto" picks the best engine for this machine: Parakeet on Apple
    # Silicon, faster-whisper on Intel. Name one explicitly to override.
    "engine": "auto",
    "engines": {
        "parakeet_mlx": {
            # Apple Silicon only. Weights are fetched from Hugging Face on
            # first use and cached under ~/.cache/huggingface.
            "model": "mlx-community/parakeet-tdt-0.6b-v3",
            "language": "",
            # Seconds of audio handed to the streaming decoder at a time when
            # transcribing an imported file, which is what makes the words
            # appear as it works. Smaller updates more often and loses a little
            # accuracy at the seams. Not used for hotkey dictation, which runs
            # in one pass.
            "stream_chunk_seconds": 10.0,
        },
        "faster_whisper": {
            # tiny.en · base.en · small.en · medium.en · large-v3, or a
            # multilingual variant without the .en suffix.
            "model": "base.en",
            "device": "cpu",
            "compute_type": "int8",
            "language": "en",
            # 1 = greedy. Higher is more accurate and markedly slower.
            "beam_size": 1,
            "vad_filter": True,
            # 0 -> let CTranslate2 choose based on core count.
            "threads": 0,
        },
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
        "deepgram": {
            # Cloud. Uploads your audio, and does the cleanup server-side.
            "base_url": "https://api.deepgram.com/v1",
            # Keyterm prompting — how the Dictionary biases this engine —
            # needs a nova-3 model and a single (non-"multi") language.
            "model": "nova-3",
            "language": "en",
            "api_key_env": "DEEPGRAM_API_KEY",
            "timeout": 30,
            # --- cleanup, applied during transcription ---
            "smart_format": True,     # punctuation, casing, dates, money
            "punctuate": True,
            "paragraphs": False,      # dictation is usually one paragraph
            "filler_words": False,    # drop "uh" and "um"
            "numerals": True,         # "twenty twenty six" -> 2026
            "measurements": False,
            "dictation": True,        # spoken "period" / "new line"
            "profanity_filter": False,
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
    # Local cleanup. Skipped step-by-step when the engine already does this
    # work itself -- see engines.base.TranscriptionEngine.handles_cleanup.
    "postprocess": {
        "strip_fillers": True,
        "fillers": ["um", "uh", "erm", "hmm", "mhm", "you know"],
        "capitalize_first": True,
        "collapse_whitespace": True,
        # Spoken phrases replaced with literal characters.
        "commands": {
            "new line": "\n",
            "new paragraph": "\n\n",
        },
    },
    "tools": {
        # Extra directories to add to PATH at startup. An app launched from
        # the Dock inherits none of your shell, so Homebrew's bin directory is
        # not on its PATH -- which is why ffmpeg can be "not found" on a
        # machine where `which ffmpeg` answers instantly. The usual prefixes
        # are added automatically; this is for anywhere unusual.
        "path_extra": [],
    },
    "interface": {
        # False hides the Dock icon and the application menu, leaving the menu
        # bar item as the whole interface -- the classic background-utility
        # shape. The status menu carries Open, Settings and Quit, so nothing
        # becomes unreachable. Changes take effect immediately.
        "dock_icon": True,
    },
    "feedback": {
        "sounds": True,
        # Chosen to be unmistakably *not* failure sounds: a soft rising bloop
        # to open, a bright chime to close. "Pop" -- a hollow thunk -- was the
        # previous stop cue and read as an error. Change them in Settings.
        "start_sound": "Bottle",
        "stop_sound": "Glass",
        "error_sound": "Basso",
        "notify_on_error": True,
    },
    # Vocabulary and corrections live in their own hand-editable file,
    # dictionary.json, not here. These are only the knobs.
    "dictionary": {
        "enabled": True,
        "bias": {
            # Prime the engine with dictionary words before it decodes.
            # Ignored by engines that cannot take a prompt (Parakeet).
            "enabled": True,
            # Kept deliberately small: a long prompt makes these models drift
            # and invent text over quiet audio.
            "max_terms": 12,
        },
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
            fresh = cls(copy.deepcopy(DEFAULTS))
            fresh.save()
            return fresh
        try:
            user = json.loads(CONFIG_FILE.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read %s (%s); using defaults", CONFIG_FILE, exc)
            return cls(copy.deepcopy(DEFAULTS))
        if not isinstance(user, dict):
            log.warning("%s is not a JSON object; using defaults", CONFIG_FILE)
            return cls(copy.deepcopy(DEFAULTS))

        config = cls(_deep_merge(DEFAULTS, user))
        if config.migrate(stored_version=int(user.get("version", 1) or 1)):
            config.save()
        return config

    def migrate(self, stored_version: int) -> bool:
        """Correct defaults that were wrong, without touching chosen values.

        Merging a stored file over the defaults means a changed default never
        reaches anyone who has already run the app -- their file still holds
        the old value. That is right for settings they chose and wrong for
        ones they never touched, which is the distinction this makes.
        """
        if stored_version >= SCHEMA_VERSION:
            return False

        migrated = []
        for version in range(stored_version + 1, SCHEMA_VERSION + 1):
            for path, was, now in MIGRATIONS.get(version, []):
                if self.get(path) == was:
                    self.set(path, now)
                    log.info("Updated %s from %r to %r", path, was, now)
                    migrated.append(path)
        if migrated:
            log.info("Migrated %d setting(s) to schema v%d", len(migrated), SCHEMA_VERSION)
        self.set("version", SCHEMA_VERSION)
        return True

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
