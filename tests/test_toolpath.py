"""PATH repair, and the bug it exists for.

An app launched from the Dock inherits launchd's PATH — `/usr/bin:/bin:
/usr/sbin:/sbin` — not your shell's. Homebrew installs to `/opt/homebrew/bin`
on Apple Silicon and `/usr/local/bin` on Intel, and neither is on that list. So
`aloud doctor` in Terminal reported ffmpeg found and the engine ready, while
the same build in /Applications put up "Transcription failed: ffmpeg not
found". That shipped.

Finding an absolute path ourselves is not enough on its own: parakeet-mlx runs
ffmpeg from inside the library, and the only channel to a subprocess we do not
spawn is the environment it inherits.
"""

import ast
import os
from pathlib import Path

import pytest

from aloud import toolpath

SRC = Path(__file__).resolve().parent.parent / "src" / "aloud"

#: The PATH a Dock launch actually gets.
LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


@pytest.fixture
def dock_launch(monkeypatch, tmp_path):
    """A minimal PATH, and a stand-in Homebrew prefix that really exists."""
    brew = tmp_path / "opt" / "homebrew" / "bin"
    brew.mkdir(parents=True)
    monkeypatch.setenv("PATH", LAUNCHD_PATH)
    monkeypatch.setattr(toolpath, "TOOL_DIRS", (str(brew), "/usr/bin"))
    return brew


def test_a_missing_tool_directory_is_added(dock_launch):
    added = toolpath.repair()
    assert str(dock_launch) in added
    assert str(dock_launch) in os.environ["PATH"].split(os.pathsep)


def test_directories_that_do_not_exist_are_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", LAUNCHD_PATH)
    monkeypatch.setattr(toolpath, "TOOL_DIRS", (str(tmp_path / "nowhere"),))
    assert toolpath.repair() == []
    assert os.environ["PATH"] == LAUNCHD_PATH


def test_what_was_already_there_keeps_its_place(dock_launch):
    """Appending, not prepending: a deliberate PATH entry still wins."""
    toolpath.repair()
    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[: len(LAUNCHD_PATH.split(":"))] == LAUNCHD_PATH.split(":")


def test_repairing_twice_changes_nothing(dock_launch):
    toolpath.repair()
    after_first = os.environ["PATH"]
    assert toolpath.repair() == []
    assert os.environ["PATH"] == after_first


def test_extra_directories_come_first(monkeypatch, tmp_path):
    """config's tools.path_extra is a deliberate choice, so it outranks guesses."""
    mine = tmp_path / "mine"
    theirs = tmp_path / "theirs"
    mine.mkdir()
    theirs.mkdir()
    monkeypatch.setenv("PATH", LAUNCHD_PATH)
    monkeypatch.setattr(toolpath, "TOOL_DIRS", (str(theirs),))
    added = toolpath.repair([str(mine)])
    assert added == [str(mine), str(theirs)]


def test_blank_entries_are_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", LAUNCHD_PATH)
    monkeypatch.setattr(toolpath, "TOOL_DIRS", ())
    assert toolpath.repair(["", "   "]) == []


def test_an_empty_path_is_survivable(monkeypatch, tmp_path):
    brew = tmp_path / "bin"
    brew.mkdir()
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(toolpath, "TOOL_DIRS", (str(brew),))
    toolpath.repair()
    assert os.environ["PATH"] == str(brew)


def test_both_homebrew_prefixes_are_covered():
    """One per architecture. Missing either breaks that machine and no other."""
    assert "/opt/homebrew/bin" in toolpath.TOOL_DIRS, "Apple Silicon"
    assert "/usr/local/bin" in toolpath.TOOL_DIRS, "Intel"


# -- the wiring, which is the part that actually fixes the bug --------------


def _calls_repair(path: Path, function: str) -> bool:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return "toolpath.repair" in ast.unparse(node)
    raise AssertionError(f"{path.name} has no {function}()")


def test_the_app_repairs_path_before_anything_else():
    """This is the one that matters: the app is what has the broken PATH."""
    assert _calls_repair(SRC / "app.py", "run")


def test_the_cli_repairs_path_too():
    """Otherwise `aloud doctor` and the app disagree about what is installed."""
    assert _calls_repair(SRC / "cli.py", "main")


def test_parakeet_does_not_ask_shutil_which_directly():
    """`shutil.which` alone is exactly the check that reported a false negative."""
    source = (SRC / "engines" / "parakeet_mlx.py").read_text()
    assert 'shutil.which("ffmpeg")' not in source
    assert "ffmpeg_path" in source


# -- the locale, which is the same trap in a third disguise ------------------


def test_repair_locale_ends_in_utf8():
    """Whatever the route there, the result must be a UTF-8 default."""
    result = toolpath.repair_locale().lower().replace("-", "")
    assert "utf8" in result


def test_repair_locale_exports_the_choice_to_children(monkeypatch):
    """ffmpeg and whisper-cli inherit our environment; they should agree."""
    monkeypatch.delenv("LANG", raising=False)
    monkeypatch.delenv("LC_CTYPE", raising=False)
    toolpath.repair_locale()
    assert os.environ["LANG"] == "en_US.UTF-8"
    assert os.environ["LC_CTYPE"] == "en_US.UTF-8"


def test_repair_locale_respects_an_existing_choice(monkeypatch):
    """A deliberately-set locale must not be overwritten, only filled in."""
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    toolpath.repair_locale()
    assert os.environ["LANG"] == "de_DE.UTF-8"


def test_the_app_and_cli_repair_the_locale():
    for name, function in (("app.py", "run"), ("cli.py", "main")):
        tree = ast.parse((SRC / name).read_text())
        node = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == function
        )
        assert "toolpath.repair_locale" in ast.unparse(node), (
            f"{name}:{function} must repair the locale before an engine loads"
        )


def test_the_bundle_declares_utf8_mode():
    """LSEnvironment reaches the interpreter before any Python runs at all."""
    setup_py = (SRC.parent.parent / "setup.py").read_text()
    assert '"LSEnvironment"' in setup_py and '"PYTHONUTF8": "1"' in setup_py


def test_our_own_text_io_never_relies_on_the_default_encoding():
    """Explicit utf-8 everywhere, so our files survive even without the repair.

    Walked with ast rather than a regex: these calls nest parentheses
    (json.dumps inside write_text), which a regex cannot bracket.
    """
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            if not (isinstance(callee, ast.Attribute)
                    and callee.attr in ("read_text", "write_text")):
                continue
            named = {kw.arg for kw in node.keywords}
            if "encoding" not in named:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, f"text I/O without an encoding: {offenders}"


# -- the stale Accessibility grant ------------------------------------------


def test_ad_hoc_advice_names_the_real_cause(monkeypatch):
    """Repeating instructions someone has already followed is worse than
    saying nothing; an ad-hoc build must explain why the grant went stale."""
    from aloud import permissions

    monkeypatch.setattr(permissions, "signing_identity", lambda: "ad-hoc")
    advice = permissions.accessibility_advice()
    assert "ad-hoc" in advice
    assert "earlier build" in advice
    assert "make_signing_cert" in advice, "and how to stop it recurring"


def test_stable_signing_gets_the_plain_advice(monkeypatch):
    from aloud import permissions

    monkeypatch.setattr(permissions, "signing_identity", lambda: "Aloud Dev")
    advice = permissions.accessibility_advice()
    assert "ad-hoc" not in advice
    assert "Accessibility" in advice


def test_summary_flags_ad_hoc_only_when_access_is_missing(monkeypatch):
    from aloud import permissions

    monkeypatch.setattr(permissions, "signing_identity", lambda: "ad-hoc")
    monkeypatch.setattr(permissions, "microphone_authorized", lambda: (True, "authorized"))

    monkeypatch.setattr(permissions, "accessibility_trusted", lambda: False)
    assert "ad-hoc signed" in permissions.summary()

    monkeypatch.setattr(permissions, "accessibility_trusted", lambda: True)
    assert "ad-hoc" not in permissions.summary(), "nothing is wrong; say nothing"


def test_signing_identity_is_blank_outside_a_bundle(monkeypatch):
    """Running from the terminal has no .app, so there is nothing to inspect."""
    from aloud import permissions

    monkeypatch.setattr(permissions, "bundle_path", lambda: "")
    assert permissions.signing_identity() == ""


def test_install_prefers_a_stable_identity_over_ad_hoc():
    script = (SRC.parent.parent / "scripts" / "install_app.sh").read_text()
    assert "find-identity" in script, "install must look for a stable identity"
    assert "Aloud Dev" in script
    assert "make fix-permissions" in script, "and say what to do when ad-hoc"


def test_the_signing_cert_uses_a_real_password():
    """Apple's SecKeychainItemImport rejects empty-password PKCS#12 files with
    "MAC verification failed (wrong password?)", which reads like a corrupt
    file rather than the policy it is. That shipped and blocked the install."""
    script = (SRC.parent.parent / "scripts" / "make_signing_cert.sh").read_text()
    assert "openssl rand" in script, "generate a password"
    assert "-passout pass:\n" not in script and '-passout pass: ' not in script, (
        "an empty PKCS#12 password fails to import on macOS"
    )
    assert '-P "$PASSWORD"' in script, "and import with the same password"


def test_the_signing_cert_proves_itself_by_signing():
    """find-identity listing a cert does not mean codesign will accept it: an
    untrusted self-signed root lists fine and fails to build a chain, which
    would only surface at the next install."""
    script = (SRC.parent.parent / "scripts" / "make_signing_cert.sh").read_text()
    assert "codesign --force --sign" in script


def test_install_refuses_to_fall_back_to_ad_hoc_silently():
    """A named identity that fails to sign must stop the install.

    Continuing produced exactly the failure the mechanism exists to prevent:
    the bundle keeps the linker's ad-hoc signature, the code identity changes
    again, the Accessibility grant stops applying, and the only clue is one
    warning line in a wall of green output.
    """
    script = (SRC.parent.parent / "scripts" / "install_app.sh").read_text()
    assert 'if [ "$IDENTITY" != "-" ]; then' in script
    assert "exit 1" in script
    assert "codesign failed" in script, "and show codesign's own error"
    assert '2>"$SIGNERR"' in script, "stderr must be captured, not /dev/null"
    # Comments explaining why --deep is gone are welcome; commands are not.
    commands = [
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    ]
    assert not any("--deep" in line for line in commands), (
        "--deep walks an alias bundle's symlinks out to Homebrew and this "
        "checkout, producing a signature that cannot verify -- and TCC will "
        "not hold a grant against one that does not verify"
    )
    assert "--verify --strict" in script, "verify before installing, not after"


def test_the_cert_script_tests_signing_rather_than_trusting_the_listing():
    """`find-identity` lists an untrusted self-signed cert perfectly well."""
    script = (SRC.parent.parent / "scripts" / "make_signing_cert.sh").read_text()
    assert "can_sign()" in script
    assert "repair_trust()" in script
    assert "--repair" in script, "and offer a way to fix an existing one"


def test_signing_resolves_the_identity_to_a_hash():
    """Two certificates sharing a name make codesign refuse outright:

        Aloud Dev: ambiguous (matches "Aloud Dev" and "Aloud Dev")

    Re-running a script that creates one is all it takes to get two, so the
    name is never used as the signing argument. A hash cannot be ambiguous.
    """
    install = (SRC.parent.parent / "scripts" / "install_app.sh").read_text()
    assert "awk '{print $2}'" in install, "resolve the name to a hash"
    assert "ambiguous" in install, "and say why, where the next reader will look"

    cert = (SRC.parent.parent / "scripts" / "make_signing_cert.sh").read_text()
    assert "identity_hashes()" in cert
    assert "forget_duplicates()" in cert, "and clear duplicates rather than pick"
