"""The design system's one rule, enforced against the code.

“Every view pulls from the tokens. No component defines its own value.” That
is only true if something checks, because the failure mode is silent: a hex
literal in a view works perfectly in whichever appearance you built it in.
"""

import re
from pathlib import Path

import pytest

UI_DIR = Path(__file__).resolve().parent.parent / "src" / "aloud" / "ui"

#: tokens.py is where values are allowed to exist; formatting.py is pure logic.
VIEW_FILES = sorted(
    path for path in UI_DIR.glob("*.py")
    if path.name not in {"tokens.py", "__init__.py"}
)

HEX_COLOUR = re.compile(r"#[0-9A-Fa-f]{6}\b")
NSCOLOR_LITERAL = re.compile(r"NSColor\.color(?:With(?:SRGB|Calibrated|Device))")
FONT_LITERAL = re.compile(r"NSFont\.(?:system|monospaced|bold|user)\w*FontOfSize")
BARE_CONSTANT = re.compile(r"constraint\w*Constant_\(\s*(-?\d+(?:\.\d+)?)\s*\)")

#: Layout priorities and zero are not design values.
ALLOWED_BARE_CONSTANTS = {"0", "0.0"}


def test_there_are_view_files_to_check():
    assert VIEW_FILES, "no view modules found — has the package moved?"


@pytest.mark.parametrize("path", VIEW_FILES, ids=lambda p: p.name)
def test_no_view_hard_codes_a_colour(path):
    found = HEX_COLOUR.findall(path.read_text())
    assert not found, f"{path.name} contains hex colours {found}; add a token instead"


@pytest.mark.parametrize("path", VIEW_FILES, ids=lambda p: p.name)
def test_no_view_builds_its_own_nscolor(path):
    """Colours must come through tokens.ns_color, which is appearance-aware."""
    assert not NSCOLOR_LITERAL.search(path.read_text()), (
        f"{path.name} constructs an NSColor directly; it will not follow dark mode"
    )


@pytest.mark.parametrize("path", VIEW_FILES, ids=lambda p: p.name)
def test_no_view_builds_its_own_font(path):
    assert not FONT_LITERAL.search(path.read_text()), (
        f"{path.name} builds a font directly; use a step of the type scale"
    )


@pytest.mark.parametrize("path", VIEW_FILES, ids=lambda p: p.name)
def test_no_view_sizes_a_constraint_with_a_bare_number(path):
    found = [
        value for value in BARE_CONSTANT.findall(path.read_text())
        if value not in ALLOWED_BARE_CONSTANTS
    ]
    assert not found, (
        f"{path.name} sizes constraints with {found}; add them to METRIC or SPACE"
    )


def test_components_is_the_only_module_that_reads_tokens_directly_for_drawing():
    """Views may name tokens, but only components turns them into AppKit objects."""
    for path in VIEW_FILES:
        if path.name in {"components.py", "meter.py", "menu_bar.py"}:
            continue  # these draw, so they legitimately bridge
        source = path.read_text()
        assert "ns_font(" not in source, f"{path.name} should use components, not ns_font"
