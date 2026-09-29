"""Sharing the Dictionary through iCloud Drive."""

import pytest

from aloud import sync
from aloud.config import Config
from aloud.dictionary import Dictionary, Entry

from test_core_pipeline import app  # noqa: F401  (fixture)


@pytest.fixture
def icloud(tmp_path, monkeypatch):
    root = tmp_path / "CloudDocs"
    root.mkdir()
    monkeypatch.setattr(sync, "ICLOUD_ROOT", root)
    return root


def correction(heard, write, hits=0):
    entry = Entry(kind="correction", heard=heard, write=write)
    entry.hits = hits
    return entry


def test_merging_keeps_everything_once():
    a = [correction("cloud code", "Claude Code", hits=3), Entry(kind="term", term="Supabase")]
    b = [correction("Cloud Code", "Claude Code", hits=2), correction("romans ate", "Romans 8")]
    merged = sync.merge_entries(a, b)
    assert [e.label for e in merged] == ["cloud code → Claude Code", "Supabase",
                                         "romans ate → Romans 8"]
    assert merged[0].hits == 5


def test_turning_sync_on_merges_with_the_other_macs_dictionary(app, icloud):
    other_mac = Dictionary([correction("cloud code", "Claude Code")], path=sync.icloud_path())
    other_mac.save()
    app.dictionary.add_correction("romans ate", "Romans 8")
    app.save_dictionary()

    app.set_dictionary_sync(True)

    assert app.dictionary.path == sync.icloud_path()
    labels = {e.label for e in app.dictionary.entries}
    assert labels == {"cloud code → Claude Code", "romans ate → Romans 8"}
    assert app.corrections_for("open cloud code").text == "open Claude Code"
    assert app.config.get("sync.dictionary") == "icloud"


def test_conflicted_copies_are_folded_in_and_removed(app, icloud):
    app.set_dictionary_sync(True)
    copy = sync.icloud_path().with_name("dictionary 2.json")
    Dictionary([correction("supa base", "Supabase")], path=copy).save()

    app.refresh_dictionary()

    assert not copy.exists()
    assert "supa base → Supabase" in {e.label for e in app.dictionary.entries}


def test_an_evicted_file_is_never_taken_for_an_empty_one(icloud, monkeypatch):
    sync.icloud_path().parent.mkdir(parents=True)
    sync.placeholder(sync.icloud_path()).write_text("stub")
    monkeypatch.setattr(sync.subprocess, "run", lambda *a, **k: None)
    config = Config()
    config.set("sync.dictionary", "icloud")
    assert not sync.ensure_downloaded(sync.icloud_path(), timeout=0.1)
    loaded = sync.load(config, timeout=0.1)
    assert loaded.path != sync.icloud_path(), "fell back to the local copy"
    assert not sync.icloud_path().exists(), "nothing was saved over the shared file"


def test_sync_needs_icloud_drive(app, tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "ICLOUD_ROOT", tmp_path / "missing")
    with pytest.raises(OSError, match="iCloud Drive is not turned on"):
        app.set_dictionary_sync(True)


def test_turning_sync_off_brings_the_entries_home(app, icloud, tmp_path, monkeypatch):
    import aloud.dictionary as dictionary_module

    monkeypatch.setattr(dictionary_module, "DICTIONARY_FILE", tmp_path / "local.json")
    app.set_dictionary_sync(True)
    app.dictionary.add_correction("cloud code", "Claude Code")
    app.save_dictionary()
    app.set_dictionary_sync(False)
    assert app.dictionary.path == tmp_path / "local.json"
    assert Dictionary.load(tmp_path / "local.json").entries
