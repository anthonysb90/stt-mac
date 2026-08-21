import pytest

from murmur import engines
from murmur.engines.base import EngineError


def test_every_registered_engine_declares_its_own_name():
    for name, engine_class in engines.REGISTRY.items():
        assert engine_class.name == name
        assert engine_class.label


def test_build_returns_the_requested_engine():
    assert engines.build("mock").name == "mock"


def test_build_rejects_unknown_engines():
    with pytest.raises(EngineError):
        engines.build("nope")


def test_mock_engine_is_always_ready(tmp_path):
    engine = engines.build("mock", {"text": "hello"})
    ok, _ = engine.check()
    assert ok
    assert engine.transcribe(tmp_path / "unused.wav").text == "hello"


def test_openai_engine_reports_a_missing_key():
    engine = engines.build("openai", {"api_key_env": "MURMUR_DEFINITELY_UNSET"})
    ok, detail = engine.check()
    assert not ok
    assert "MURMUR_DEFINITELY_UNSET" in detail
