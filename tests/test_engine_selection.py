"""Per-machine engine selection: the `auto` setting and its fallback chain."""

import pytest

from aloud import engines
from aloud.config import Config
from aloud.engines.base import EngineError, TranscriptionEngine


@pytest.fixture
def mac(monkeypatch):
    """Pretend to be a Mac of a given architecture."""

    def _as(machine: str):
        monkeypatch.setattr(engines.sys, "platform", "darwin")
        monkeypatch.setattr(engines.platform, "machine", lambda: machine)

    return _as


def test_apple_silicon_prefers_parakeet(mac):
    mac("arm64")
    assert engines.resolve(engines.AUTO) == "parakeet_mlx"
    assert engines.preferences()[0] == "parakeet_mlx"


def test_intel_prefers_faster_whisper(mac):
    mac("x86_64")
    assert engines.resolve(engines.AUTO) == "faster_whisper"
    # MLX has no x86_64 build, so Parakeet must never be offered there.
    assert "parakeet_mlx" not in engines.preferences()


def test_whisper_cpp_is_the_last_resort_on_both(mac):
    for machine in ("arm64", "x86_64"):
        mac(machine)
        assert engines.preferences()[-1] == "whisper_cpp"


def test_cloud_and_mock_are_never_auto_selected(mac):
    for machine in ("arm64", "x86_64"):
        mac(machine)
        assert "openai" not in engines.preferences()
        assert "mock" not in engines.preferences()


def test_an_explicit_name_resolves_to_itself():
    assert engines.resolve("whisper_cpp") == "whisper_cpp"
    assert engines.resolve("mock") == "mock"


def test_build_rejects_unknown_engines():
    with pytest.raises(EngineError):
        engines.build("nope")


class _Ready(TranscriptionEngine):
    """An engine that reports itself available."""

    name = "ready"
    label = "Ready"

    def transcribe(self, wav_path):  # pragma: no cover - never called here
        raise NotImplementedError

    def check(self):
        return True, "ready"


def test_select_skips_engines_that_are_not_installed(mac, monkeypatch):
    mac("arm64")
    # Parakeet is unavailable off a Mac, so selection should walk past it.
    monkeypatch.setitem(engines.REGISTRY, "whisper_cpp", _Ready)
    monkeypatch.setattr(engines, "preferences", lambda: ["parakeet_mlx", "whisper_cpp"])

    config = Config()
    config.set("engine", engines.AUTO)
    assert isinstance(engines.select(config), _Ready)


def test_select_returns_the_preferred_engine_when_nothing_is_ready(mac):
    """The UI needs an engine object to explain what to install."""
    mac("arm64")
    config = Config()
    config.set("engine", engines.AUTO)
    selected = engines.select(config)
    assert selected.name == "parakeet_mlx"
    assert selected.check()[0] is False


def test_an_explicitly_named_engine_is_honoured_even_when_broken():
    config = Config()
    config.set("engine", "openai")
    config.set("engines.openai.api_key_env", "ALOUD_DEFINITELY_UNSET")
    selected = engines.select(config)
    assert selected.name == "openai"
    assert selected.check()[0] is False


def test_selected_engine_receives_its_own_options():
    config = Config()
    config.set("engine", "faster_whisper")
    config.set("engines.faster_whisper.model", "small.en")
    assert engines.select(config).options["model"] == "small.en"
