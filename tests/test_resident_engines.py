"""The two in-process engines: readiness reporting and lazy loading.

Neither model can be loaded here (no MLX, no CTranslate2, no Mac), so these
cover the parts that must behave correctly *before* a model exists — which is
exactly the state a half-bootstrapped machine is in.
"""

import pytest

from aloud.engines import faster_whisper as fw_module
from aloud.engines import parakeet_mlx as pk_module
from aloud.engines.base import EngineError


# -- Parakeet ---------------------------------------------------------------


def test_parakeet_reports_the_architecture_requirement(monkeypatch):
    monkeypatch.setattr(pk_module, "supported", lambda: False)
    ok, detail = pk_module.ParakeetMLXEngine().check()
    assert not ok
    assert "Apple Silicon" in detail


def test_parakeet_reports_a_missing_package(monkeypatch):
    monkeypatch.setattr(pk_module, "supported", lambda: True)
    monkeypatch.setattr(pk_module.importlib.util, "find_spec", lambda _name: None)
    ok, detail = pk_module.ParakeetMLXEngine().check()
    assert not ok
    assert "parakeet-mlx is not installed" in detail


def test_parakeet_reports_missing_ffmpeg(monkeypatch):
    from aloud import media

    monkeypatch.setattr(pk_module, "supported", lambda: True)
    monkeypatch.setattr(pk_module.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(media, "ffmpeg_path", lambda: None)
    ok, detail = pk_module.ParakeetMLXEngine().check()
    assert not ok
    assert "ffmpeg" in detail
    # Telling someone to install what they already installed wastes their time.
    assert "PATH" in detail


def test_parakeet_finds_ffmpeg_outside_the_shell_path(monkeypatch):
    """A Dock launch has no Homebrew prefix on PATH; `which` alone said no.

    That is not hypothetical: it shipped, and produced "ffmpeg not found" in
    the app on a machine whose terminal found ffmpeg instantly.
    """
    from aloud import media

    monkeypatch.setattr(pk_module, "supported", lambda: True)
    monkeypatch.setattr(pk_module.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(media.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        media.Path, "is_file", lambda self: str(self) == "/opt/homebrew/bin/ffmpeg"
    )
    ok, _detail = pk_module.ParakeetMLXEngine().check()
    assert ok, "the Homebrew prefix must be searched directly"


def test_parakeet_uses_the_configured_model_id():
    engine = pk_module.ParakeetMLXEngine({"model": "mlx-community/parakeet-tdt-0.6b-v2"})
    assert engine._model_id().endswith("v2")
    assert pk_module.ParakeetMLXEngine()._model_id() == pk_module.DEFAULT_MODEL


def test_parakeet_transcribe_refuses_before_it_can_load(tmp_path, monkeypatch):
    monkeypatch.setattr(pk_module, "supported", lambda: False)
    with pytest.raises(EngineError):
        pk_module.ParakeetMLXEngine().transcribe(tmp_path / "x.wav")


def test_parakeet_warm_up_never_raises(monkeypatch):
    """Warm-up runs on a background thread at startup; it must not crash the app."""
    monkeypatch.setattr(pk_module, "supported", lambda: False)
    pk_module.ParakeetMLXEngine().warm_up()


# -- faster-whisper ---------------------------------------------------------


def test_faster_whisper_reports_a_missing_package(monkeypatch):
    monkeypatch.setattr(fw_module.importlib.util, "find_spec", lambda _name: None)
    ok, detail = fw_module.FasterWhisperEngine().check()
    assert not ok
    assert "faster-whisper is not installed" in detail


def test_faster_whisper_reports_model_and_precision(monkeypatch):
    monkeypatch.setattr(fw_module.importlib.util, "find_spec", lambda _name: object())
    ok, detail = fw_module.FasterWhisperEngine({"model": "small.en"}).check()
    assert ok
    assert "small.en" in detail and "int8" in detail


def test_faster_whisper_warm_up_never_raises(monkeypatch):
    monkeypatch.setattr(fw_module.importlib.util, "find_spec", lambda _name: None)
    fw_module.FasterWhisperEngine().warm_up()


# -- the resident-model contract both share ---------------------------------


class _FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, _path):
        self.calls += 1
        return type("Result", (), {"text": "  hello world  "})()


def test_parakeet_loads_the_model_once_and_reuses_it(tmp_path, monkeypatch):
    """The whole point of an in-process engine: no reload per dictation."""
    engine = pk_module.ParakeetMLXEngine()
    loads = []

    def fake_load():
        loads.append(1)
        return engine._model

    engine._model = _FakeModel()
    monkeypatch.setattr(engine, "_load", fake_load)

    for _ in range(3):
        transcript = engine.transcribe(tmp_path / "x.wav")

    assert transcript.text == "hello world"  # stripped
    assert transcript.engine == "parakeet_mlx"
    assert engine._model.calls == 3
    assert len(loads) == 3  # _load called each time, but it is a cheap no-op


# -- Parakeet: streaming a file, and surviving a parakeet-mlx that cannot ----


class _StreamingParakeetStream:
    """Stands in for parakeet-mlx's streaming context.

    Each chunk of audio adds a word, which is what a growing transcript looks
    like from the outside.
    """

    def __init__(self, fail_on_add=False):
        self.chunks = []
        self.fail_on_add = fail_on_add
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True
        return False

    def add_audio(self, chunk):
        if self.fail_on_add:
            raise TypeError("add_audio() takes different arguments")
        self.chunks.append(chunk)

    @property
    def result(self):
        return type("R", (), {"text": " ".join(f"word{n}" for n in range(len(self.chunks)))})


class _StreamingParakeet:
    def __init__(self, stream=None, whole="one pass text"):
        self.stream = stream
        self.whole = whole
        self.whole_calls = 0

    def transcribe_stream(self):
        if self.stream is None:
            raise AttributeError("no transcribe_stream in this build")
        return self.stream

    def transcribe(self, _path):
        self.whole_calls += 1
        return type("R", (), {"text": self.whole})


def _write_wav(path, seconds=3.0, rate=16000, channels=1, width=2):
    import struct
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 1000) * int(rate * seconds) * channels)


@pytest.fixture
def fake_mlx(monkeypatch):
    """A stand-in for mlx.core, whose array() we only need to be a no-op."""
    import sys
    import types

    core = types.ModuleType("mlx.core")
    core.array = lambda values: values
    package = types.ModuleType("mlx")
    package.core = core
    monkeypatch.setitem(sys.modules, "mlx", package)
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    return core


def _ready_engine(monkeypatch, model, **options):
    engine = pk_module.ParakeetMLXEngine(options)
    engine._model = model
    monkeypatch.setattr(pk_module, "supported", lambda: True)
    return engine


def test_parakeet_streams_a_file_and_reports_progress(tmp_path, monkeypatch, fake_mlx):
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=30.0)
    stream = _StreamingParakeetStream()
    engine = _ready_engine(monkeypatch, _StreamingParakeet(stream), stream_chunk_seconds=10.0)

    seen = []
    result = engine.transcribe(wav, on_progress=lambda text, done, total: seen.append(
        (text, round(done, 3), round(total, 3))
    ) or True)

    assert len(stream.chunks) == 3, "30s at 10s a chunk"
    assert [done for _text, done, _total in seen] == [10.0, 20.0, 30.0]
    assert all(total == 30.0 for *_rest, total in seen)
    assert result.text == "word0 word1 word2"
    assert result.meta["mode"] == "streamed"
    assert stream.closed, "the streaming context must be exited"


def test_parakeet_progress_never_overshoots_the_length(tmp_path, monkeypatch, fake_mlx):
    """A final short chunk must not report more audio than the file holds."""
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=25.0)
    engine = _ready_engine(
        monkeypatch, _StreamingParakeet(_StreamingParakeetStream()), stream_chunk_seconds=10.0
    )

    seen = []
    engine.transcribe(wav, on_progress=lambda text, done, total: seen.append(done) or True)
    assert seen[-1] == 25.0
    assert max(seen) <= 25.0


def test_parakeet_stops_when_progress_asks_it_to(tmp_path, monkeypatch, fake_mlx):
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=60.0)
    stream = _StreamingParakeetStream()
    engine = _ready_engine(monkeypatch, _StreamingParakeet(stream), stream_chunk_seconds=10.0)

    engine.transcribe(wav, on_progress=lambda *_a: False)
    assert len(stream.chunks) == 1, "cancelled after the first chunk"


def test_parakeet_dictation_takes_the_single_pass_path(tmp_path, monkeypatch, fake_mlx):
    """No progress callback means nobody is watching, so accuracy wins."""
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=3.0)
    model = _StreamingParakeet(_StreamingParakeetStream())
    engine = _ready_engine(monkeypatch, model)

    result = engine.transcribe(wav)
    assert result.text == "one pass text"
    assert result.meta["mode"] == "whole"
    assert model.whole_calls == 1


def test_parakeet_falls_back_when_the_build_cannot_stream(tmp_path, monkeypatch, fake_mlx):
    """An older parakeet-mlx must cost the live words, not the transcript."""
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=20.0)
    model = _StreamingParakeet(stream=None)
    engine = _ready_engine(monkeypatch, model)

    seen = []
    result = engine.transcribe(wav, on_progress=lambda *a: seen.append(a) or True)
    assert result.text == "one pass text"
    assert result.meta["mode"] == "whole"
    assert model.whole_calls == 1
    assert seen == [("", 0.0, 0.0)], "an indeterminate bar, not a stalled one"


def test_parakeet_falls_back_when_add_audio_has_another_shape(tmp_path, monkeypatch, fake_mlx):
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=20.0)
    model = _StreamingParakeet(_StreamingParakeetStream(fail_on_add=True))
    engine = _ready_engine(monkeypatch, model)

    result = engine.transcribe(wav, on_progress=lambda *_a: True)
    assert result.text == "one pass text"
    assert model.whole_calls == 1


def test_parakeet_falls_back_when_mlx_is_missing(tmp_path, monkeypatch):
    """No fake_mlx fixture here: the import itself has to fail."""
    import sys

    monkeypatch.setitem(sys.modules, "mlx.core", None)
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=20.0)
    model = _StreamingParakeet(_StreamingParakeetStream())
    engine = _ready_engine(monkeypatch, model)

    result = engine.transcribe(wav, on_progress=lambda *_a: True)
    assert result.text == "one pass text"
    assert model.whole_calls == 1


def test_parakeet_falls_back_on_audio_it_cannot_read(tmp_path, monkeypatch, fake_mlx):
    """Stereo or 8-bit means the file never went through media.prepare."""
    wav = tmp_path / "stereo.wav"
    _write_wav(wav, seconds=5.0, channels=2)
    model = _StreamingParakeet(_StreamingParakeetStream())
    engine = _ready_engine(monkeypatch, model)

    result = engine.transcribe(wav, on_progress=lambda *_a: True)
    assert result.text == "one pass text"
    assert model.whole_calls == 1


def test_parakeet_declares_progress_support():
    """The transcribe view decides whether to show words from this flag."""
    assert pk_module.ParakeetMLXEngine.supports_progress is True


def test_parakeet_stream_chunk_seconds_rejects_nonsense():
    for bad in ("", None, 0, -5, "abc"):
        engine = pk_module.ParakeetMLXEngine({"stream_chunk_seconds": bad})
        assert engine._chunk_seconds() == pk_module.DEFAULT_STREAM_CHUNK_SECONDS
    assert pk_module.ParakeetMLXEngine({"stream_chunk_seconds": 4})._chunk_seconds() == 4.0
