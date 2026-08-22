import json

from aloud import config as config_module
from aloud.config import DEFAULTS, Config, _deep_merge


def test_deep_merge_preserves_untouched_branches():
    merged = _deep_merge(DEFAULTS, {"audio": {"sample_rate": 48000}})
    assert merged["audio"]["sample_rate"] == 48000
    assert merged["audio"]["channels"] == DEFAULTS["audio"]["channels"]
    assert merged["hotkey"] == DEFAULTS["hotkey"]


def test_deep_merge_does_not_mutate_the_defaults():
    _deep_merge(DEFAULTS, {"engine": "mock"})
    assert DEFAULTS["engine"] == "auto"


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
    monkeypatch.setattr("aloud.config.CONFIG_FILE", bad)
    assert Config.load().get("engine") == DEFAULTS["engine"]


def test_save_then_load_round_trips(tmp_path, monkeypatch):
    target = tmp_path / "config.json"
    monkeypatch.setattr("aloud.config.CONFIG_FILE", target)
    monkeypatch.setattr("aloud.config.ensure_dirs", lambda: None)
    config = Config()
    config.set("hotkey.key", "fn")
    config.save()
    assert json.loads(target.read_text())["hotkey"]["key"] == "fn"
    assert Config.load().get("hotkey.key") == "fn"


# -- migration ---------------------------------------------------------------


def test_a_default_that_was_wrong_reaches_an_existing_install(tmp_path, monkeypatch):
    """The bug this exists for: changing a default never reached anyone.

    load() merges the stored file over the defaults, so a value already written
    to disk wins forever — including one the user never chose.
    """
    import json as json_module

    target = tmp_path / "config.json"
    target.write_text(json_module.dumps(
        {"feedback": {"start_sound": "Tink", "stop_sound": "Pop"}}
    ))
    monkeypatch.setattr("aloud.config.CONFIG_FILE", target)
    monkeypatch.setattr("aloud.config.ensure_dirs", lambda: None)

    config = Config.load()
    assert config.get("feedback.start_sound") == "Bottle"
    assert config.get("feedback.stop_sound") == "Glass"


def test_a_value_the_user_chose_is_never_overwritten(tmp_path, monkeypatch):
    import json as json_module

    target = tmp_path / "config.json"
    target.write_text(json_module.dumps({"feedback": {"start_sound": "Frog"}}))
    monkeypatch.setattr("aloud.config.CONFIG_FILE", target)
    monkeypatch.setattr("aloud.config.ensure_dirs", lambda: None)

    assert Config.load().get("feedback.start_sound") == "Frog"


def test_migrating_stamps_the_version_so_it_runs_once(tmp_path, monkeypatch):
    import json as json_module

    target = tmp_path / "config.json"
    target.write_text(json_module.dumps({"feedback": {"start_sound": "Tink"}}))
    monkeypatch.setattr("aloud.config.CONFIG_FILE", target)
    monkeypatch.setattr("aloud.config.ensure_dirs", lambda: None)

    Config.load()
    stored = json_module.loads(target.read_text())
    assert stored["version"] == config_module.SCHEMA_VERSION
    # A second load must leave the (now migrated) value alone.
    stored["feedback"]["start_sound"] = "Tink"
    target.write_text(json_module.dumps(stored))
    assert Config.load().get("feedback.start_sound") == "Tink"


def test_an_already_current_config_is_not_rewritten():
    config = Config()
    assert config.migrate(stored_version=config_module.SCHEMA_VERSION) is False
