from aloud import hotkey as hotkey_mod
from aloud.hotkey import MODIFIER_KEYS, HotkeyError, HotkeyListener

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


# -- one-sided keys must track their own physical bit ------------------------


class _FlagsEvent:
    """A fake CGEvent carrying a keycode and a flag word."""

    def __init__(self, keycode, flags):
        self.keycode = keycode
        self.flags = flags


def _drive(listener, keycode, flags, monkeypatch):
    """Push one flags-changed event through the real _handle path."""
    monkeypatch.setattr(
        hotkey_mod.Quartz, "CGEventGetIntegerValueField",
        lambda event, _field: event.keycode, raising=False,
    )
    monkeypatch.setattr(
        hotkey_mod.Quartz, "CGEventGetFlags",
        lambda event: event.flags, raising=False,
    )
    listener._handle(None, hotkey_mod.Quartz.kCGEventFlagsChanged,
                     _FlagsEvent(keycode, flags), None)


def test_release_is_seen_while_the_other_option_is_held(monkeypatch):
    """Right Option up + left Option still down used to eat the release.

    Both sides share kCGEventFlagMaskAlternate, so testing that mask reported
    "still down" and the recording ran until the max-duration timer. The
    device-specific bit (NX_DEVICERALTKEYMASK, 0x40) is this key's own.
    """
    listener, recorder = _listener("hold", key="right_option")
    right, left = 0x40, 0x20
    shared = MODIFIER_KEYS["right_option"][1]
    keycode = MODIFIER_KEYS["right_option"][0]

    _drive(listener, keycode, shared | right, monkeypatch)          # right down
    _drive(listener, keycode, shared | right | left, monkeypatch)   # left joins
    # Right released; the shared bit stays set because left is still down.
    _drive(listener, keycode, shared | left, monkeypatch)
    assert recorder.events == ["press", "release"]


def test_every_sided_key_has_a_device_bit():
    for name in MODIFIER_KEYS:
        if name != "fn":
            assert name in hotkey_mod.DEVICE_KEY_MASKS, name
    assert "fn" not in hotkey_mod.DEVICE_KEY_MASKS, "fn has no sided variant"


def test_left_and_right_device_bits_are_disjoint():
    left = hotkey_mod.DEVICE_KEY_MASKS["left_option"]
    right = hotkey_mod.DEVICE_KEY_MASKS["right_option"]
    assert left & right == 0


def test_fn_still_uses_the_public_mask():
    listener, _ = _listener("hold", key="fn")
    assert listener._mask == MODIFIER_KEYS["fn"][1]
