"""Static guards for the Settings window.

Two bugs shipped here, and neither can fail on a machine without AppKit:

* the API key field was built for the *running* engine only, so on a Mac
  transcribing locally there was no field at all and no way to store a
  Deepgram key without first switching to an engine that could not start
  without one;
* the sections were pinned to a fixed-height window with no scroller, so the
  content that did not fit was simply unreachable — the Model section was
  clipped off the top edge.

Both are asserted against the source, the way the rest of the AppKit rules are.
"""

import ast
import re
from pathlib import Path

import pytest

SOURCE_PATH = (
    Path(__file__).resolve().parent.parent
    / "src" / "aloud" / "ui" / "settings_window.py"
)
SOURCE = SOURCE_PATH.read_text()
TREE = ast.parse(SOURCE)


def _method(name: str) -> ast.FunctionDef:
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"settings_window.py has no {name}()")


def test_credentials_are_offered_for_every_service_not_just_the_active_one():
    """A key you cannot store until you switch to the engine that needs it."""
    body = ast.unparse(_method("_credentials_section"))
    assert "engines.names()" in body and "REGISTRY" in body, (
        "the credentials section must enumerate the engine registry"
    )
    assert "needs_api_key" in body, (
        "it must filter on needs_api_key rather than on the running engine"
    )


def test_the_credentials_section_does_not_read_the_running_engine():
    body = ast.unparse(_method("_credentials_section"))
    assert "controller.engine" not in body, (
        "scoping credentials to the active engine is the bug this replaced"
    )


def test_each_service_gets_its_own_field_and_status():
    """One shared field would write whichever key was typed last to one file."""
    body = ast.unparse(_method("_credentials_block"))
    for piece in ("secure_field", "_save_key", "_clear_key", "_key_rows["):
        assert piece in body, f"_credentials_block is missing {piece}"


def test_key_handlers_capture_the_service_name():
    """A late-binding closure would send every button to the last engine."""
    body = ast.unparse(_method("_credentials_block"))
    handlers = re.findall(r"lambda _s, n=name:", body)
    assert len(handlers) >= 3, (
        "bind the engine name as a default argument in each button's lambda; "
        f"found {len(handlers)}"
    )


def test_the_window_can_scroll_its_content():
    body = ast.unparse(_method("_build"))
    assert "C.scroller" in body, (
        "content taller than the window has to scroll or it is unreachable"
    )


def test_the_window_is_resizable_and_has_a_floor():
    body = ast.unparse(_method("_build"))
    assert "NSWindowStyleMaskResizable" in body
    assert "setContentMinSize_" in body, (
        "a resizable window needs a minimum or the form collapses"
    )


def test_no_control_is_hidden_while_its_caption_stays():
    """Hiding a field left its right-aligned caption floating beside nothing.

    Every row here is built to be visible. If one ever has to disappear, hide
    the row that carries the caption, not the control inside it.
    """
    assert "setHidden_" not in SOURCE, (
        "settings_window.py hides a view; hide the whole captioned row instead"
    )


@pytest.mark.parametrize("token", ["settings_width_min", "settings_height_min"])
def test_the_size_floor_comes_from_tokens(token):
    assert f'T.METRIC["{token}"]' in SOURCE, f"{token} should come from the tokens"
