from murmur import hotkey as hotkey_mod
from murmur.hotkey import MODIFIER_KEYS, HotkeyError, HotkeyListener

import pytest


class Recorder:
    def __init__(self):
        self.events = []

    def press(self):
        self.events.append("press")

    def release(self):
        self.events.append("release")


def _listener(mode="hold", key="right_option"):
    recorder = Recorder()
    return HotkeyListener(key, recorder.press, recorder.release, mode=mode), recorder


def test_unknown_key_is_rejected():
    with pytest.raises(HotkeyError):
        HotkeyListener("banana", lambda: None, lambda: None)


def test_left_and_right_variants_have_distinct_keycodes():
    assert MODIFIER_KEYS["left_option"][0] != MODIFIER_KEYS["right_option"][0]
    assert MODIFIER_KEYS["left_option"][1] == MODIFIER_KEYS["right_option"][1]


def test_hold_mode_reports_both_edges():
    listener, recorder = _listener("hold")
    listener._dispatch(True)
    listener._dispatch(False)
    assert recorder.events == ["press", "release"]


def test_repeated_flag_events_are_collapsed():
    listener, recorder = _listener("hold")
    listener._dispatch(True)
    listener._dispatch(True)
    listener._dispatch(False)
    assert recorder.events == ["press", "release"]


def test_toggle_mode_alternates_on_press_only():
    listener, recorder = _listener("toggle")
    for is_down in (True, False, True, False):
        listener._dispatch(is_down)
    assert recorder.events == ["press", "release"]
    assert listener.active is False


def test_reset_clears_toggle_state():
    listener, _ = _listener("toggle")
    listener._dispatch(True)
    assert listener.active is True
    listener.reset()
    assert listener.active is False


def test_describe_produces_readable_labels():
    assert hotkey_mod.describe("right_option") == "Right Option"
    assert hotkey_mod.describe("fn") == "Fn"
