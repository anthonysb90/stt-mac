"""Static guards for the PyObjC mistakes that only show up at launch.

None of this can be caught by running the test suite off a Mac, and all of it
fails the same way when it is wrong: a py2app "Launch error" dialog with the
traceback thrown away. So the rules are asserted against the source instead.

Each test here corresponds to a bug that actually shipped.
"""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "aloud"
SOURCES = sorted(SRC.rglob("*.py"))

#: Frameworks whose absence from a standalone bundle is an ImportError at launch.
FRAMEWORKS = {
    "AppKit", "Foundation", "Quartz", "objc", "CoreFoundation",
    "ApplicationServices", "AVFoundation", "CoreMedia",
}

#: Symbols that belong to Foundation. AppKit re-exports most of them, but
#: relying on that is how you get an AttributeError on a macOS you did not test.
FOUNDATION_OWNED = [
    "NSObject", "NSInsetRect", "NSRunLoop", "NSRunLoopCommonModes",
    "NSAttributedString", "NSMutableAttributedString", "NSMakeRect",
]


def test_there_are_sources_to_check():
    assert SOURCES


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_nsapp_is_never_used_as_an_object(path):
    """`NSApp` is a *function* in PyObjC, not the application instance.

    `AppKit.NSApp.setMainMenu_(menu)` raises AttributeError the moment the app
    finishes launching. Use `NSApplication.sharedApplication()`.
    """
    offenders = re.findall(r"\bAppKit\.NSApp\.\w+", path.read_text())
    assert not offenders, f"{path.name}: {offenders} — use NSApplication.sharedApplication()"


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_foundation_symbols_come_from_foundation(path):
    source = path.read_text()
    offenders = [symbol for symbol in FOUNDATION_OWNED if f"AppKit.{symbol}" in source]
    assert not offenders, (
        f"{path.name} reaches {offenders} through AppKit; import them from Foundation"
    )


def _function_level_framework_imports(path: Path):
    """Framework imports that sit inside a function body.

    These are deliberate — importing AVFoundation at module scope costs launch
    time for something that may never be called — but py2app's modulegraph only
    walks module-level imports, so anything here has to be declared in setup.py
    or it will be missing from a standalone bundle.
    """
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Import):
                for alias in inner.names:
                    root = alias.name.split(".")[0]
                    if root in FRAMEWORKS:
                        found.add(root)
            elif isinstance(inner, ast.ImportFrom) and inner.module:
                root = inner.module.split(".")[0]
                if root in FRAMEWORKS:
                    found.add(root)
    return found


def test_lazily_imported_frameworks_are_declared_to_py2app():
    """The bug that produced an empty 'Launch error' dialog.

    ApplicationServices and AVFoundation are only ever imported inside
    functions, so modulegraph never saw them and the standalone bundle shipped
    without them.
    """
    lazy = set()
    for path in SOURCES:
        lazy |= _function_level_framework_imports(path)

    declared = set(
        re.findall(r'^\s*"([A-Za-z]+)",', (ROOT / "setup.py").read_text(), re.MULTILINE)
    )
    missing = sorted(lazy - declared)
    assert not missing, (
        f"{missing} are imported inside functions but not listed in setup.py's "
        f"PYOBJC_FRAMEWORKS — a standalone build will not include them"
    )


def test_every_subpackage_is_declared_for_installation():
    """A subpackage missing from setup() is missing from an installed copy."""
    declared = re.search(r"packages=\[([^\]]*)\]", (ROOT / "setup.py").read_text())
    listed = set(re.findall(r'"([\w.]+)"', declared.group(1)))
    actual = {"aloud"} | {
        "aloud." + path.parent.name
        for path in SRC.rglob("__init__.py")
        if path.parent != SRC
    }
    assert actual <= listed, f"setup.py is missing {sorted(actual - listed)}"


def test_the_bundle_entry_point_forwards_its_arguments():
    """`--version` from the bundle's binary is the build-time launch check."""
    source = (ROOT / "Aloud.py").read_text()
    assert "sys.argv[1:]" in source
    assert "psn_" in source, "Finder can append a -psn_ argument; tolerate it"
