"""macOS privacy (TCC) permission checks.

Aloud needs two grants:

Accessibility  to observe the hotkey system-wide and to synthesize the paste.
Microphone     to record.

Both are granted to an *app bundle identity*, not to a script. Running from a
terminal attaches the grants to the terminal app instead, which is why
`make app` exists -- see docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Tuple

log = logging.getLogger(__name__)

_ACCESSIBILITY_PANE = (
    "x-apple.systempreferences:com.apple.preference.security"
    "?Privacy_Accessibility"
)
_MICROPHONE_PANE = (
    "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
)


def accessibility_trusted(prompt: bool = False) -> bool:
    """Whether this process may observe and post keyboard events."""
    try:
        import ApplicationServices
    except ImportError:  # pragma: no cover - only on non-mac dev machines
        log.warning("ApplicationServices unavailable; assuming untrusted")
        return False

    options = None
    if prompt:
        import Foundation

        options = Foundation.NSDictionary.dictionaryWithObject_forKey_(
            True, ApplicationServices.kAXTrustedCheckOptionPrompt
        )
    return bool(ApplicationServices.AXIsProcessTrustedWithOptions(options))


def microphone_authorized() -> Tuple[bool, str]:
    """Current microphone authorization as ``(granted, status name)``."""
    try:
        import AVFoundation
    except ImportError:  # pragma: no cover
        return False, "unavailable"

    status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeAudio
    )
    names = {
        AVFoundation.AVAuthorizationStatusNotDetermined: "not determined",
        AVFoundation.AVAuthorizationStatusRestricted: "restricted",
        AVFoundation.AVAuthorizationStatusDenied: "denied",
        AVFoundation.AVAuthorizationStatusAuthorized: "authorized",
    }
    granted = status == AVFoundation.AVAuthorizationStatusAuthorized
    return granted, names.get(status, str(status))


def request_microphone() -> None:
    """Trigger the system microphone prompt if it has not been shown yet."""
    try:
        import AVFoundation
    except ImportError:  # pragma: no cover
        return
    AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AVFoundation.AVMediaTypeAudio,
        lambda granted: log.info("Microphone access granted: %s", bool(granted)),
    )


def open_accessibility_settings() -> None:
    subprocess.run(["open", _ACCESSIBILITY_PANE], check=False)


def open_microphone_settings() -> None:
    subprocess.run(["open", _MICROPHONE_PANE], check=False)


def summary() -> str:
    """One-line status for the menu bar."""
    mic_ok, mic_state = microphone_authorized()
    ax = "granted" if accessibility_trusted() else "MISSING"
    mic = "granted" if mic_ok else mic_state.upper()
    return f"Accessibility: {ax} · Microphone: {mic}"
