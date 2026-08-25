import sys

import pytest

if sys.platform == "darwin":  # pragma: no cover - needs a real pasteboard
    pytest.skip("exercises the framework stubs, not real AppKit", allow_module_level=True)

import Quartz  # the stub installed by conftest

from aloud.injector import TextInjector, read_clipboard, write_clipboard


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


# -- non-text clipboard contents must survive dictation ----------------------


def _put_image_on_clipboard():
    """Simulate a copied screenshot: a PNG item with no string representation."""
    import AppKit

    item = AppKit.NSPasteboardItem.alloc().init()
    item.setData_forType_(b"\x89PNG fake image bytes", "public.png")
    pasteboard = AppKit.NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    pasteboard.writeObjects_([item])
    return item


def test_paste_mode_restores_a_copied_image(monkeypatch):
    """A screenshot on the clipboard used to be silently destroyed.

    read_clipboard() returned None for it, so nothing was restored despite
    restore_clipboard=True. The snapshot has to capture every representation
    of every item, not just the string.
    """
    import AppKit
    from aloud import injector

    monkeypatch.setattr(injector.time, "sleep", lambda _s: None)
    _put_image_on_clipboard()

    delivered = injector.TextInjector(mode="paste", restore_delay=0)
    delivered.deliver("hello world")

    # The restore runs on a background thread; with sleep stubbed out it can
    # finish before control returns here, so only the end state is assertable:
    # the image is back, byte for byte.
    for thread in list(__import__("threading").enumerate()):
        if thread.name == "aloud-clipboard":
            thread.join(timeout=2)
    items = AppKit.NSPasteboard.generalPasteboard().pasteboardItems()
    assert len(items) == 1
    assert items[0].dataForType_("public.png") == b"\x89PNG fake image bytes"


def test_snapshot_of_an_empty_clipboard_is_none():
    import AppKit
    from aloud import injector

    AppKit.NSPasteboard.generalPasteboard().clearContents()
    assert injector.snapshot_clipboard() is None


def test_snapshot_round_trips_multiple_representations():
    import AppKit
    from aloud import injector

    item = AppKit.NSPasteboardItem.alloc().init()
    item.setData_forType_(b"<b>rich</b>", "public.html")
    item.setData_forType_(b"rich", "public.utf8-plain-text")
    pasteboard = AppKit.NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    pasteboard.writeObjects_([item])

    snapshot = injector.snapshot_clipboard()
    injector.write_clipboard("overwritten")
    injector.restore_clipboard(snapshot)

    restored = pasteboard.pasteboardItems()[0]
    assert restored.dataForType_("public.html") == b"<b>rich</b>"
    assert restored.dataForType_("public.utf8-plain-text") == b"rich"
