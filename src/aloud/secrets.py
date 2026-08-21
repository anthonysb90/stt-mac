"""Finding API keys for an app that was launched from the Dock.

A GUI app does not inherit your shell environment. Exporting
``DEEPGRAM_API_KEY`` in ``.zshrc`` reaches ``aloud doctor`` in Terminal and
reaches nothing at all when you double-click Aloud in Applications — which
would show up as "the cloud engine works when I test it and fails when I use
it", the worst kind of bug to be handed.

So a key is looked for in three places, in order:

1. the environment, which is what the CLI and a development run use;
2. a file in the app's support directory, which is what a Dock launch uses;
3. the login keychain, for anyone who would rather not have a key on disk in
   the clear.

:func:`store_key` writes (2) with owner-only permissions.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from . import BUNDLE_ID
from .paths import SUPPORT_DIR, ensure_dirs

log = logging.getLogger(__name__)

KEYS_DIR = SUPPORT_DIR / "keys"
#: rw for the owner, nothing for anyone else.
KEY_FILE_MODE = 0o600


def key_path(name: str) -> Path:
    return KEYS_DIR / f"{name}.key"


def read_key(env_var: str, name: str) -> str:
    """The key for ``name``, or an empty string if it is not set anywhere."""
    from_env = os.environ.get(env_var, "").strip()
    if from_env:
        return from_env

    path = key_path(name)
    try:
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    except OSError:
        log.warning("Could not read %s", path)

    return _read_keychain(name)


def store_key(name: str, value: str) -> Path:
    """Save a key where a Dock-launched app will find it."""
    ensure_dirs()
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    KEYS_DIR.chmod(0o700)
    path = key_path(name)
    path.write_text(value.strip() + "\n", encoding="utf-8")
    path.chmod(KEY_FILE_MODE)
    return path


def forget_key(name: str) -> bool:
    path = key_path(name)
    if path.exists():
        path.unlink()
        return True
    return False


def describe_source(env_var: str, name: str) -> str:
    """Where the key came from, for `aloud doctor`."""
    if os.environ.get(env_var, "").strip():
        return f"${env_var}"
    if key_path(name).is_file():
        return str(key_path(name))
    if _read_keychain(name):
        return "login keychain"
    return "not set"


def _read_keychain(name: str) -> str:
    """Ask the login keychain, without prompting for a password."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", BUNDLE_ID, "-a", name, "-w"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
