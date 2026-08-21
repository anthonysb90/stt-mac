"""The Dictionary store: editing, search, biasing, and the hand-editable file."""

import json

import pytest

from aloud.dictionary import CORRECTION, TERM, Dictionary, DictionaryError, Entry


@pytest.fixture
def dictionary(tmp_path, monkeypatch):
    monkeypatch.setattr("aloud.dictionary.ensure_dirs", lambda: None)
    return Dictionary(path=tmp_path / "dictionary.json")


# -- entries -----------------------------------------------------------------


def test_a_term_derives_its_own_pattern_and_replacement():
    entry = Entry(kind=TERM, term="Anthropic")
    assert entry.pattern == entry.replacement == "Anthropic"
    assert entry.label == "Anthropic"


def test_a_correction_maps_heard_to_written():
    entry = Entry(kind=CORRECTION, heard="cloud code", write="Claude Code")
    assert entry.pattern == "cloud code"
    assert entry.replacement == "Claude Code"
    assert entry.label == "cloud code → Claude Code"


def test_entries_get_an_id_and_a_timestamp_for_free():
    entry = Entry(kind=TERM, term="Vercel")
    assert entry.id and entry.created_at > 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": TERM, "term": "   "},
        {"kind": CORRECTION, "heard": "", "write": "Claude Code"},
        {"kind": CORRECTION, "heard": "cloud code", "write": ""},
        {"kind": "nonsense", "term": "x"},
    ],
)
def test_invalid_entries_are_rejected(kwargs):
    with pytest.raises(DictionaryError):
        Entry(**kwargs)


# -- editing -----------------------------------------------------------------


def test_add_edit_delete(dictionary):
    entry = dictionary.add_term("Vercel")
    assert len(dictionary) == 1

    dictionary.update(entry.id, term="Vercel Inc")
    assert dictionary.get(entry.id).term == "Vercel Inc"

    assert dictionary.remove(entry.id) is True
    assert dictionary.remove(entry.id) is False
    assert len(dictionary) == 0


def test_editing_validates_the_result(dictionary):
    entry = dictionary.add_term("Vercel")
    with pytest.raises(DictionaryError):
        dictionary.update(entry.id, term="  ")
    with pytest.raises(DictionaryError):
        dictionary.update(entry.id, nonexistent="x")


def test_search_covers_every_text_field(dictionary):
    dictionary.add_term("Anthropic", notes="the lab")
    dictionary.add_correction("cloud code", "Claude Code")

    assert len(dictionary.search("anthropic")) == 1
    assert len(dictionary.search("the lab")) == 1
    assert len(dictionary.search("CLOUD")) == 1
    assert len(dictionary.search("Claude")) == 1
    assert len(dictionary.search("")) == 2
    assert dictionary.search("nothing here") == []


def test_disabled_entries_are_excluded_from_the_enabled_view(dictionary):
    dictionary.add_term("Vercel")
    dictionary.add(Entry(kind=TERM, term="Supabase", enabled=False))
    assert [e.term for e in dictionary.enabled()] == ["Vercel"]


def test_hits_accumulate(dictionary):
    entry = dictionary.add_correction("cloud code", "Claude Code")
    dictionary.record_hit(entry.id, 2)
    dictionary.record_hit(entry.id)
    assert dictionary.get(entry.id).hits == 3


# -- biasing -----------------------------------------------------------------


def test_bias_terms_returns_what_we_want_the_model_to_produce(dictionary):
    dictionary.add_term("Supabase")
    dictionary.add_correction("cloud code", "Claude Code")
    assert set(dictionary.bias_terms()) == {"Supabase", "Claude Code"}


def test_bias_terms_stays_short(dictionary):
    for index in range(40):
        dictionary.add_term(f"Term{index}")
    assert len(dictionary.bias_terms(limit=12)) == 12


def test_bias_terms_prefers_the_most_recently_added(dictionary):
    old = dictionary.add_term("OldTerm")
    new = dictionary.add_term("NewTerm")
    old.created_at, new.created_at = 100.0, 200.0
    assert dictionary.bias_terms(limit=1) == ["NewTerm"]


def test_bias_terms_skips_disabled_and_duplicates(dictionary):
    dictionary.add_term("Supabase")
    dictionary.add_term("supabase")
    dictionary.add(Entry(kind=TERM, term="Hidden", enabled=False))
    assert dictionary.bias_terms() == ["Supabase"] or dictionary.bias_terms() == ["supabase"]
    assert len(dictionary.bias_terms()) == 1


# -- the file ----------------------------------------------------------------


def test_save_then_load_round_trips(dictionary):
    dictionary.add_term("Anthropic", notes="the lab")
    dictionary.add_correction("cloud code", "Claude Code")
    dictionary.save()

    reloaded = Dictionary.load(dictionary.path)
    assert [e.label for e in reloaded] == ["Anthropic", "cloud code → Claude Code"]
    assert reloaded.entries[0].notes == "the lab"


def test_the_file_is_plain_and_hand_writable(dictionary):
    """Someone editing this by hand should not have to invent ids or kinds."""
    dictionary.path.write_text(
        json.dumps(
            {
                "entries": [
                    {"term": "Anthropic"},
                    {"heard": "cloud code", "write": "Claude Code"},
                ]
            }
        )
    )
    loaded = Dictionary.load(dictionary.path)
    assert [e.kind for e in loaded] == [TERM, CORRECTION]
    assert all(entry.id for entry in loaded)


def test_a_bare_list_of_entries_is_accepted(dictionary):
    dictionary.path.write_text(json.dumps([{"term": "Vercel"}]))
    assert len(Dictionary.load(dictionary.path)) == 1


def test_unknown_keys_survive_a_round_trip(dictionary):
    """Never silently discard something a future version or a human added."""
    dictionary.path.write_text(json.dumps({"entries": [{"term": "Vercel", "colour": "blue"}]}))
    loaded = Dictionary.load(dictionary.path)
    loaded.save()
    assert json.loads(dictionary.path.read_text())["entries"][0]["colour"] == "blue"


def test_one_bad_entry_does_not_lose_the_others(dictionary):
    dictionary.path.write_text(
        json.dumps({"entries": [{"term": "Vercel"}, {"term": "   "}, "junk", {"term": "Supabase"}]})
    )
    assert [e.term for e in Dictionary.load(dictionary.path)] == ["Vercel", "Supabase"]


def test_unreadable_json_keeps_what_is_in_memory(dictionary):
    dictionary.add_term("Vercel")
    dictionary.path.write_text("{ not json")
    dictionary._read()
    assert [e.term for e in dictionary] == ["Vercel"]


def test_loading_a_missing_file_creates_it(tmp_path, monkeypatch):
    monkeypatch.setattr("aloud.dictionary.ensure_dirs", lambda: None)
    path = tmp_path / "dictionary.json"
    loaded = Dictionary.load(path)
    assert path.exists() and len(loaded) == 0


def test_outside_edits_are_picked_up_while_running(dictionary):
    dictionary.add_term("Vercel")
    dictionary.save()
    assert dictionary.reload_if_changed() is False

    payload = json.loads(dictionary.path.read_text())
    payload["entries"].append({"term": "Supabase"})
    # Move the mtime forward explicitly; the write may land in the same tick.
    dictionary.path.write_text(json.dumps(payload))
    import os
    os.utime(dictionary.path, (0, dictionary._mtime + 10))

    assert dictionary.reload_if_changed() is True
    assert [e.term for e in dictionary] == ["Vercel", "Supabase"]


def test_saving_is_atomic(dictionary):
    """A crash mid-save must not truncate a file the user may have open."""
    dictionary.add_term("Vercel")
    dictionary.save()
    assert not dictionary.path.with_suffix(".json.tmp").exists()
    assert json.loads(dictionary.path.read_text())["version"] == 1
