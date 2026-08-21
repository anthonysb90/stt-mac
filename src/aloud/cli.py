"""Command line entry point.

Without arguments this launches the menu bar app. The subcommands exist so the
pipeline can be exercised piece by piece over SSH or in CI, where there is no
GUI and no permission grants.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, __version__, engines, logging_setup
from .config import Config
from .paths import CONFIG_FILE, LOG_FILE, ensure_dirs


def _cmd_doctor(config: Config) -> int:
    import platform

    from . import permissions
    from .hotkey import describe

    configured = str(config.get("engine", engines.AUTO))
    selected = engines.select(config)

    print(f"{APP_NAME} {__version__}")
    print(f"  machine     {platform.machine()} · macOS {platform.mac_ver()[0] or 'n/a'}")
    print(f"  python      {sys.version.split()[0]}")
    print(f"  config      {CONFIG_FILE}")
    print(f"  log         {LOG_FILE}")
    print(f"  hotkey      {config.get('hotkey.mode')} {describe(config.get('hotkey.key'))}")
    print(f"  permissions {permissions.summary()}")
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


def _cmd_history(limit: int) -> int:
    from . import history

    for entry in history.recent(limit):
        print(f"{entry.get('at')}  {entry.get('text')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aloud", description=f"{APP_NAME} dictation")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="launch the menu bar app (default)")
    sub.add_parser("doctor", help="report engine, device, and permission status")
    sub.add_parser("warm", help="load the selected engine, downloading its model")
    transcribe = sub.add_parser("transcribe", help="transcribe a WAV file and print it")
    transcribe.add_argument("wav", type=Path)
    hist = sub.add_parser("history", help="show recent dictations")
    hist.add_argument("-n", "--limit", type=int, default=10)

    args = parser.parse_args(argv)

    ensure_dirs()
    config = Config.load()
    logging_setup.configure(config.get("logging.level", "INFO"))

    if args.command == "doctor":
        return _cmd_doctor(config)
    if args.command == "warm":
        return _cmd_warm(config)
    if args.command == "transcribe":
        return _cmd_transcribe(config, args.wav)
    if args.command == "history":
        return _cmd_history(args.limit)

    from .app import AloudApp

    AloudApp(config).start()
    return 0
