"""Minimal stand-ins for the macOS frameworks.

They exist so the wiring in :mod:`aloud.app` can be exercised on a machine
without AppKit -- CI, a Linux box, a container. They implement only what Aloud
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


class _FakePasteboardItem:
    """One pasteboard item: a dict of type -> bytes, like the real thing."""

    @classmethod
    def alloc(cls) -> type:
        return cls

    @classmethod
    def init(cls) -> "_FakePasteboardItem":
        return cls({})

    def __init__(self, data: Optional[Dict[str, bytes]] = None) -> None:
        self._data: Dict[str, bytes] = dict(data or {})

    def types(self) -> List[str]:
        return list(self._data)

    def dataForType_(self, kind: str) -> Optional[bytes]:
        return self._data.get(kind)

    def setData_forType_(self, data: bytes, kind: str) -> None:
        self._data[kind] = bytes(data)


class _FakePasteboard:
    """Item-based, like NSPasteboard: a string is one item with a string type."""

    STRING_TYPE = "public.utf8-plain-text"
    _items: List[_FakePasteboardItem] = []

    @classmethod
    def generalPasteboard(cls) -> "_FakePasteboard":
        return cls()

    def clearContents(self) -> None:
        type(self)._items = []

    def setString_forType_(self, value: str, kind: str) -> None:
        type(self)._items = [_FakePasteboardItem({kind: value.encode("utf-8")})]

    def stringForType_(self, kind: str) -> Optional[str]:
        for item in type(self)._items:
            data = item.dataForType_(kind)
            if data is not None:
                return data.decode("utf-8")
        return None

    def pasteboardItems(self) -> List[_FakePasteboardItem]:
        return list(type(self)._items)

    def writeObjects_(self, items: List[_FakePasteboardItem]) -> bool:
        type(self)._items = list(items)
        return True


class _FakeSound:
    @staticmethod
    def soundNamed_(_name: str):
        return None


class _FakeApplication:
    """Just enough NSApplication to exercise the activation policy.

    Shared, like the real one: `sharedApplication()` always hands back the same
    object, so a test can set a policy through one call and read it through
    another. `reset()` puts it back to Regular between tests.
    """

    #: Real values, so a mistake here would also be a mistake on a Mac.
    REGULAR = 0
    ACCESSORY = 1

    _instance: Optional["_FakeApplication"] = None

    def __init__(self) -> None:
        self.policy = self.REGULAR
        self.activations = 0

    @classmethod
    def sharedApplication(cls) -> "_FakeApplication":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def activationPolicy(self) -> int:
        return self.policy

    def setActivationPolicy_(self, policy: int) -> bool:
        self.policy = policy
        return True

    def activateIgnoringOtherApps_(self, _flag: bool) -> None:
        self.activations += 1


def _make_appkit() -> types.ModuleType:
    return _module(
        "AppKit",
        NSPasteboard=_FakePasteboard,
        NSPasteboardItem=_FakePasteboardItem,
        NSPasteboardTypeString="public.utf8-plain-text",
        NSSound=_FakeSound,
        NSApplication=_FakeApplication,
        NSApplicationActivationPolicyRegular=_FakeApplication.REGULAR,
        NSApplicationActivationPolicyAccessory=_FakeApplication.ACCESSORY,
        NSControlStateValueOn=1,
        NSControlStateValueOff=0,
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
        NSData=types.SimpleNamespace(
            dataWithBytes_length_=lambda payload, _length: bytes(payload)
        ),
        NSDictionary=types.SimpleNamespace(
            dictionaryWithObject_forKey_=lambda value, key: {key: value}
        ),
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
