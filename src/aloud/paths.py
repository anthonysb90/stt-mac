"""Canonical on-disk locations, following macOS conventions."""

from __future__ import annotations

from pathlib import Path

from . import APP_NAME

HOME = Path.home()

SUPPORT_DIR = HOME / "Library" / "Application Support" / APP_NAME
LOG_DIR = HOME / "Library" / "Logs" / APP_NAME
CACHE_DIR = HOME / "Library" / "Caches" / APP_NAME

CONFIG_FILE = SUPPORT_DIR / "config.json"
MODELS_DIR = SUPPORT_DIR / "models"
VENDOR_DIR = SUPPORT_DIR / "vendor"
HISTORY_FILE = SUPPORT_DIR / "history.jsonl"
LOG_FILE = LOG_DIR / "aloud.log"


def ensure_dirs() -> None:
    """Create every directory the app writes to. Safe to call repeatedly."""
    for directory in (SUPPORT_DIR, LOG_DIR, CACHE_DIR, MODELS_DIR, VENDOR_DIR):
        directory.mkdir(parents=True, exist_ok=True)
