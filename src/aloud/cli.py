"""Command line entry point.

Without arguments this launches the menu bar app. The subcommands exist so the
pipeline can be exercised piece by piece over SSH or in CI, where there is no
GUI and no permission grants.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, __version__, build_id, engines, logging_setup, toolpath
from .config import Config
from .paths import CONFIG_FILE, LOG_FILE, ensure_dirs


def _cmd_doctor(config: Config) -> int:
    import platform

    from . import permissions
    from .hotkey import describe

    configured = str(config.get("engine", engines.AUTO))
    selected = engines.select(config)

    print(f"{APP_NAME} {__version__} (build {build_id()})")
    print(f"  machine     {platform.machine()} · macOS {platform.mac_ver()[0] or 'n/a'}")
    print(f"  python      {sys.version.split()[0]}")
    print(f"  config      {CONFIG_FILE}")
    print(f"  log         {LOG_FILE}")
    print(f"  hotkey      {config.get('hotkey.mode')} {describe(config.get('hotkey.key'))}")
    print(f"  permissions {permissions.summary()}")
    # Printed because the app and the terminal can disagree about this, and
    # when they do the app says "ffmpeg not found" on a machine that has it.
    from .media import ffmpeg_path

    print(f"  ffmpeg      {ffmpeg_path() or 'not found'}")
    print(f"  encoding    {toolpath.repair_locale()}")
    print("  dock icon   "
          + ("shown" if config.get("interface.dock_icon", True)
             else "hidden (menu bar only)"))
    if configured == engines.AUTO:
        print(f"  engine      auto -> {selected.name}"
              f"  (order: {' > '.join(engines.preferences())})")
    else:
        print(f"  engine      {configured}")

    failures = 0
    for name in engines.names():
        engine = engines.build(name, config.engine_options(name))
        ok, detail = engine.check()
        marker = "ok " if ok else "-- "
        active = " (active)" if name == selected.name else ""
        print(f"  {marker}{name:<16}{detail}{active}")
        if not ok and name == selected.name:
            failures += 1

    try:
        from .audio import list_input_devices

        print("  inputs      " + ", ".join(d["name"] for d in list_input_devices()))
    except Exception as exc:
        print(f"  inputs      unavailable ({exc})")
        failures += 1

    return 1 if failures else 0


def _cmd_tap_test(seconds: float) -> int:
    """Watch the event tap and report what actually reaches it.

    Run it from inside the bundle, which is the whole point::

        /Applications/Aloud.app/Contents/MacOS/Aloud tap-test

    Permissions attach to the app's code identity, so running this from a
    terminal answers a different question than the one being asked -- it would
    report Terminal's access, not Aloud's.

    Prints one line per modifier event with the app that had focus at the time,
    which separates the two failures that look identical from the outside: a
    tap receiving nothing at all, and a tap receiving only its own app's
    events (what an untrusted process gets, rather than the NULL the
    documentation implies).
    """
    import time

    import Quartz

    from . import permissions
    from .hotkey import MODIFIER_KEYS

    print(f"{APP_NAME} {__version__} (build {build_id()})")
    bundle = permissions.bundle_path()
    print(f"  bundle      {bundle or 'NOT IN A BUNDLE — run the copy in /Applications'}")
    print(f"  signature   {permissions.signing_identity() or 'unknown'}")
    valid, why = permissions.signature_valid()
    print(f"  signature ok {'yes' if valid else 'NO — ' + why}")
    trusted = permissions.accessibility_trusted()
    print(f"  trusted     {trusted}")
    if not valid:
        print("\n  A signature that does not verify cannot hold a TCC grant, so")
        print("  Accessibility will never stick until this is fixed.")
    if not trusted:
        print("\n  AXIsProcessTrusted says no. Everything below will be empty.")

    def frontmost() -> str:
        try:
            import AppKit

            app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
            return str(app.localizedName()) if app is not None else "?"
        except Exception:
            return "?"

    seen = {"total": 0, "foreign": 0}
    watched = {code: name for name, (code, _mask) in MODIFIER_KEYS.items()}

    def handle(_proxy, event_type, event, _refcon):
        if event_type != Quartz.kCGEventFlagsChanged:
            return event
        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )
        name = watched.get(keycode)
        if name is None:
            return event
        front = frontmost()
        seen["total"] += 1
        if front != APP_NAME:
            seen["foreign"] += 1
        flags = Quartz.CGEventGetFlags(event)
        print(f"  {name:<14} flags=0x{flags:08x}  frontmost={front}")
        return event

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged),
        handle,
        None,
    )
    if tap is None:
        print("\n  CGEventTapCreate returned NULL — the tap could not be made at all.")
        return 1

    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(
        Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
    )
    Quartz.CGEventTapEnable(tap, True)
    print(f"  tap         created and enabled")
    print(f"\nPress and release your hotkey a few times — in Aloud, then in another")
    print(f"app (a browser, Notes, anything). Listening for {seconds:.0f} seconds.\n")

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.25, False)
        if not Quartz.CGEventTapIsEnabled(tap):
            print("  ! the system disabled the tap; re-enabling")
            Quartz.CGEventTapEnable(tap, True)

    print(f"\n  {seen['total']} modifier events seen, "
          f"{seen['foreign']} of them while another app had focus.")
    if seen["total"] == 0:
        print("  Verdict: the tap receives nothing. Accessibility is not in effect")
        print("           for this build, whatever System Settings shows.")
        return 1
    if seen["foreign"] == 0:
        print("  Verdict: own-app events only — the classic signature of a tap")
        print("           created without Accessibility. The grant in System")
        print("           Settings belongs to a different build of Aloud.")
        return 1
    print("  Verdict: the tap sees other apps. The hotkey plumbing is fine.")
    return 0


def _cmd_warm(config: Config) -> int:
    """Load the selected engine now, downloading weights if needed."""
    import time

    engine = engines.select(config)
    ok, detail = engine.check()
    if not ok:
        print(f"{engine.name}: {detail}", file=sys.stderr)
        return 1
    print(f"Warming {engine.label}… (first run downloads the model)")
    started = time.monotonic()
    engine.warm_up()
    ok, detail = engine.check()
    print(f"  {detail} in {time.monotonic() - started:.1f}s")
    return 0 if ok else 1


def _cmd_transcribe(config: Config, wav: Path) -> int:
    from .postprocess import process

    if not wav.is_file():
        print(f"No such file: {wav}", file=sys.stderr)
        return 2
    engine = engines.select(config)
    try:
        transcript = engine.transcribe(wav)
    except engines.EngineError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(process(transcript.text, config.get("postprocess", {})))
    print(
        f"[{transcript.engine} · {transcript.duration:.2f}s]",
        file=sys.stderr,
    )
    return 0


def _cmd_key(name: str, value: str, forget: bool) -> int:
    """Store, show, or remove an API key for a cloud engine."""
    import getpass

    from . import secrets

    if name not in engines.names():
        print(f"Unknown engine {name!r}. Try: {', '.join(engines.names())}", file=sys.stderr)
        return 2

    if forget:
        removed = secrets.forget_key(name)
        print(f"Removed the stored {name} key." if removed else f"No stored {name} key.")
        return 0

    if not value:
        value = getpass.getpass(f"{name} API key (input hidden): ")
    if not value.strip():
        print("Nothing entered; no key stored.", file=sys.stderr)
        return 1

    path = secrets.store_key(name, value)
    print(f"Stored the {name} key in {path} (readable only by you).")
    print("The app reads it from there, so this works when launched from the Dock.")
    return 0


def _cmd_history(limit: int) -> int:
    from . import history

    for entry in history.recent(limit):
        print(f"{entry.get('at')}  {entry.get('text')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aloud", description=f"{APP_NAME} dictation")
    parser.add_argument(
        "--version", action="version",
        version=f"{APP_NAME} {__version__} (build {build_id()})",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="launch the menu bar app (default)")
    sub.add_parser("doctor", help="report engine, device, and permission status")
    sub.add_parser("warm", help="load the selected engine, downloading its model")
    tap = sub.add_parser(
        "tap-test", help="report what the global hotkey tap actually receives"
    )
    tap.add_argument("--seconds", type=float, default=20.0)
    transcribe = sub.add_parser("transcribe", help="transcribe a WAV file and print it")
    transcribe.add_argument("wav", type=Path)
    hist = sub.add_parser("history", help="show recent dictations")
    hist.add_argument("-n", "--limit", type=int, default=10)
    key = sub.add_parser("key", help="store an API key for a cloud engine")
    key.add_argument("engine", help="deepgram or openai")
    key.add_argument("value", nargs="?", default="", help="the key; prompted for if omitted")
    key.add_argument("--forget", action="store_true", help="remove the stored key")

    args = parser.parse_args(argv)

    ensure_dirs()
    config = Config.load()
    logging_setup.configure(config.get("logging.level", "INFO"))
    # Before any engine is built: `check()` asks whether ffmpeg exists, and the
    # answer has to be the same here as it is in the app.
    toolpath.repair(config.get("tools.path_extra", []))
    toolpath.repair_locale()

    if args.command == "doctor":
        return _cmd_doctor(config)
    if args.command == "warm":
        return _cmd_warm(config)
    if args.command == "tap-test":
        return _cmd_tap_test(args.seconds)
    if args.command == "transcribe":
        return _cmd_transcribe(config, args.wav)
    if args.command == "history":
        return _cmd_history(args.limit)
    if args.command == "key":
        return _cmd_key(args.engine, args.value, args.forget)

    # The import is inside the guard on purpose: the failure this exists for
    # was PyObjC rejecting a class at definition time, so `aloud.app` never
    # finished importing and nothing inside it could report anything.
    try:
        from .app import run

        return run(config)
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - catching it is the point
        from .launch import report

        report(exc)
        return 1
