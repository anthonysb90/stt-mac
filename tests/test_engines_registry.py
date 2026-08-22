import pytest

from aloud import engines
from aloud.engines.base import EngineError


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
    engine = engines.build("openai", {"api_key_env": "ALOUD_DEFINITELY_UNSET"})
    ok, detail = engine.check()
    assert not ok
    assert "ALOUD_DEFINITELY_UNSET" in detail


def test_every_key_taking_engine_names_its_environment_variable():
    """Settings lists key fields straight off the classes.

    It has to: constructing a local engine loads a model, which is not a thing
    opening Settings should do. So an engine that wants a key must say which
    variable it reads without being instantiated first — otherwise its field
    appears in Settings with nowhere to read an existing key from, and a key
    already exported in the shell reads as "not set".
    """
    for name, engine_class in engines.REGISTRY.items():
        if engine_class.needs_api_key:
            assert engine_class.api_key_env_default, (
                f"{name} needs a key but declares no api_key_env_default"
            )


def test_api_key_env_falls_back_to_the_class_default():
    assert engines.build("deepgram").api_key_env == "DEEPGRAM_API_KEY"
    assert engines.build("openai").api_key_env == "OPENAI_API_KEY"


def test_api_key_env_honours_a_config_override():
    engine = engines.build("deepgram", {"api_key_env": "MY_OWN_VAR"})
    assert engine.api_key_env == "MY_OWN_VAR"


def test_engines_that_need_no_key_name_no_variable():
    for name, engine_class in engines.REGISTRY.items():
        if not engine_class.needs_api_key:
            assert engine_class.api_key_env_default == "", name
