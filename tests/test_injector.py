import sys

import pytest

if sys.platform == "darwin":  # pragma: no cover - needs a real pasteboard
    pytest.skip("exercises the framework stubs, not real AppKit", allow_module_level=True)

import Quartz  # the stub installed by conftest

from murmur.injector import TextInjector, read_clipboard, write_clipboard


@pytest.fixture(autouse=True)
def clear_posted():
    Quartz.posted_events.clear()
    yield
    Quartz.posted_events.clear()


def test_clipboard_mode_writes_without_keystrokes():
    TextInjector(mode="clipboard", trailing_space=False).deliver("hello")
    assert read_clipboard() == "hello"
    assert Quartz.posted_events == []


def test_paste_mode_sets_the_clipboard_and_sends_command_v():
    TextInjector(mode="paste", restore_clipboard=False, trailing_space=False).deliver("hi")
    assert read_clipboard() == "hi"
    # One key-down and one key-up for the "v" key.
    assert [event.keycode for event in Quartz.posted_events] == [9, 9]
    assert all(event.flags & Quartz.kCGEventFlagMaskCommand for event in Quartz.posted_events)


def test_paste_mode_restores_the_previous_clipboard():
    write_clipboard("original")
    injector = TextInjector(mode="paste", restore_clipboard=True, restore_delay=0.01,
                            trailing_space=False)
    injector.deliver("dictated")
    assert read_clipboard() == "dictated"

    import time

    time.sleep(0.2)
    assert read_clipboard() == "original"


def test_type_mode_emits_the_characters_as_unicode_events():
    TextInjector(mode="type", trailing_space=False).deliver("abc")
    typed = "".join(dict.fromkeys(event.unicode for event in Quartz.posted_events))
    assert typed == "abc"


def test_trailing_space_is_appended_once():
    TextInjector(mode="clipboard", trailing_space=True).deliver("word")
    assert read_clipboard() == "word "
    TextInjector(mode="clipboard", trailing_space=True).deliver("line\n")
    assert read_clipboard() == "line\n"


def test_empty_text_is_a_no_op():
    write_clipboard("untouched")
    TextInjector(mode="paste").deliver("")
    assert read_clipboard() == "untouched"
    assert Quartz.posted_events == []
