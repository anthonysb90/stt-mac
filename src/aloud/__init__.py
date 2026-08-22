"""Aloud — a push-to-talk dictation app for macOS.

Hold a key, speak, release. Whatever you said is transcribed and typed into
whichever app currently has focus.

The package is deliberately layered so that each concern can be swapped
independently (see docs/ARCHITECTURE.md):

    hotkey    -> when to record          (Quartz event tap)
    audio     -> how to capture          (PortAudio via sounddevice)
    engines   -> how to transcribe       (whisper.cpp, cloud API, mock)
    postprocess -> how to clean the text (fillers, dictionary, commands)
    injector  -> how to deliver the text (pasteboard + Cmd-V, or synth typing)
    app       -> the menu bar shell that wires those together
"""

APP_NAME = "Aloud"
BUNDLE_ID = "com.aloud.Aloud"
__version__ = "0.1.0"

def build_id() -> str:
    """A short hash of the installed source.

    Two people looking at the same bug need to know they are looking at the
    same code. A version number cannot tell you that between releases, and a
    git commit cannot tell you anything at all when the copy came from a
    downloaded zip.
    """
    import hashlib
    from pathlib import Path

    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    return digest.hexdigest()[:12]


__all__ = ["APP_NAME", "BUNDLE_ID", "__version__", "build_id"]
