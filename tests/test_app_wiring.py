"""End-to-end wiring: hotkey edge -> recording -> engine -> delivery.

Runs against the framework stubs in ``tests/stubs.py``, so it exercises the
real state machine, the real queue, and the real post-processing without a
microphone, a model, or a menu bar.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from aloud.app import AloudApp, State
from aloud.config import Config


def _silent_wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


class FakeRecorder:
    """Stands in for the PortAudio recorder."""

    def __init__(self, wav_path: Path) -> None:
        self.wav_path = wav_path
        self.recording = False
        self.starts = 0

    def start(self) -> None:
        self.recording = True
        self.starts += 1

    def stop(self):
        self.recording = False
        return self.wav_path

    def cancel(self) -> None:
        self.recording = False


class CapturingInjector:
    def __init__(self) -> None:
        self.delivered: list[str] = []

    def deliver(self, text: str) -> None:
        self.delivered.append(text)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("aloud.history.HISTORY_FILE", tmp_path / "history.jsonl")
    monkeypatch.setattr("aloud.history.ensure_dirs", lambda: None)

    config = Config()
    config.set("engine", "mock")
    config.set("engines.mock.text", "um, hello there new line friend")
    config.set("feedback.sounds", False)
    config.set("feedback.notify_on_error", False)

    monkeypatch.setattr("aloud.app.Recorder", lambda **_kwargs: FakeRecorder(tmp_path / "x.wav"))
    instance = AloudApp(config)
    instance.recorder = FakeRecorder(_silent_wav(tmp_path / "speech.wav"))
    instance.injector = CapturingInjector()
    return instance


def test_menu_is_built_with_the_expected_entries(app):
    titles = [item.title for item in app.menu if hasattr(item, "title")]
    assert "Idle" in titles
    assert any(title.startswith("Engine:") for title in titles)
    assert any(title.startswith("Hotkey:") for title in titles)


def test_press_starts_recording_and_release_queues_a_job(app):
    app.on_hotkey_press()
    assert app.state is State.RECORDING
    assert app.recorder.recording

    app.on_hotkey_release()
    assert app.state is State.TRANSCRIBING
    assert app._jobs.qsize() == 1


def test_second_press_while_recording_is_ignored(app):
    app.on_hotkey_press()
    app.on_hotkey_press()
    assert app.recorder.starts == 1


def test_release_without_a_press_does_nothing(app):
    app.on_hotkey_release()
    assert app.state is State.IDLE
    assert app._jobs.qsize() == 0


def test_transcript_is_post_processed_before_delivery(app, tmp_path):
    wav = _silent_wav(tmp_path / "job.wav")
    app._transcribe_and_deliver(wav)
    # Filler stripped, spoken command expanded, first letter capitalised.
    assert app.injector.delivered == ["Hello there\nfriend"]
    assert app.state is State.IDLE


def test_short_recordings_are_discarded(app, tmp_path):
    app.recorder = FakeRecorder(_silent_wav(tmp_path / "blip.wav", seconds=0.1))
    app.on_hotkey_press()
    app.on_hotkey_release()
    assert app.state is State.IDLE
    assert app._jobs.qsize() == 0


def test_empty_transcript_is_not_delivered(app, tmp_path):
    app.config.set("engines.mock.text", "")
    app.engine.options["text"] = ""
    app._transcribe_and_deliver(_silent_wav(tmp_path / "quiet.wav"))
    assert app.injector.delivered == []


def test_dictation_is_written_to_history(app, tmp_path):
    from aloud import history

    app._transcribe_and_deliver(_silent_wav(tmp_path / "job.wav"))
    entries = history.recent(5)
    assert entries and entries[0]["text"] == "Hello there\nfriend"
