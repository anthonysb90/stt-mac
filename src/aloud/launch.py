"""Reporting a failure to start.

Deliberately its own module, with no framework imports at the top. The failure
this was written for was an *import* failure in :mod:`aloud.app` -- PyObjC
rejecting a class at definition time -- so a reporter living inside that module
was never reached. A reporter has to be importable when the thing it reports on
is not.

What it replaces: py2app shows "Launch error — see the py2app website for
debugging launch issues" and discards the traceback, which is the least useful
thing it could do with it.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import APP_NAME, __version__, build_id
from .paths import LAUNCH_ERROR_FILE, ensure_dirs

log = logging.getLogger(__name__)


def describe(exc: BaseException) -> str:
    """The full report: what failed, where, and on what."""
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return (
        f"{APP_NAME} {__version__} (build {build_id()}) failed to start\n"
        f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"python {sys.version.split()[0]} on {platform.machine()} "
        f"· macOS {platform.mac_ver()[0] or 'unknown'}\n"
        f"source {Path(__file__).resolve().parent}\n\n"
        f"{detail}"
    )


def report(exc: BaseException) -> Path:
    """Write, log, print and show a launch failure. Never raises."""
    report_text = describe(exc)

    try:
        ensure_dirs()
        LAUNCH_ERROR_FILE.write_text(report_text, encoding="utf-8")
    except OSError:
        pass

    try:
        log.critical("Launch failed\n%s", report_text)
    except Exception:
        pass

    sys.stderr.write("\n" + report_text)
    _show_dialog(exc)
    return LAUNCH_ERROR_FILE


def _show_dialog(exc: BaseException) -> None:
    """Put it on screen too, since a GUI launch has no terminal to print to.

    AppKit is imported here rather than at module scope: if AppKit is what
    broke, the report must still reach the log and stderr.
    """
    try:
        import AppKit

        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(f"{APP_NAME} could not start")
        alert.setInformativeText_(
            f"{type(exc).__name__}: {exc}\n\nFull details:\n{LAUNCH_ERROR_FILE}"
        )
        alert.addButtonWithTitle_("OK")
        alert.addButtonWithTitle_("Show Details")
        if alert.runModal() == AppKit.NSAlertSecondButtonReturn:
            subprocess.run(["open", "-t", str(LAUNCH_ERROR_FILE)], check=False)
    except Exception:
        log.debug("Could not show the launch failure dialog", exc_info=True)
