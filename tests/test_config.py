import json

from murmur.config import DEFAULTS, Config, _deep_merge


def test_deep_merge_preserves_untouched_branches():
    merged = _deep_merge(DEFAULTS, {"audio": {"sample_rate": 48000}})
    assert merged["audio"]["sample_rate"] == 48000
    assert merged["audio"]["channels"] == DEFAULTS["audio"]["channels"]
    assert merged["hotkey"] == DEFAULTS["hotkey"]


def test_deep_merge_does_not_mutate_the_defaults():
    _deep_merge(DEFAULTS, {"engine": "mock"})
    assert DEFAULTS["engine"] == "whisper_cpp"


def test_dotted_get_and_set():
    config = Config()
    assert config.get("audio.sample_rate") == 16000
    config.set("audio.sample_rate", 22050)
    assert config.get("audio.sample_rate") == 22050


def test_get_returns_default_for_missing_path():
    assert Config().get("nope.not.here", "fallback") == "fallback"


def test_engine_options_follow_the_selected_engine():
    config = Config()
    config.set("engine", "openai")
    assert config.engine_options()["model"] == "whisper-1"
    assert config.engine_options("mock")["text"]


def test_load_falls_back_to_defaults_on_bad_json(tmp_path, monkeypatch):
    bad = tmp_path / "config.json"
    bad.write_text("{ not json")
    monkeypatch.setattr("murmur.config.CONFIG_FILE", bad)
    assert Config.load().get("engine") == DEFAULTS["engine"]


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    monkeypatch.setattr("murmur.config.CONFIG_FILE", target)
    monkeypatch.setattr("murmur.config.ensure_dirs", lambda: None)
    config = Config()
    config.set("hotkey.key", "fn")
    config.save()
    assert json.loads(target.read_text())["hotkey"]["key"] == "fn"
    assert Config.load().get("hotkey.key") == "fn"
