"""Microphone capture.

Records 16 kHz mono PCM -- what every Whisper variant wants -- straight into
memory, then writes a WAV file for the engine to read. Recordings are short
(a held key), so buffering in RAM keeps the stop-to-text path free of disk
latency.

``sounddevice`` is used in *raw* mode so no numpy conversion happens on the
audio callback thread, and so numpy is not a dependency at all.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

SAMPLE_WIDTH_BYTES = 2  # int16


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
        with self._lock:
            self._chunks.append(bytes(indata))

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


def wav_duration(path: Path) -> float:
    """Length of a WAV file in seconds."""
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        rate = wav.getframerate() or 1
        return frames / float(rate)


def list_input_devices() -> list[dict]:
    """Every input-capable device, for the menu bar picker."""
    import sounddevice as sd

    devices = []
    for index, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) > 0:
            devices.append({"index": index, "name": info.get("name", f"Device {index}")})
    return devices
