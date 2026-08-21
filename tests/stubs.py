"""Minimal stand-ins for the macOS frameworks.

They exist so the wiring in :mod:`murmur.app` can be exercised on a machine
without AppKit -- CI, a Linux box, a container. They implement only what Murmur
actually calls, and are installed by ``conftest.py`` only when the real
frameworks are unavailable.
"""

from __future__ import annotations

import sys
import types
from typing import Any, Callable, Dict, List, Optional


class _Const(int):
    """An int that also carries a name, standing in for a CF/CG constant."""


def _module(name: str, **attributes: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


# -- Quartz ----------------------------------------------------------------

_QUARTZ_CONSTANTS = {
    "kCGEventFlagMaskSecondaryFn": 0x800000,
    "kCGEventFlagMaskShift": 0x20000,
    "kCGEventFlagMaskControl": 0x40000,
    "kCGEventFlagMaskAlternate": 0x80000,
    "kCGEventFlagMaskCommand": 0x100000,
    "kCGEventTapDisabledByTimeout": 0xFFFFFFFE,
    "kCGEventTapDisabledByUserInput": 0xFFFFFFFF,
    "kCGEventFlagsChanged": 12,
    "kCGSessionEventTap": 1,
    "kCGHeadInsertEventTap": 0,
    "kCGEventTapOptionListenOnly": 1,
    "kCGKeyboardEventKeycode": 9,
    "kCFRunLoopCommonModes": "common",
    "kCGHIDEventTap": 0,
    "kCGEventSourceStateHIDSystemState": 1,
}


class FakeEvent:
    def __init__(self, keycode: int = 0, flags: int = 0) -> None:
        self.keycode = keycode
        self.flags = flags
        self.unicode = ""


def _make_quartz() -> types.ModuleType:
    posted: List[FakeEvent] = []

    def CGEventTapCreate(*_args, **_kwargs):
        return object()

    def CGEventCreateKeyboardEvent(_source, keycode, _is_down):
        return FakeEvent(keycode=keycode)

    def CGEventKeyboardSetUnicodeString(event, _length, text):
        event.unicode = text

    quartz = _module(
        "Quartz",
        CGEventMaskBit=lambda bit: 1 << bit,
        CGEventTapCreate=CGEventTapCreate,
        CFMachPortCreateRunLoopSource=lambda *_: object(),
        CFRunLoopGetCurrent=lambda: object(),
        CFRunLoopGetMain=lambda: object(),
        CFRunLoopAddSource=lambda *_: None,
        CFRunLoopRemoveSource=lambda *_: None,
        CGEventTapEnable=lambda *_: None,
        CGEventGetIntegerValueField=lambda event, _field: event.keycode,
        CGEventGetFlags=lambda event: event.flags,
        CGEventSourceCreate=lambda _state: object(),
        CGEventCreateKeyboardEvent=CGEventCreateKeyboardEvent,
        CGEventKeyboardSetUnicodeString=CGEventKeyboardSetUnicodeString,
        CGEventSetFlags=lambda event, flags: setattr(event, "flags", flags),
        CGEventPost=lambda _tap, event: posted.append(event),
        posted_events=posted,
        **_QUARTZ_CONSTANTS,
    )
    return quartz


# -- AppKit / Foundation ---------------------------------------------------


class _FakePasteboard:
    _contents: Optional[str] = None

    @classmethod
    def generalPasteboard(cls) -> "_FakePasteboard":
        return cls()

    def clearContents(self) -> None:
        type(self)._contents = None

    def setString_forType_(self, value: str, _type: str) -> None:
        type(self)._contents = value

    def stringForType_(self, _type: str) -> Optional[str]:
        return type(self)._contents


class _FakeSound:
    @staticmethod
    def soundNamed_(_name: str):
        return None


def _make_appkit() -> types.ModuleType:
    return _module(
        "AppKit",
        NSPasteboard=_FakePasteboard,
        NSPasteboardTypeString="public.utf8-plain-text",
        NSSound=_FakeSound,
    )


class _InlineQueue:
    """Runs blocks immediately instead of hopping to a run loop."""

    @classmethod
    def mainQueue(cls) -> "_InlineQueue":
        return cls()

    def addOperationWithBlock_(self, block: Callable[[], None]) -> None:
        block()


def _make_foundation() -> types.ModuleType:
    return _module(
        "Foundation",
        NSOperationQueue=_InlineQueue,
        NSDictionary=types.SimpleNamespace(
            dictionaryWithObject_forKey_=lambda value, key: {key: value}
        ),
    )


# -- rumps ------------------------------------------------------------------


class FakeMenuItem:
    def __init__(self, title: str, callback: Optional[Callable] = None) -> None:
        self.title = title
        self.callback = callback
        self.children: List["FakeMenuItem"] = []

    def set_callback(self, callback: Optional[Callable]) -> None:
        self.callback = callback

    def add(self, item: "FakeMenuItem") -> None:
        self.children.append(item)


class FakeApp:
    def __init__(self, name: str, title: str = "", quit_button: Any = None) -> None:
        self.name = name
        self.title = title
        self.quit_button = quit_button
        self.menu: List[Any] = []
        self.ran = False

    def run(self) -> None:
        self.ran = True


def _make_rumps() -> types.ModuleType:
    alerts: List[tuple] = []
    notifications: List[tuple] = []

    class FakeTimer:
        def __init__(self, callback, interval):
            self.callback = callback
            self.interval = interval

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    return _module(
        "rumps",
        App=FakeApp,
        MenuItem=FakeMenuItem,
        Timer=FakeTimer,
        alert=lambda *args, **kwargs: alerts.append((args, kwargs)),
        notification=lambda **kwargs: notifications.append(kwargs),
        quit_application=lambda: None,
        recorded_alerts=alerts,
        recorded_notifications=notifications,
    )


# -- other frameworks -------------------------------------------------------


def _make_application_services() -> types.ModuleType:
    return _module(
        "ApplicationServices",
        AXIsProcessTrustedWithOptions=lambda _options: True,
        kAXTrustedCheckOptionPrompt="AXTrustedCheckOptionPrompt",
    )


def _make_avfoundation() -> types.ModuleType:
    return _module(
        "AVFoundation",
        AVMediaTypeAudio="soun",
        AVAuthorizationStatusNotDetermined=0,
        AVAuthorizationStatusRestricted=1,
        AVAuthorizationStatusDenied=2,
        AVAuthorizationStatusAuthorized=3,
        AVCaptureDevice=types.SimpleNamespace(
            authorizationStatusForMediaType_=lambda _type: 3,
            requestAccessForMediaType_completionHandler_=lambda _type, handler: handler(True),
        ),
    )


def _make_sounddevice() -> types.ModuleType:
    return _module(
        "sounddevice",
        RawInputStream=lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("no audio device in the test environment")
        ),
        query_devices=lambda: [],
    )


STUBS: Dict[str, Callable[[], types.ModuleType]] = {
    "Quartz": _make_quartz,
    "AppKit": _make_appkit,
    "Foundation": _make_foundation,
    "rumps": _make_rumps,
    "ApplicationServices": _make_application_services,
    "AVFoundation": _make_avfoundation,
    "sounddevice": _make_sounddevice,
}


def install() -> List[str]:
    """Register any framework that is not already importable. Returns the names used."""
    installed = []
    for name, factory in STUBS.items():
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            sys.modules[name] = factory()
            installed.append(name)
    return installed
