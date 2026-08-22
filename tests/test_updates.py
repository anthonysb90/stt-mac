"""Updating in place — what it reports, and what it refuses to do."""

import subprocess

import pytest

from aloud import updates


@pytest.fixture
def fake_git(monkeypatch):
    """Drive updates.py by scripting git's answers."""
    calls = []
    answers = {}

    def run(*args, cwd=None):
        calls.append(args)
        for prefix, result in answers.items():
            if args[: len(prefix)] == prefix:
                return result
        return (0, "")

    monkeypatch.setattr(updates, "_git", run)
    return calls, answers


# -- reporting ---------------------------------------------------------------


def test_a_folder_without_git_is_offered_a_way_forward(fake_git):
    _, answers = fake_git
    answers[("rev-parse", "--git-dir")] = (1, "not a git repository")
    status = updates.check()
    assert not status.checked and not status.is_git
    assert "settings and virtualenv" in status.detail


def test_an_unreachable_remote_is_reported_not_raised(fake_git):
    _, answers = fake_git
    answers[("fetch",)] = (1, "Could not resolve host")
    status = updates.check()
    assert not status.checked
    assert "Could not resolve host" in status.detail


def test_being_up_to_date_says_so(fake_git):
    _, answers = fake_git
    answers[("rev-list", "--count")] = (0, "0")
    status = updates.check()
    assert status.checked and not status.available
    assert status.headline == "Aloud is up to date."


def test_available_updates_are_counted_and_summarised(fake_git):
    _, answers = fake_git
    answers[("rev-list", "--count")] = (0, "3")
    answers[("log",)] = (0, "Fix the menu bar icon\nAdd drag and drop\nChange the sounds")
    status = updates.check()
    assert status.available and status.behind == 3
    assert status.headline == "3 updates available."
    assert "Add drag and drop" in status.summary


def test_a_single_update_is_not_pluralised(fake_git):
    _, answers = fake_git
    answers[("rev-list", "--count")] = (0, "1")
    assert updates.check().headline == "1 update available."


def test_unparseable_counts_do_not_crash_the_check(fake_git):
    _, answers = fake_git
    answers[("rev-list", "--count")] = (0, "not a number")
    assert updates.check().behind == 0


# -- applying ----------------------------------------------------------------


def test_local_changes_block_the_update_rather_than_being_discarded(fake_git):
    _, answers = fake_git
    answers[("status", "--porcelain")] = (0, " M src/aloud/app.py")
    ok, message = updates.apply()
    assert not ok
    assert "nothing has been changed" in message


def test_a_clean_checkout_fast_forwards(fake_git):
    calls, answers = fake_git
    answers[("status", "--porcelain")] = (0, "")
    answers[("rev-parse", "--short", "HEAD")] = (0, "abc1234")
    ok, message = updates.apply()
    assert ok and "abc1234" in message
    assert any(c[:2] == ("merge", "--ff-only") for c in calls), "must not be a rebase or reset"


def test_a_failed_merge_reports_git_s_own_words(fake_git):
    _, answers = fake_git
    answers[("status", "--porcelain")] = (0, "")
    answers[("merge",)] = (1, "fatal: Not possible to fast-forward")
    ok, message = updates.apply()
    assert not ok and "fast-forward" in message


# -- adopting a zip install --------------------------------------------------


def test_adopting_runs_the_expected_sequence(fake_git):
    calls, answers = fake_git
    answers[("rev-parse", "--git-dir")] = (1, "not a git repository")
    ok, message = updates.adopt()
    assert ok
    performed = [c[0] for c in calls]
    assert performed[1:] == ["init", "remote", "fetch", "checkout"]


def test_adopting_stops_at_the_first_failure(fake_git):
    calls, answers = fake_git
    answers[("rev-parse", "--git-dir")] = (1, "no")
    answers[("fetch",)] = (1, "Could not resolve host")
    ok, message = updates.adopt()
    assert not ok and "Could not resolve host" in message
    assert not any(c[0] == "checkout" for c in calls), "must not check out after a failed fetch"


def test_adopting_an_existing_checkout_is_a_no_op(fake_git):
    calls, _ = fake_git
    ok, _ = updates.adopt()
    assert ok
    assert not any(c[0] == "init" for c in calls)


# -- rebuild detection -------------------------------------------------------


@pytest.mark.parametrize(
    "changed,expected",
    [("src/aloud/app.py\n", False),
     ("setup.py\n", True),
     ("requirements.txt\n", True),
     ("scripts/build_app.sh\n", True),
     ("docs/INSTALL.md\nsetup.py\n", True)],
)
def test_only_build_affecting_changes_ask_for_a_rebuild(fake_git, changed, expected):
    _, answers = fake_git
    answers[("diff", "--name-only")] = (0, changed)
    assert updates.needs_rebuild(updates.UpdateStatus()) is expected


# -- the real thing ----------------------------------------------------------


def test_git_failures_are_turned_into_return_values(monkeypatch):
    def explode(*_args, **_kwargs):
        raise OSError("git not installed")

    monkeypatch.setattr(subprocess, "run", explode)
    code, out = updates._git("status")
    assert code == 1 and "git not installed" in out
