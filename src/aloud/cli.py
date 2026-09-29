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
    file_engine = str(config.get("file_engine", engines.SAME) or engines.SAME)
    print(f"  files       {'same as dictation' if file_engine == engines.SAME else file_engine}")

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


def _cmd_transcribe(config: Config, path: Path, engine_name: str, fmt: str,
                    output: Path | None) -> int:
    """Transcribe any audio or video file, through the same path as the app.

    Uses the file engine unless ``--engine`` names another, applies the
    Dictionary's corrections, and writes any export format.
    """
    from . import export, media
    from .core import DictationController, Job
    from .engines.base import EngineError

    if not path.is_file():
        print(f"No such file: {path}", file=sys.stderr)
        return 2
    target_format = export.BY_KEY.get(fmt)
    if target_format is None:
        print(f"Unknown format {fmt!r}. Try: {', '.join(export.BY_KEY)}", file=sys.stderr)
        return 2
    if engine_name:
        config.set("file_engine", engine_name)

    # The controller without start(): no hotkey, no worker thread, no warm-up.
    controller = DictationController(config)
    engine = controller.files_engine()
    ok, detail = engine.check()
    if not ok:
        print(f"{engine.name}: {detail}", file=sys.stderr)
        return 1

    results: dict = {}

    class _Listener:
        def on_result(self, dictation):
            results["dictation"] = dictation

        def on_error(self, title, message):
            results["error"] = f"{title}: {message}"

        def on_progress(self, _job, _text, done, total):
            if total > 0 and sys.stderr.isatty():
                print(f"\r  {done / total:6.1%}", end="", file=sys.stderr, flush=True)

    controller.add_observer(_Listener())
    print(f"Transcribing {path.name} with {engine.label}…", file=sys.stderr)
    try:
        controller._run_job(Job(audio=path, deliver=False, source="file",
                                label=path.name, prepare=True))
    except (EngineError, media.MediaError) as exc:
        results["error"] = str(exc)
    if sys.stderr.isatty():
        print("", file=sys.stderr)

    if "error" in results or "dictation" not in results:
        print(f"error: {results.get('error', 'no speech was recognised')}", file=sys.stderr)
        return 1
    dictation = results["dictation"]
    try:
        rendered = export.render(target_format, dictation.text, dictation.segments)
    except ValueError as exc:
        print(f"error: {exc}. Try --format txt.", file=sys.stderr)
        return 1

    if output is None:
        sys.stdout.write(rendered)
    else:
        output.write_text(rendered, encoding="utf-8")
        print(f"Wrote {output}", file=sys.stderr)
    print(f"[{dictation.engine} · {dictation.seconds:.1f}s · "
          f"{len(dictation.segments)} segments]", file=sys.stderr)
    return 0


def _cmd_models(action: str, name: str) -> int:
    """List, download or delete local models: the Models window, in a terminal."""
    from . import models

    if action == "list":
        for engine in (models.PARAKEET, models.FASTER_WHISPER, models.WHISPER_CPP):
            available, reason = models.engine_available(engine)
            print(f"{models.ENGINE_TITLES[engine]}" + ("" if available else f"  ({reason})"))
            for model in models.for_engine(engine):
                state = models.status(model)
                print(f"  {'*' if state.installed else ' '} {model.model_id:<38} "
                      f"{model.size_label:>8}  {model.languages:<22} {state.label}")
        print("\n  * downloaded.  `aloud models download <name>` to fetch one.")
        return 0

    matches = [m for m in models.CATALOG if name in (m.model_id, m.key, m.repo)]
    if not matches:
        print(f"No model called {name!r}. `aloud models list` shows them.", file=sys.stderr)
        return 2
    model = matches[0]

    if action == "delete":
        removed = models.delete(model)
        print(f"Deleted {model.title}." if removed else f"{model.title} was not downloaded.")
        return 0

    last = {"shown": -1}

    def progress(done: int, total: int) -> bool:
        percent = int(100 * done / total) if total else 0
        if percent != last["shown"]:
            last["shown"] = percent
            print(f"\r  {percent:3d}%  {done / 1e6:,.0f} of {total / 1e6:,.0f} MB",
                  end="", flush=True)
        return True

    print(f"Downloading {model.title} ({model.size_label})…")
    try:
        target = models.download(model, progress)
    except (models.DownloadError, KeyboardInterrupt) as exc:
        print(f"\nNot downloaded: {exc or 'cancelled'}", file=sys.stderr)
        return 1
    print(f"\nSaved to {target}")
    print(f"Use it: set engines.{model.engine}.model to that path, or press Use in "
          f"Aloud → Models.")
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
    transcribe = sub.add_parser(
        "transcribe", help="transcribe an audio or video file and print or save it"
    )
    transcribe.add_argument("file", type=Path)
    transcribe.add_argument("--engine", default="",
                            help="engine to use instead of the configured file engine")
    transcribe.add_argument("--format", default="txt",
                            help="txt, timestamped, srt or vtt (default txt)")
    transcribe.add_argument("-o", "--output", type=Path, default=None,
                            help="write to this file instead of printing")
    model_cmd = sub.add_parser("models", help="list, download or delete local models")
    model_cmd.add_argument("action", choices=["list", "download", "delete"], nargs="?",
                           default="list")
    model_cmd.add_argument("name", nargs="?", default="",
                           help="e.g. large-v3-turbo, or ggml-base.en.bin")
    hist = sub.add_parser("history", help="show recent dictations")
    hist.add_argument("-n", "--limit", type=int, default=10)
    key = sub.add_parser("key", help="store an API key for a cloud engine")
    key.add_argument("engine", help="deepgram, openai, groq, assemblyai or elevenlabs")
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
        return _cmd_transcribe(config, args.file, args.engine, args.format, args.output)
    if args.command == "models":
        if args.action != "list" and not args.name:
            parser.error(f"models {args.action} needs a model name")
        return _cmd_models(args.action, args.name)
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
