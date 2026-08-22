"""Getting an arbitrary audio file into a shape the engines can read.

Dictation always produces a 16 kHz mono WAV, because that is what the recorder
writes. A file you drag in is whatever it happens to be — an m4a voice memo, an
mp3, the audio track of a screen recording — so it has to be normalised first.

The engines disagree about what they can open. faster-whisper decodes through
PyAV and takes most things; Parakeet shells out to ffmpeg; whisper.cpp accepts
16 kHz WAV and nothing else; Deepgram takes whatever you upload as long as the
content type is right. Converting up front means none of that matters, and the
transcription path stays identical to the dictation one.

When ffmpeg is not installed the original file is passed through unchanged.
That is not a guess that it will work — it is letting the engine try and report
its own error, which is more useful than refusing on this layer's behalf.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1

#: What the open panel offers. Video containers are included because the audio
#: track of a screen recording is a thing people want transcribed.
AUDIO_EXTENSIONS = (
    "wav", "mp3", "m4a", "aac", "flac", "ogg", "oga", "opus",
    "aiff", "aif", "aifc", "caf", "wma", "amr",
    "mp4", "m4v", "mov", "webm", "mkv",
)

#: Content types for the engines that upload the bytes rather than decode them.
MIME_TYPES = {
    "wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4",
    "aac": "audio/aac", "flac": "audio/flac", "ogg": "audio/ogg",
    "oga": "audio/ogg", "opus": "audio/opus", "aiff": "audio/aiff",
    "aif": "audio/aiff", "caf": "audio/x-caf", "mp4": "video/mp4",
    "mov": "video/quicktime", "webm": "video/webm", "mkv": "video/x-matroska",
}


class MediaError(RuntimeError):
    pass


@dataclass
class Prepared:
    """A file ready for an engine, and whether we are responsible for deleting it."""

    path: Path
    temporary: bool
    converted: bool
    original: Path

    def cleanup(self) -> None:
        if self.temporary:
            self.path.unlink(missing_ok=True)


def ffmpeg_path() -> Optional[str]:
    found = shutil.which("ffmpeg")
    if found:
        return found
    # A GUI app launched from the Dock gets a minimal PATH that omits both
    # Homebrew prefixes, so look there directly before giving up.
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(candidate).is_file():
            return candidate
    return None


def mime_type(path: Path) -> str:
    return MIME_TYPES.get(path.suffix.lower().lstrip("."), "application/octet-stream")


def is_supported(path: Path) -> bool:
    return path.suffix.lower().lstrip(".") in AUDIO_EXTENSIONS


def prepare(path: Path) -> Prepared:
    """Normalise ``path`` to 16 kHz mono WAV, if we can and if it is needed."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise MediaError(f"No such file: {path}")

    if path.suffix.lower() == ".wav":
        return Prepared(path=path, temporary=False, converted=False, original=path)

    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        log.info("ffmpeg not found; handing %s to the engine as-is", path.name)
        return Prepared(path=path, temporary=False, converted=False, original=path)

    handle = tempfile.NamedTemporaryFile(prefix="aloud-import-", suffix=".wav", delete=False)
    handle.close()
    target = Path(handle.name)

    command = [
        ffmpeg,
        "-nostdin", "-y",
        "-i", str(path),
        "-vn",                       # ignore any video track
        "-ac", str(TARGET_CHANNELS),
        "-ar", str(TARGET_SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(target),
    ]
    log.debug("Converting: %s", " ".join(command))
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise MediaError(f"Could not run ffmpeg: {exc}") from exc

    if completed.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        tail = (completed.stderr or "").strip().splitlines()
        reason = tail[-1] if tail else f"exit code {completed.returncode}"
        raise MediaError(f"ffmpeg could not read {path.name}: {reason}")

    return Prepared(path=target, temporary=True, converted=True, original=path)


def duration_of(path: Path) -> float:
    """Length in seconds, best effort. Returns 0.0 when it cannot be determined."""
    if path.suffix.lower() == ".wav":
        try:
            import wave

            with wave.open(str(path), "rb") as handle:
                rate = handle.getframerate() or 1
                return handle.getnframes() / float(rate)
        except Exception:
            return 0.0

    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        return 0.0
    probe = Path(ffmpeg).with_name("ffprobe")
    if not probe.is_file():
        return 0.0
    try:
        completed = subprocess.run(
            [str(probe), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15, check=False,
        )
        return float(completed.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0
