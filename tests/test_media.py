"""Importing an arbitrary audio file — format handling, and cleaning up after."""

import subprocess
import wave
from pathlib import Path

import pytest

from aloud import media


def _wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


# -- what the open panel offers ---------------------------------------------


@pytest.mark.parametrize("name", ["a.mp3", "a.M4A", "a.wav", "a.flac", "a.mov", "a.webm"])
def test_common_audio_and_video_files_are_accepted(name):
    assert media.is_supported(Path(name))


@pytest.mark.parametrize("name", ["notes.txt", "photo.png", "archive.zip", "noext"])
def test_other_files_are_not(name):
    assert not media.is_supported(Path(name))


def test_video_containers_are_included():
    """The audio track of a screen recording is a thing people want transcribed."""
    assert media.is_supported(Path("recording.mov"))


# -- content types -----------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [("a.wav", "audio/wav"), ("a.mp3", "audio/mpeg"), ("a.m4a", "audio/mp4"),
     ("a.MP3", "audio/mpeg"), ("a.zzz", "application/octet-stream")],
)
def test_mime_types_drive_what_gets_uploaded(name, expected):
    assert media.mime_type(Path(name)) == expected


# -- preparation -------------------------------------------------------------


def test_a_wav_is_used_as_it_is(tmp_path):
    source = _wav(tmp_path / "already.wav")
    prepared = media.prepare(source)
    assert prepared.path == source
    assert not prepared.converted and not prepared.temporary


def test_a_missing_file_is_rejected_before_anything_else(tmp_path):
    with pytest.raises(media.MediaError, match="No such file"):
        media.prepare(tmp_path / "nope.mp3")


def test_without_ffmpeg_the_file_is_handed_over_untouched(tmp_path, monkeypatch):
    """Letting the engine try and report its own error beats refusing here."""
    monkeypatch.setattr(media, "ffmpeg_path", lambda: None)
    source = tmp_path / "voice.m4a"
    source.write_bytes(b"pretend audio")
    prepared = media.prepare(source)
    assert prepared.path == source
    assert not prepared.converted


def test_conversion_asks_ffmpeg_for_16k_mono_pcm(tmp_path, monkeypatch):
    source = tmp_path / "voice.m4a"
    source.write_bytes(b"pretend audio")
    seen = {}

    def fake_run(command, **_kwargs):
        seen["command"] = command
        Path(command[-1]).write_bytes(b"RIFF....WAVEfake")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", fake_run)

    prepared = media.prepare(source)
    command = seen["command"]
    assert "-ac" in command and command[command.index("-ac") + 1] == "1"
    assert "-ar" in command and command[command.index("-ar") + 1] == "16000"
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "pcm_s16le"
    assert "-vn" in command, "a video track must be ignored, not transcoded"
    assert prepared.converted and prepared.temporary
    assert prepared.original == source


def test_a_converted_file_cleans_itself_up(tmp_path, monkeypatch):
    source = tmp_path / "voice.m4a"
    source.write_bytes(b"pretend audio")

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"RIFF....WAVEfake")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", fake_run)

    prepared = media.prepare(source)
    assert prepared.path.exists()
    prepared.cleanup()
    assert not prepared.path.exists()
    assert source.exists(), "the user's own file must survive"


def test_an_unreadable_file_reports_ffmpeg_s_own_reason(tmp_path, monkeypatch):
    source = tmp_path / "broken.mp3"
    source.write_bytes(b"not audio")

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 1, "", "Invalid data found when processing input")

    monkeypatch.setattr(media, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", fake_run)

    with pytest.raises(media.MediaError, match="Invalid data"):
        media.prepare(source)


def test_an_empty_conversion_counts_as_a_failure(tmp_path, monkeypatch):
    """ffmpeg can exit 0 and still write nothing."""
    source = tmp_path / "silent.m4a"
    source.write_bytes(b"x")

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(media, "ffmpeg_path", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", fake_run)

    with pytest.raises(media.MediaError):
        media.prepare(source)


def test_ffmpeg_is_found_where_a_dock_launch_cannot_see_path(monkeypatch):
    """A GUI app gets a minimal PATH that omits both Homebrew prefixes."""
    monkeypatch.setattr(media.shutil, "which", lambda _n: None)
    monkeypatch.setattr(media.Path, "is_file", lambda self: str(self) == "/opt/homebrew/bin/ffmpeg")
    assert media.ffmpeg_path() == "/opt/homebrew/bin/ffmpeg"


# -- duration ----------------------------------------------------------------


def test_wav_duration_is_read_directly(tmp_path):
    assert media.duration_of(_wav(tmp_path / "two.wav", seconds=2)) == pytest.approx(2.0)


def test_duration_is_zero_rather_than_an_error_when_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "ffmpeg_path", lambda: None)
    source = tmp_path / "voice.m4a"
    source.write_bytes(b"x")
    assert media.duration_of(source) == 0.0
