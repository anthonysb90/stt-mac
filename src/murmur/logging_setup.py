"""Logging that survives a GUI app with no terminal attached."""

from __future__ import annotations

import logging
import logging.handlers
import sys

from .paths import LOG_FILE, ensure_dirs

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"


def configure(level: str = "INFO") -> None:
    ensure_dirs()
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(file_handler)

    # Only useful in `make run`; harmless inside a bundle.
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(stream_handler)
