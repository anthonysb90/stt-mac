"""Key lookup — the part that decides whether a cloud engine works from the Dock."""

import pytest

from aloud import secrets


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(secrets, "ensure_dirs", lambda: None)
    # No keychain in the test environment.
    monkeypatch.setattr(secrets, "_read_keychain", lambda _name: "")
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)


def test_the_environment_is_consulted_first(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "from-env")
    secrets.store_key("deepgram", "from-file")
    assert secrets.read_key("DEEPGRAM_API_KEY", "deepgram") == "from-env"


def test_the_file_is_the_fallback_a_dock_launch_needs():
    secrets.store_key("deepgram", "from-file")
    assert secrets.read_key("DEEPGRAM_API_KEY", "deepgram") == "from-file"


def test_a_missing_key_is_an_empty_string_not_an_error():
    assert secrets.read_key("DEEPGRAM_API_KEY", "deepgram") == ""


def test_stored_keys_are_readable_only_by_the_owner():
    path = secrets.store_key("deepgram", "secret")
    assert path.stat().st_mode & 0o777 == 0o600
    assert secrets.KEYS_DIR.stat().st_mode & 0o777 == 0o700


def test_surrounding_whitespace_is_stripped():
    secrets.store_key("deepgram", "  spaced-key\n")
    assert secrets.read_key("DEEPGRAM_API_KEY", "deepgram") == "spaced-key"


def test_forgetting_a_key_removes_the_file():
    secrets.store_key("deepgram", "secret")
    assert secrets.forget_key("deepgram") is True
    assert secrets.forget_key("deepgram") is False
    assert secrets.read_key("DEEPGRAM_API_KEY", "deepgram") == ""


def test_the_source_is_reported_so_doctor_can_explain_itself(monkeypatch):
    assert secrets.describe_source("DEEPGRAM_API_KEY", "deepgram") == "not set"
    path = secrets.store_key("deepgram", "secret")
    assert secrets.describe_source("DEEPGRAM_API_KEY", "deepgram") == str(path)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "x")
    assert secrets.describe_source("DEEPGRAM_API_KEY", "deepgram") == "$DEEPGRAM_API_KEY"


def test_an_empty_file_does_not_count_as_a_key():
    secrets.store_key("deepgram", "")
    assert secrets.read_key("DEEPGRAM_API_KEY", "deepgram") == ""
