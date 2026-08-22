"""Whether Aloud appears in the Dock.

One setting, two activation policies:

* ``Regular`` — Dock icon, application menu, a place in the app switcher.
* ``Accessory`` — none of those. The app lives in the menu bar alone.

Accessory is not simply "Regular without the Dock icon". macOS also takes the
application menu away, so ⌘, and ⌘Q stop working and the *only* route to
Settings, the window and Quit is the menu bar item's menu. That menu carries
all three, which is the thing that makes hiding the Dock icon safe rather than
a way to lose the app. If the status item ever becomes optional, this has to
refuse to hide the Dock icon while it is off.

Switching is live — no relaunch — because a setting you have to restart to see
is a setting people assume is broken.
"""

from __future__ import annotations

import logging

import AppKit

log = logging.getLogger(__name__)


def policy_for(visible: bool):
    return (
        AppKit.NSApplicationActivationPolicyRegular if visible
        else AppKit.NSApplicationActivationPolicyAccessory
    )


def apply(visible: bool) -> bool:
    """Show or hide the Dock icon. Returns whether anything changed."""
    app = AppKit.NSApplication.sharedApplication()
    wanted = policy_for(visible)
    if app.activationPolicy() == wanted:
        return False

    app.setActivationPolicy_(wanted)
    log.info("Dock icon %s", "shown" if visible else "hidden")
    if visible:
        # Coming back from Accessory the application menu does not reappear
        # until the app is activated again, which leaves a Regular app with no
        # menu bar -- the worst of both.
        app.activateIgnoringOtherApps_(True)
    return True


def is_visible() -> bool:
    app = AppKit.NSApplication.sharedApplication()
    return app.activationPolicy() == AppKit.NSApplicationActivationPolicyRegular
