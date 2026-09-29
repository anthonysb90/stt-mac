"""Spoken commands, snippets, and formatting per app."""

import pytest

from aloud import voice
from aloud.core import Job

from test_core_pipeline import _silent_wav, app  # noqa: F401  (fixture)


@pytest.mark.parametrize("text", ["Scratch that.", "scratch that", "Delete that!", "undo that"])
def test_scratch_phrases(text):
    assert voice.is_scratch(text)


@pytest.mark.parametrize("text", ["Scratch that idea from the list.", "that", ""])
def test_scratch_only_when_said_on_its_own(text):
    assert not voice.is_scratch(text)


SNIPPETS = {"my signature": "Pastor Anthony\nMission USA",
            "my address": "123 Main St", "my address line two": "Suite 4"}


@pytest.mark.parametrize("said,typed", [
    ("Insert my signature.", "Pastor Anthony\nMission USA"),
    ("my signature", "Pastor Anthony\nMission USA"),
    ("Thanks for coming. Insert my signature", "Thanks for coming. Pastor Anthony\nMission USA"),
    ("insert my address line two", "Suite 4"),
    ("Sign it with my signature please", "Sign it with my signature please"),
])
def test_snippets(said, typed):
    assert voice.expand_snippets(said, SNIPPETS) == typed


def test_styles():
    assert voice.style_for("com.apple.MobileSMS") == "chat"
    assert voice.style_for("com.apple.mail") == "prose"
    assert voice.style_for("com.apple.mail", {"com.apple.mail": "chat"}) == "chat"
    assert voice.style_for("com.apple.MobileSMS", {"com.apple.MobileSMS": "nonsense"}) == "chat"
    assert voice.apply_style("See you soon.", "chat") == "See you soon"
    assert voice.apply_style("Really?", "chat") == "Really?"
    assert voice.apply_style("Well...", "chat") == "Well..."
    assert voice.apply_style("Git status.", "code") == "git status"
    assert voice.apply_style("NASA data.", "code") == "NASA data"
    assert voice.apply_style("Dear Tim,", "prose") == "Dear Tim,"
    assert voice.trailing_space("code", True) is False


# -- in the dictation pipeline ---------------------------------------------------


def dictate(app, tmp_path, text, bundle="com.apple.mail"):
    app.config.set("engines.mock.text", text)
    app.engine = __import__("aloud.engines", fromlist=["build"]).build(
        "mock", app.config.engine_options("mock"))
    import aloud.core as core

    core.injector_frontmost = lambda: (bundle, 42)
    app._run_job(Job(audio=_silent_wav(tmp_path / "d.wav"), deliver=True))


@pytest.fixture(autouse=True)
def restore_frontmost():
    import aloud.core as core

    real = core.injector_frontmost
    yield
    core.injector_frontmost = real


def test_scratch_that_removes_the_last_dictation_and_types_nothing(app, tmp_path):
    dictate(app, tmp_path, "Scratch that.")
    assert app.injector.delivered == [] and app.injector.undone == 1


def test_a_snippet_is_typed_in_place_of_its_trigger(app, tmp_path):
    app.config.set("snippets", {"my signature": "Pastor Anthony"})
    dictate(app, tmp_path, "insert my signature")
    assert app.injector.delivered == ["Pastor Anthony"]


def test_chat_apps_get_no_final_period(app, tmp_path):
    dictate(app, tmp_path, "See you at church.", bundle="com.apple.MobileSMS")
    assert app.injector.delivered == ["See you at church"]
    dictate(app, tmp_path, "See you at church.", bundle="com.apple.mail")
    assert app.injector.delivered[-1] == "See you at church."


def test_undo_last_is_refused_in_another_app_or_after_a_minute(monkeypatch):
    import time

    from aloud import injector

    inj = injector.TextInjector(mode="paste", restore_clipboard=False)
    monkeypatch.setattr(injector, "frontmost_app", lambda: ("com.apple.mail", 1))
    monkeypatch.setattr(injector, "send_command_v", lambda: None)
    sent = []
    monkeypatch.setattr(injector, "_send_key", lambda code, flags=0: sent.append((code, flags)))
    inj.deliver("hello")
    monkeypatch.setattr(injector, "frontmost_app", lambda: ("com.apple.Safari", 2))
    assert inj.undo_last() == "a different app is in front" and sent == []

    monkeypatch.setattr(injector, "frontmost_app", lambda: ("com.apple.mail", 1))
    inj.last_delivery["at"] = time.monotonic() - 120
    assert inj.undo_last() == "too long ago"

    inj.deliver("hello")
    assert inj.undo_last() == "removed" and sent[-1][0] == injector._Z_KEYCODE
    assert inj.undo_last() == "nothing to scratch", "only once"
