"""The Dock icon toggle, and the things that must stay reachable without it.

Hiding the Dock icon switches macOS to the Accessory activation policy, which
also removes the application menu — so ⌘, and ⌘Q stop working and the menu bar
item becomes the whole interface. These check both halves: that the switch does
what it says, and that nothing essential is left behind the menu that vanishes.
"""

import ast
from pathlib import Path

import AppKit
import pytest

from aloud.config import DEFAULTS, Config
from aloud.ui import dock

SRC = Path(__file__).resolve().parent.parent / "src" / "aloud"


@pytest.fixture(autouse=True)
def fresh_application():
    """The stub NSApplication is shared, so each test starts from Regular."""
    AppKit.NSApplication.reset()
    yield
    AppKit.NSApplication.reset()


def test_the_two_policies_are_different():
    assert dock.policy_for(True) == AppKit.NSApplicationActivationPolicyRegular
    assert dock.policy_for(False) == AppKit.NSApplicationActivationPolicyAccessory


def test_hiding_switches_to_accessory():
    assert dock.apply(False) is True
    assert AppKit.NSApplication.sharedApplication().policy == (
        AppKit.NSApplicationActivationPolicyAccessory
    )
    assert not dock.is_visible()


def test_showing_again_reactivates_the_app():
    """Without this the menu bar does not come back and you get neither."""
    dock.apply(False)
    before = AppKit.NSApplication.sharedApplication().activations
    dock.apply(True)
    app = AppKit.NSApplication.sharedApplication()
    assert app.policy == AppKit.NSApplicationActivationPolicyRegular
    assert app.activations == before + 1
    assert dock.is_visible()


def test_hiding_does_not_steal_focus():
    """Activating on the way *out* would yank the user back to a hidden app."""
    before = AppKit.NSApplication.sharedApplication().activations
    dock.apply(False)
    assert AppKit.NSApplication.sharedApplication().activations == before


def test_applying_the_current_policy_is_a_no_op():
    assert dock.apply(True) is False, "already Regular"
    assert AppKit.NSApplication.sharedApplication().activations == 0


def test_the_dock_icon_is_on_by_default():
    assert DEFAULTS["interface"]["dock_icon"] is True


def test_the_setting_round_trips(tmp_path, monkeypatch):
    from aloud import config as config_module

    path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_FILE", path)
    monkeypatch.setattr(config_module, "ensure_dirs", lambda: None)

    stored = Config.load()
    stored.set("interface.dock_icon", False)
    stored.save()
    assert Config.load().get("interface.dock_icon") is False


# -- what must survive the application menu disappearing --------------------


def _source(name: str) -> str:
    return (SRC / name).read_text()


def test_launch_honours_the_setting():
    body = _source("app.py")
    assert "dock.policy_for(" in body, (
        "run() must set the activation policy from interface.dock_icon"
    )


def test_the_window_does_not_barge_in_when_the_dock_icon_is_hidden():
    """A menu-bar-only app that opens a window at login is not one."""
    tree = ast.parse(_source("app.py"))
    finish = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_finish_launching"
    )
    body = ast.unparse(finish)
    assert "interface.dock_icon" in body
    assert "self.main_window.show()" in body


@pytest.mark.parametrize("entry", ["open_main", "open_settings", "quit"])
def test_the_menu_bar_can_do_everything_the_app_menu_could(entry):
    """With no application menu, this menu is the only way to reach these."""
    assert f'self.handlers["{entry}"]' in _source("ui/menu_bar.py"), (
        f"the status menu must offer {entry}; it is the only route once the "
        "Dock icon is hidden"
    )


def test_the_menu_bar_offers_the_toggle_itself():
    body = _source("ui/menu_bar.py")
    assert "self.dock_item" in body and "_toggle_dock" in body


def test_the_menu_bar_checkmark_is_rebuilt_before_the_menu_opens():
    """Settings can change the same value, so a once-set checkmark goes stale."""
    body = _source("ui/menu_bar.py")
    assert "setDelegate_(self._refresher)" in body
    assert "MenuRefresher" in body


def test_settings_offers_the_toggle_and_explains_the_cost():
    body = _source("ui/settings_window.py")
    assert "_appearance_section" in body
    assert "interface.dock_icon" in body
    # The trade has to be stated where the switch is, not only in the README.
    assert "application menu" in body
