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


def signing_identity() -> str:
    """How the running bundle is signed: an identity name, "ad-hoc", or "".

    Worth knowing at runtime because it decides what a *missing* Accessibility
    grant actually means. Ad-hoc signatures are a hash of the bundle, so every
    rebuild is a different app to macOS and any grant made against an earlier
    build stops applying -- while still appearing, switched on, in System
    Settings. Telling someone to do what they have already done is worse than
    saying nothing; this is how the message knows better.
    """
    bundle = bundle_path()
    if not bundle:
        return ""
    try:
        result = subprocess.run(
            ["codesign", "-dv", "--verbose=4", bundle],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""

    output = (result.stderr or "") + (result.stdout or "")
    if "Signature=adhoc" in output:
        return "ad-hoc"
    for line in output.splitlines():
        if line.startswith("Authority="):
            return line.split("=", 1)[1].strip()
    return ""


def bundle_path() -> str:
    """The .app this process is running from, or "" outside a bundle."""
    try:
        import AppKit

        bundle = AppKit.NSBundle.mainBundle()
        path = str(bundle.bundlePath()) if bundle is not None else ""
    except Exception:
        return ""
    return path if path.endswith(".app") else ""


def signature_valid() -> Tuple[bool, str]:
    """Whether the bundle's signature actually verifies.

    TCC will not honour a grant against a signature that does not check out,
    and codesign accepting a signature at signing time does not mean the result
    verifies afterwards -- `--deep` over an alias bundle that references a
    Python framework outside itself is exactly the shape that produces one.
    The symptom is a correct-looking identity and a permission that never
    takes, which is indistinguishable from every other cause without asking.
    """
    bundle = bundle_path()
    if not bundle:
        return False, "not in a bundle"
    try:
        result = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", "--verbose=2", bundle],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run codesign: {exc}"

    if result.returncode == 0:
        return True, "valid"
    detail = ((result.stderr or "") + (result.stdout or "")).strip().splitlines()
    return False, detail[-1] if detail else f"exit {result.returncode}"


def accessibility_advice() -> str:
    """What to actually do about a missing Accessibility grant, given how the
    app is signed. Returns the body of the alert."""
    common = (
        "Add Aloud under Privacy & Security \u2192 Accessibility, then quit "
        "and reopen it."
    )
    if signing_identity() != "ad-hoc":
        return common

    return (
        "This build is signed ad-hoc, which gives it a new identity every time "
        "it is rebuilt \u2014 so a grant you made for an earlier build no longer "
        "applies, even though System Settings still shows Aloud switched on.\n\n"
        "Remove the Aloud row with the \u2212 button, add it again with +, then "
        "quit and reopen Aloud.\n\n"
        "To stop this recurring, run ./scripts/make_signing_cert.sh once and "
        "reinstall; the grant will survive every rebuild after that."
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
    line = f"Accessibility: {ax} · Microphone: {mic}"
    # Named only when it is the likely explanation, so `aloud doctor` does not
    # carry a line about code signing on a machine where nothing is wrong.
    if ax == "MISSING" and signing_identity() == "ad-hoc":
        line += " (ad-hoc signed — an earlier build's grant will not apply)"
    return line
