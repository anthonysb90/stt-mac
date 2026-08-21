"""The application menu bar — the one at the top of the screen.

A menu-bar-only app gets this for free by not having it. A real app has to
build the whole thing by hand, and the parts people reach for without thinking
(Cmd-Q, Cmd-W, Cmd-comma, Cut/Copy/Paste, Hide Others) are exactly the parts
that feel broken when they are missing.

Editing commands are wired to `nil` targets on purpose: that sends them down
the responder chain, so Cmd-C copies from whichever text field has focus
without this module knowing anything about the views.
"""

from __future__ import annotations

from typing import Callable, Dict

import AppKit

from .. import APP_NAME


def _item(title: str, selector: bytes, key: str = "",
          modifiers=None, target=None) -> AppKit.NSMenuItem:
    item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, key)
    if modifiers is not None:
        item.setKeyEquivalentModifierMask_(modifiers)
    if target is not None:
        item.setTarget_(target)
    return item


def _submenu(parent: AppKit.NSMenu, title: str) -> AppKit.NSMenu:
    holder = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
    menu = AppKit.NSMenu.alloc().initWithTitle_(title)
    holder.setSubmenu_(menu)
    parent.addItem_(holder)
    return menu


def build(handlers: Dict[str, Callable], target) -> AppKit.NSMenu:
    """Construct and install the main menu.

    ``handlers`` is only used to decide which optional items appear; the items
    themselves call selectors on ``target``, which is the app delegate.
    """
    main = AppKit.NSMenu.alloc().init()

    # --- Aloud -------------------------------------------------------------
    app_menu = _submenu(main, APP_NAME)
    app_menu.addItem_(_item(f"About {APP_NAME}", b"showAbout:", target=target))
    app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
    app_menu.addItem_(_item("Settings…", b"showSettings:", ",", target=target))
    app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
    app_menu.addItem_(_item("Open Dictionary File", b"openDictionaryFile:", target=target))
    app_menu.addItem_(_item("Open Log", b"openLog:", target=target))
    app_menu.addItem_(AppKit.NSMenuItem.separatorItem())

    services = AppKit.NSMenu.alloc().init()
    services_item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Services", None, "")
    services_item.setSubmenu_(services)
    app_menu.addItem_(services_item)
    AppKit.NSApplication.sharedApplication().setServicesMenu_(services)
    app_menu.addItem_(AppKit.NSMenuItem.separatorItem())

    app_menu.addItem_(_item(f"Hide {APP_NAME}", b"hide:", "h"))
    app_menu.addItem_(_item(
        "Hide Others", b"hideOtherApplications:", "h",
        AppKit.NSEventModifierFlagCommand | AppKit.NSEventModifierFlagOption,
    ))
    app_menu.addItem_(_item("Show All", b"unhideAllApplications:"))
    app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
    app_menu.addItem_(_item(f"Quit {APP_NAME}", b"terminate:", "q"))

    # --- Dictation ---------------------------------------------------------
    dictation = _submenu(main, "Dictation")
    dictation.addItem_(_item("Start Dictation", b"startDictation:", "d", target=target))
    dictation.addItem_(_item("Stop Dictation", b"stopDictation:", "d",
                             AppKit.NSEventModifierFlagCommand | AppKit.NSEventModifierFlagShift,
                             target=target))
    dictation.addItem_(_item("Cancel Recording", b"cancelDictation:", "\x1b", target=target))
    dictation.addItem_(AppKit.NSMenuItem.separatorItem())
    dictation.addItem_(_item("Copy Last Dictation", b"copyLast:", "c",
                             AppKit.NSEventModifierFlagCommand | AppKit.NSEventModifierFlagShift,
                             target=target))

    # --- Edit --------------------------------------------------------------
    # nil targets on purpose: these travel the responder chain to whatever
    # text field has focus.
    edit = _submenu(main, "Edit")
    edit.addItem_(_item("Undo", b"undo:", "z"))
    edit.addItem_(_item("Redo", b"redo:", "Z"))
    edit.addItem_(AppKit.NSMenuItem.separatorItem())
    edit.addItem_(_item("Cut", b"cut:", "x"))
    edit.addItem_(_item("Copy", b"copy:", "c"))
    edit.addItem_(_item("Paste", b"paste:", "v"))
    edit.addItem_(_item("Select All", b"selectAll:", "a"))
    edit.addItem_(AppKit.NSMenuItem.separatorItem())
    edit.addItem_(_item("Find", b"performTextFinderAction:", "f"))

    # --- View --------------------------------------------------------------
    view = _submenu(main, "View")
    view.addItem_(_item("History", b"showHistory:", "1", target=target))
    view.addItem_(_item("Dictionary", b"showDictionary:", "2", target=target))
    view.addItem_(AppKit.NSMenuItem.separatorItem())
    view.addItem_(_item("Enter Full Screen", b"toggleFullScreen:", "f",
                        AppKit.NSEventModifierFlagCommand | AppKit.NSEventModifierFlagControl))

    # --- Window ------------------------------------------------------------
    window = _submenu(main, "Window")
    window.addItem_(_item("Minimize", b"performMiniaturize:", "m"))
    window.addItem_(_item("Zoom", b"performZoom:"))
    window.addItem_(AppKit.NSMenuItem.separatorItem())
    window.addItem_(_item("Close", b"performClose:", "w"))
    window.addItem_(AppKit.NSMenuItem.separatorItem())
    window.addItem_(_item(f"{APP_NAME} Window", b"showMainWindow:", "0", target=target))
    AppKit.NSApplication.sharedApplication().setWindowsMenu_(window)

    AppKit.NSApplication.sharedApplication().setMainMenu_(main)
    return main
