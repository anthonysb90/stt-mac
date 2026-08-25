"""The microphone menu, built once, used everywhere it appears.

The main window's popup and the menu bar's submenu are the same menu: the
system default, a separator, every input device with a check against the
current selection, and a disabled row when there are none. They were two
near-identical copies, which is how the two surfaces would eventually have
come to disagree about which microphone is selected -- a fix to the matching
logic in one would have been forgotten in the other.
"""

from __future__ import annotations

from typing import Callable, Optional

import AppKit

from .. import audio
from . import components as C


def populate_menu(
    menu: AppKit.NSMenu,
    controller,
    keeper: list,
    describe_default: Optional[Callable[[str], str]] = None,
) -> None:
    """Rebuild ``menu`` with the current devices, checking the selected one.

    ``describe_default`` lets a caller shorten the system-default label to fit
    a narrow popup; the menu bar has room and passes nothing.
    """
    menu.removeAllItems()
    current = controller.input_device()

    default_label = audio.describe_device(None)
    if describe_default is not None:
        default_label = describe_default(default_label)
    menu.addItem_(C.menu_item(
        default_label, lambda: controller.set_input_device(None),
        keeper, checked=current is None,
    ))

    devices = audio.list_input_devices()
    if devices:
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
    for device in devices:
        index = device["index"]
        menu.addItem_(C.menu_item(
            device["name"],
            lambda i=index: controller.set_input_device(i),
            keeper,
            checked=(current == index or current == device["name"]),
        ))
    if not devices:
        menu.addItem_(C.menu_item("No microphones found", None, keeper, enabled=False))
