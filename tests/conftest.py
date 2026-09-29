"""Make the macOS-only modules importable off a Mac."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import stubs  # noqa: E402  (tests/ is on sys.path via rootdir)

STUBBED = stubs.install()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _private_config_file(tmp_path, monkeypatch):
    """Keep every test away from the real config.json.

    Several code paths save the config as a side effect -- switching engines,
    choosing a file engine, picking a microphone -- and without this, running
    the suite on a Mac rewrote the user's own settings with whatever the last
    test chose. A test that wants a specific file still patches CONFIG_FILE
    itself; this only changes the default.
    """
    monkeypatch.setattr("aloud.config.CONFIG_FILE", tmp_path / "config.json")
