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


@dataclass
class FileInfo:
    """What we can tell you about a file before spending minutes on it.

    Worth showing before transcribing rather than after: a two-hour recording
    and a two-minute one look identical in a file picker, and only one of them
    is worth starting on a CPU-only Mac.
    """

    path: Path
    size_bytes: int = 0
    duration: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    container: str = ""
    supported: bool = True
    modified: float = 0.0

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size_label(self) -> str:
        size = float(self.size_bytes)
        for unit in ("bytes", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                precision = 0 if unit in ("bytes", "KB") else 1
                return f"{size:.{precision}f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    @property
    def duration_label(self) -> str:
        if self.duration <= 0:
            return "unknown"
        seconds = int(self.duration)
        hours, rest = divmod(seconds, 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes}:{secs:02d}"

    @property
    def audio_label(self) -> str:
        if not self.sample_rate:
            return ""
        channels = {1: "mono", 2: "stereo"}.get(self.channels, f"{self.channels} channels")
        return f"{self.sample_rate / 1000:g} kHz {channels}"

    def rows(self) -> list:
        """Label/value pairs, in the order worth reading them."""
        pairs = [
            ("Format", self.container.upper() or "unknown"),
            ("Length", self.duration_label),
            ("Size", self.size_label),
        ]
        if self.audio_label:
            pairs.append(("Audio", self.audio_label))
        if not self.supported:
            pairs.append(("Note", "Not an audio or video file Aloud recognises"))
        elif self.duration <= 0:
            pairs.append(("Note", "Length unknown — install ffmpeg to read it"))
        return pairs


def inspect(path: Path) -> FileInfo:
    """Read what we can about a file, without decoding all of it."""
    path = Path(path).expanduser()
    info = FileInfo(
        path=path,
        container=path.suffix.lower().lstrip("."),
        supported=is_supported(path),
    )
    try:
        stat = path.stat()
        info.size_bytes = stat.st_size
        info.modified = stat.st_mtime
    except OSError:
        return info

    if path.suffix.lower() == ".wav":
        try:
            import wave

            with wave.open(str(path), "rb") as handle:
                info.sample_rate = handle.getframerate()
                info.channels = handle.getnchannels()
                if info.sample_rate:
                    info.duration = handle.getnframes() / float(info.sample_rate)
        except Exception:
            log.debug("Could not read WAV header for %s", path, exc_info=True)
        return info

    probed = _probe(path)
    info.duration = probed.get("duration", 0.0)
    info.sample_rate = int(probed.get("sample_rate", 0) or 0)
    info.channels = int(probed.get("channels", 0) or 0)
    return info


def _probe(path: Path) -> dict:
    """Ask ffprobe for the stream details. Returns {} when it is unavailable."""
    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        return {}
    probe = Path(ffmpeg).with_name("ffprobe")
    if not probe.is_file():
        return {}
    try:
        completed = subprocess.run(
            [
                str(probe), "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=sample_rate,channels:format=duration",
                "-of", "default=noprint_wrappers=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}

    found = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        try:
            found[key.strip()] = float(value) if key.strip() == "duration" else value.strip()
        except ValueError:
            continue
    return found


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
