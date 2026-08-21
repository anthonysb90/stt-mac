"""Marshal work onto the main thread.

AppKit is not thread-safe: touching an NSStatusItem from the transcription
worker will eventually crash or silently no-op. Every UI mutation goes through
here.
"""

from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger(__name__)


def run_on_main(func: Callable[[], None]) -> None:
    """Schedule ``func`` on the main run loop, or run it inline if AppKit is absent."""
    try:
        import Foundation

        Foundation.NSOperationQueue.mainQueue().addOperationWithBlock_(func)
    except Exception:  # pragma: no cover - non-mac dev machines and teardown
        try:
            func()
        except Exception:
            log.exception("Main-thread callback failed")
