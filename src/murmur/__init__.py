"""Murmur — a push-to-talk dictation app for macOS.

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

APP_NAME = "Murmur"
BUNDLE_ID = "com.murmur.Murmur"
__version__ = "0.1.0"

__all__ = ["APP_NAME", "BUNDLE_ID", "__version__"]
