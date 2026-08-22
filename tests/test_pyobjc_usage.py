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


# ---------------------------------------------------------------------------
# Selector arity — the bug that produced BadPrototypeError at import time
# ---------------------------------------------------------------------------

OBJC_BASE_PREFIXES = ("AppKit.NS", "Foundation.NS", "Quartz.", "NS")


def _selector_arity(name: str) -> int:
    """How many arguments PyObjC's selector for this method name takes.

    Underscores become colons; a leading underscore is kept as-is. So `_alert`
    is a zero-argument selector and `on_error` is a one-argument one — which is
    why a two-argument `on_error(self, title, message)` is rejected.
    """
    return name.lstrip("_").count("_")


def _objc_subclasses(tree):
    """Classes PyObjC will transform — including ones that inherit indirectly.

    DropZone subclasses TokenBox, which subclasses NSView. PyObjC transforms it
    exactly the same way, so a check that only looked for `AppKit.NS...` in the
    bases would have silently stopped covering it.
    """
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    objc_names = set()
    # Repeat until it settles, so a chain of any depth is caught.
    for _ in range(len(classes) + 1):
        grew = False
        for node in classes:
            if node.name in objc_names:
                continue
            for base in node.bases:
                text = ast.unparse(base)
                if text.startswith(OBJC_BASE_PREFIXES) or text in objc_names:
                    objc_names.add(node.name)
                    grew = True
                    break
        if not grew:
            break
    return [node for node in classes if node.name in objc_names]


def _is_python_method(fn) -> bool:
    return any(
        ast.unparse(d) in ("objc.python_method", "python_method")
        for d in fn.decorator_list
    )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_exposed_selectors_have_matching_arity(path):
    """A mismatch is not a runtime error — it stops the class being defined.

    PyObjC raises BadPrototypeError while executing the `class` statement, so
    the module never imports and the app dies before any of its own error
    handling exists. It surfaces as a bare "Launch error" with no traceback,
    which is why this is asserted rather than discovered.
    """
    tree = ast.parse(path.read_text())
    problems = []
    for klass in _objc_subclasses(tree):
        for fn in klass.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _is_python_method(fn) or (fn.name.startswith("__") and fn.name.endswith("__")):
                continue
            actual = len([a for a in fn.args.args if a.arg != "self"])
            expected = _selector_arity(fn.name)
            if actual != expected:
                problems.append(
                    f"{klass.name}.{fn.name}: selector takes {expected} argument(s), "
                    f"method takes {actual} — add @objc.python_method"
                )
    assert not problems, f"{path.name}\n  " + "\n  ".join(problems)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_helper_methods_on_objc_classes_are_marked_as_python(path):
    """A helper exposed as a selector is a bug waiting for a rename.

    Even when the arity happens to line up, `on_state` becomes the selector
    `on:state`, which is not something anyone intended. Anything AppKit is not
    going to call should say so.
    """
    tree = ast.parse(path.read_text())
    leaked = []
    for klass in _objc_subclasses(tree):
        for fn in klass.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _is_python_method(fn) or (fn.name.startswith("__") and fn.name.endswith("__")):
                continue
            # A real selector either takes no arguments or ends with `_`.
            takes_args = any(a.arg != "self" for a in fn.args.args)
            if takes_args and not fn.name.endswith("_"):
                leaked.append(f"{klass.name}.{fn.name}")
    assert not leaked, (
        f"{path.name}: {leaked} take arguments but do not end with '_', so they are "
        f"not AppKit callbacks — mark them @objc.python_method"
    )


def test_the_launch_reporter_does_not_import_frameworks_at_module_scope():
    """It has to be importable when the thing it reports on is not."""
    tree = ast.parse((SRC / "launch.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in FRAMEWORKS, (
                    f"launch.py imports {alias.name} at module scope; if that is what "
                    f"broke, the report never gets written"
                )


def test_the_bundle_entry_point_forwards_its_arguments():
    """`--version` from the bundle's binary is the build-time launch check."""
    source = (ROOT / "Aloud.py").read_text()
    assert "sys.argv[1:]" in source
    assert "psn_" in source, "Finder can append a -psn_ argument; tolerate it"


def test_the_checker_follows_indirect_subclasses():
    """Guard the guard: DropZone inherits NSView through TokenBox."""
    tree = ast.parse((SRC / "ui" / "components.py").read_text())
    names = {node.name for node in _objc_subclasses(tree)}
    assert {"TokenBox", "DropZone"} <= names
