"""Updating in place, from the app.

Aloud runs out of a git checkout — the bundle in /Applications points at it —
so an update is a pull and a relaunch. That is the whole mechanism, and it is
deliberately not a bespoke download-and-replace: git already knows what changed,
already verifies what it fetched, and already refuses to clobber local edits.

The one wrinkle is a folder that came from a downloaded zip and has no git
history at all. Rather than declaring that unsupported, :func:`adopt` attaches
it to the repository in place, keeping the virtualenv and settings that are
already there.

No AppKit here: the dialogs live in the app layer, and this stays testable.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

REPOSITORY = "https://github.com/anthonysb90/stt-mac.git"
DEFAULT_BRANCH = "claude/whispr-dictation-app-glvd1v"

#: Long enough for a slow network, short enough that a hung remote does not
#: freeze the menu.
TIMEOUT = 60


@dataclass
class UpdateStatus:
    """What we know after asking the remote."""

    checked: bool = False
    available: bool = False
    behind: int = 0
    current: str = ""
    latest: str = ""
    branch: str = ""
    is_git: bool = False
    dirty: bool = False
    detail: str = ""
    summary: List[str] = None

    def __post_init__(self):
        if self.summary is None:
            self.summary = []

    @property
    def headline(self) -> str:
        if not self.checked:
            return self.detail or "Could not check for updates."
        if not self.available:
            return "Aloud is up to date."
        plural = "" if self.behind == 1 else "s"
        return f"{self.behind} update{plural} available."


def repo_root() -> Path:
    """The checkout the running code came from."""
    return Path(__file__).resolve().parents[2]


def _git(*args: str, cwd: Optional[Path] = None) -> Tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd or repo_root()),
            capture_output=True, text=True, timeout=TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return completed.returncode, (completed.stdout or completed.stderr).strip()


def is_git_checkout() -> bool:
    code, _ = _git("rev-parse", "--git-dir")
    return code == 0


def current_branch() -> str:
    code, out = _git("rev-parse", "--abbrev-ref", "HEAD")
    return out if code == 0 else DEFAULT_BRANCH


def has_local_changes() -> bool:
    code, out = _git("status", "--porcelain")
    return code == 0 and bool(out.strip())


def check() -> UpdateStatus:
    """Ask the remote what is there. Never raises."""
    root = repo_root()
    if not is_git_checkout():
        return UpdateStatus(
            checked=False, is_git=False,
            detail=(
                "This copy of Aloud was not installed with git, so it cannot "
                "update itself yet. Connecting it to the repository takes a "
                "moment and keeps your settings and virtualenv."
            ),
        )

    branch = current_branch()
    code, out = _git("fetch", "--quiet", "origin", branch)
    if code != 0:
        return UpdateStatus(
            checked=False, is_git=True, branch=branch,
            detail=f"Could not reach the repository:\n{out}",
        )

    _, current = _git("rev-parse", "--short", "HEAD")
    _, latest = _git("rev-parse", "--short", f"origin/{branch}")
    code, count = _git("rev-list", "--count", f"HEAD..origin/{branch}")
    behind = int(count) if code == 0 and count.isdigit() else 0

    summary: List[str] = []
    if behind:
        code, log_out = _git(
            "log", "--no-merges", "--pretty=format:%s", "-8", f"HEAD..origin/{branch}"
        )
        if code == 0 and log_out:
            summary = [line for line in log_out.splitlines() if line.strip()]

    return UpdateStatus(
        checked=True, available=behind > 0, behind=behind,
        current=current, latest=latest, branch=branch,
        is_git=True, dirty=has_local_changes(), summary=summary,
        detail=f"{root}",
    )


def adopt() -> Tuple[bool, str]:
    """Attach a zip-installed folder to the repository, in place.

    Fetch and reset rather than clone-and-copy: the virtualenv, config and
    dictionary all live in this folder and are gitignored, so they survive
    untouched.
    """
    root = repo_root()
    if is_git_checkout():
        return True, "Already a git checkout."

    for args in (
        ("init", "--quiet"),
        ("remote", "add", "origin", REPOSITORY),
        ("fetch", "--quiet", "origin", DEFAULT_BRANCH),
        ("checkout", "-f", "-B", DEFAULT_BRANCH, f"origin/{DEFAULT_BRANCH}"),
    ):
        code, out = _git(*args, cwd=root)
        if code != 0:
            return False, f"git {' '.join(args)} failed:\n{out}"
    return True, f"Connected {root} to the repository."


def apply() -> Tuple[bool, str]:
    """Fast-forward to the latest commit. Refuses rather than discards."""
    branch = current_branch()
    if has_local_changes():
        return False, (
            "There are uncommitted changes in the Aloud folder. Updating would "
            "overwrite them, so nothing has been changed."
        )
    code, out = _git("merge", "--ff-only", f"origin/{branch}")
    if code != 0:
        return False, f"Could not update:\n{out}"
    _, current = _git("rev-parse", "--short", "HEAD")
    return True, f"Updated to {current}."


def needs_rebuild(status: UpdateStatus) -> bool:
    """Whether the .app itself has to be rebuilt, not just the source.

    An alias bundle runs the source directly, so most updates need only a
    relaunch. A change to the build definition or the dependencies is the
    exception.
    """
    watched = ("setup.py", "requirements", "Aloud.py", "scripts/")
    code, out = _git("diff", "--name-only", "HEAD@{1}", "HEAD")
    if code != 0:
        return False
    return any(name.startswith(watched) or name in watched for name in out.splitlines())
