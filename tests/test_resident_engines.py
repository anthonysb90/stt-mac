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
    monkeypatch.setattr(pk_module, "supported", lambda: True)
    monkeypatch.setattr(pk_module.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(pk_module.shutil, "which", lambda _name: None)
    ok, detail = pk_module.ParakeetMLXEngine().check()
    assert not ok
    assert "ffmpeg" in detail


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
