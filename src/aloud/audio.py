"""Microphone capture.

Records 16 kHz mono PCM -- what every Whisper variant wants -- straight into
memory, then writes a WAV file for the engine to read. Recordings are short
(a held key), so buffering in RAM keeps the stop-to-text path free of disk
latency.

``sounddevice`` is used in *raw* mode so no numpy conversion happens on the
audio callback thread, and so numpy is not a dependency at all.
"""

from __future__ import annotations

import array
import logging
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

SAMPLE_WIDTH_BYTES = 2  # int16
INT16_FULL_SCALE = 32768.0


class AudioError(RuntimeError):
    pass


class Recorder:
    """Start/stop microphone capture and hand back a WAV file."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        device: Optional[object] = None,
        max_seconds: float = 300.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device
        self.max_seconds = max_seconds

        self._stream = None
        self._chunks: List[bytes] = []
        self._lock = threading.Lock()
        self._started_at: Optional[float] = None
        self._overflowed = False
        self._level = 0.0

    # -- lifecycle ---------------------------------------------------------

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self.recording:
            return
        import sounddevice as sd  # imported lazily: loading PortAudio is slow

        with self._lock:
            self._chunks = []
            self._overflowed = False

        try:
            self._stream = sd.RawInputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                device=self.device,
                blocksize=0,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:  # sounddevice raises a grab-bag of errors
            self._stream = None
            raise AudioError(f"Could not open the microphone: {exc}") from exc

        self._started_at = time.monotonic()
        log.debug("Recording started (%d Hz, %d ch)", self.sample_rate, self.channels)

    def stop(self) -> Optional[Path]:
        """Stop capture and write the audio to a temp WAV. None if nothing was captured."""
        if not self.recording:
            return None

        stream, self._stream = self._stream, None
        try:
            stream.stop()
            stream.close()
        except Exception:
            log.exception("Error while closing the audio stream")

        self._level = 0.0
        with self._lock:
            chunks, self._chunks = self._chunks, []

        if not chunks:
            log.debug("Recording produced no audio")
            return None
        if self._overflowed:
            log.warning("Audio input overflowed; some samples were dropped")

        return self._write_wav(b"".join(chunks))

    def cancel(self) -> None:
        """Stop capture and discard whatever was recorded."""
        if not self.recording:
            return
        stream, self._stream = self._stream, None
        self._level = 0.0
        try:
            stream.stop()
            stream.close()
        finally:
            with self._lock:
                self._chunks = []

    # -- internals ---------------------------------------------------------

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            self._overflowed = True
        block = bytes(indata)
        self._level = _peak_of(block)
        with self._lock:
            self._chunks.append(block)

    @property
    def level(self) -> float:
        """Peak amplitude of the most recent block, 0.0 to 1.0.

        Read by the level meter. Peak rather than RMS because the meter's job
        is to show headroom -- an RMS reading barely moves when you clip.
        Deliberately not locked: it is a single float written by the audio
        thread and read by the UI, where a torn read costs one stale frame.
        """
        return self._level if self.recording else 0.0

    def _write_wav(self, pcm: bytes) -> Path:
        handle = tempfile.NamedTemporaryFile(
            prefix="aloud-", suffix=".wav", delete=False
        )
        handle.close()
        path = Path(handle.name)
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(SAMPLE_WIDTH_BYTES)
            wav.setframerate(self.sample_rate)
            wav.writeframes(pcm)
        log.debug("Wrote %s (%.2fs)", path, self.duration_of(pcm))
        return path

    def duration_of(self, pcm: bytes) -> float:
        frame_bytes = SAMPLE_WIDTH_BYTES * self.channels
        return len(pcm) / float(frame_bytes * self.sample_rate)

    @property
    def elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at


def _peak_of(pcm: bytes) -> float:
    """Peak amplitude of one int16 block, normalised to 0.0-1.0.

    Runs on the audio callback thread, so it uses `array` rather than anything
    that would allocate per sample. A block is ~1k samples and this is a C-level
    loop, which is comfortably inside the callback's budget.
    """
    if len(pcm) < SAMPLE_WIDTH_BYTES:
        return 0.0
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % SAMPLE_WIDTH_BYTES)])
    if not samples:
        return 0.0
    return min(max(-min(samples), max(samples)) / INT16_FULL_SCALE, 1.0)


def wav_duration(path: Path) -> float:
    """Length of a WAV file in seconds."""
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate() or 1
        return frames / float(rate)


#: What the config means by "whatever the system is set to".
SYSTEM_DEFAULT = None


def list_input_devices() -> List[dict]:
    """Every input-capable device, newest listing each time it is called.

    Re-queried rather than cached because devices come and go: plugging in a
    headset mid-session is exactly when someone reaches for this list.
    Returns an empty list rather than raising if the audio stack is unhappy —
    a picker with nothing in it is better than a menu that will not open.
    """
    try:
        import sounddevice as sd

        devices = []
        for index, info in enumerate(sd.query_devices()):
            if info.get("max_input_channels", 0) > 0:
                devices.append({
                    "index": index,
                    "name": info.get("name") or f"Device {index}",
                    "channels": info.get("max_input_channels", 0),
                    "sample_rate": int(info.get("default_samplerate", 0) or 0),
                })
        return devices
    except Exception as exc:
        # Called every time a picker opens, so no traceback spam.
        log.warning("Could not list input devices: %s", exc)
        return []


def default_input_device() -> Optional[dict]:
    """The device macOS is currently set to use, if it can be determined."""
    try:
        import sounddevice as sd

        index = sd.default.device[0]
        if index is None or index < 0:
            return None
        info = sd.query_devices(index)
        return {"index": index, "name": info.get("name") or f"Device {index}"}
    except Exception:
        return None


def describe_device(device: object) -> str:
    """A label for whatever is stored in the config."""
    if device is SYSTEM_DEFAULT:
        current = default_input_device()
        return f"System Default ({current['name']})" if current else "System Default"
    for entry in list_input_devices():
        if entry["index"] == device or entry["name"] == device:
            return entry["name"]
    return f"{device} (not connected)"
