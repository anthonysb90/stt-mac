"""The transcript library: saving, reopening, renaming, searching, deleting."""

import json

import pytest

from aloud import library
from aloud.core import Job
from aloud.engines.base import Segment

from test_core_pipeline import _silent_wav, app  # noqa: F401  (fixture)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "Transcripts"


def record(**changes):
    values = dict(
        title="Sunday Sermon", text="Grace and peace. Amen.",
        raw_text="grace and peace amen",
        segments=[Segment(0.0, 2.0, "Grace and peace.", "A"), Segment(2.5, 3.0, "Amen.", "B")],
        source_name="sermon.mp4", audio_seconds=3.0, engine="groq",
    )
    values.update(changes)
    return library.Record(**values)


# -- on disk --------------------------------------------------------------------


def test_a_saved_transcript_round_trips(root):
    original = record(speakers={"A": "Pastor Tim"})
    folder = library.save(original, root)
    assert folder.parent == root and folder.name.endswith("Sunday Sermon")
    loaded = library.load(folder)
    assert loaded.id == original.id and loaded.title == "Sunday Sermon"
    assert loaded.raw_text == "grace and peace amen"
    assert loaded.segments == original.segments
    assert loaded.speaker_name("A") == "Pastor Tim" and loaded.speaker_name("B") == "Speaker 2"
    assert loaded.folder == folder


def test_a_plain_text_copy_is_kept_for_finder_and_spotlight(root):
    folder = library.save(record(), root)
    text = (folder / library.TEXT_FILE).read_text()
    assert "Grace and peace." in text and "Speaker 1" in text


def test_the_json_is_readable_on_its_own(root):
    data = json.loads((library.save(record(), root) / library.DATA_FILE).read_text())
    assert data["version"] == library.FORMAT_VERSION
    assert data["segments"][0] == {"start": 0.0, "end": 2.0, "text": "Grace and peace.",
                                   "speaker": "A"}


def test_same_titles_on_the_same_day_get_their_own_folders(root):
    first = library.save(record(), root)
    second = library.save(record(), root)
    assert first != second and second.name.endswith("(2)")


def test_titles_that_are_not_valid_folder_names_are_cleaned(root):
    folder = library.save(record(title='Q&A: "Why?" 1/2'), root)
    assert "/" not in folder.name and ":" not in folder.name and folder.is_dir()


def test_saving_again_updates_in_place(root):
    r = record()
    folder = library.save(r, root)
    r.speakers = {"B": "Deacon Ray"}
    assert library.save(r, root) == folder
    assert library.load(folder).speakers == {"B": "Deacon Ray"}
    assert not list(folder.glob("*.tmp"))


def test_rename_moves_the_folder_with_the_title(root):
    r = record()
    library.save(r, root)
    library.rename(r, "Romans 8 — Part 1", root)
    assert r.folder.name.endswith("Romans 8 — Part 1")
    assert library.load(r.folder).title == "Romans 8 — Part 1"
    assert len(list(root.iterdir())) == 1


def test_a_damaged_transcript_is_skipped_not_fatal(root):
    library.save(record(), root)
    broken = root / "2026-01-01 broken"
    broken.mkdir()
    (broken / library.DATA_FILE).write_text("{not json")
    assert [r.title for r in library.all_records(root)] == ["Sunday Sermon"]


def test_delete_uses_the_trash_when_given_one(root):
    r = record()
    library.save(r, root)
    trashed = []
    assert library.delete(r, trash=lambda path: trashed.append(path) or True)
    assert trashed == [r.folder]
    assert library.delete(r) and not r.folder.exists()


def test_the_index_rereads_only_what_changed(root, monkeypatch):
    a, b = record(title="A"), record(title="B")
    library.save(a, root)
    library.save(b, root)
    index = library.Index(root)
    assert {r.title for r in index.records()} == {"A", "B"}

    loads = []
    real = library.load
    monkeypatch.setattr(library, "load", lambda folder: loads.append(folder) or real(folder))
    index.records()
    assert loads == []  # nothing changed
    import os
    import time

    b.speakers = {"A": "X"}
    library.save(b, root)
    stamp = time.time() + 5
    os.utime(b.folder / library.DATA_FILE, (stamp, stamp))
    index.records()
    assert loads == [b.folder]


# -- search ---------------------------------------------------------------------


def test_search_needs_every_word_somewhere(root):
    records = [record(title="Sunday sermon", text="Grace abounds."),
               record(title="Board meeting", text="Budget and grace period.")]
    assert [h.record.title for h in library.search("sermon grace", records)] == ["Sunday sermon"]
    assert len(library.search("GRACE", records)) == 2
    assert library.search("nothing", records) == []


def test_search_finds_speaker_names(root):
    r = record(speakers={"A": "Pastor Tim"})
    assert library.search("tim", [r])


def test_search_ranks_by_how_often_the_words_occur():
    records = [record(title="Once", text="faith"), record(title="Thrice", text="faith faith faith")]
    assert [h.record.title for h in library.search("faith", records)] == ["Thrice", "Once"]


def test_snippets_show_the_words_around_the_match():
    text = "word " * 50 + "the needle is here " + "word " * 50
    snippet = library.snippet(text, "needle")
    assert "needle" in snippet and snippet.startswith("…") and snippet.endswith("…")
    assert len(snippet) < 200


def test_find_all_is_case_insensitive_and_does_not_overlap():
    assert library.find_all("Amen, amen. AMEN", "amen") == [(0, 4), (6, 4), (12, 4)]
    assert library.find_all("aaaa", "aa") == [(0, 2), (2, 2)]
    assert library.find_all("text", "") == []


def test_speakers_are_numbered_by_first_appearance():
    r = record(segments=[Segment(0, 1, "x", "B"), Segment(1, 2, "y", "A")])
    assert r.speaker_labels == ["B", "A"]
    assert r.speaker_name("B") == "Speaker 1"


def test_titles_come_from_file_names():
    assert library.title_from("sunday_sermon.mp4") == "sunday sermon"
    assert library.title_from("") == "Untitled transcript"


# -- the pipeline saves every file ------------------------------------------------


def run_file(app, path):
    seen = []
    app.add_observer(type("L", (), {"on_result": lambda _s, d: seen.append(d)})())
    app._run_job(Job(audio=path, deliver=False, source="file", label=path.name))
    return seen[-1]


def test_a_transcribed_file_is_saved_to_the_library(app, tmp_path):
    source = _silent_wav(tmp_path / "evening_service.wav", seconds=2)
    dictation = run_file(app, source)
    saved = library.all_records()
    assert len(saved) == 1 and dictation.record is not None
    r = saved[0]
    assert r.id == dictation.record.id
    assert r.title == "evening service" and r.source_path == str(source)
    assert r.audio_seconds == pytest.approx(2.0)
    assert r.raw_text  # what the engine produced, before cleanup


def test_dictation_is_not_saved_to_the_library(app, tmp_path):
    app._run_job(Job(audio=_silent_wav(tmp_path / "d.wav"), deliver=True))
    assert library.all_records() == []


def test_the_library_can_be_turned_off(app, tmp_path):
    app.config.set("library.enabled", False)
    dictation = run_file(app, _silent_wav(tmp_path / "a.wav"))
    assert dictation.record is None and library.all_records() == []


def test_a_library_that_cannot_be_written_does_not_lose_the_result(app, tmp_path, monkeypatch):
    def full_disk(*_a, **_k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(library, "save", full_disk)
    dictation = run_file(app, _silent_wav(tmp_path / "a.wav"))
    assert dictation.text and dictation.record is None


def test_search_ranges_count_the_way_appkit_does():
    """An emoji is one Python character but two UTF-16 units."""
    text = "🙏 Amen. Pray. amen"
    ranges = library.find_all_utf16(text, "amen")
    encoded = text.encode("utf-16-le")
    words = [encoded[s * 2:(s + n) * 2].decode("utf-16-le") for s, n in ranges]
    assert words == ["Amen", "amen"]
    assert library.utf16_length(text) == len(text) + 1
    assert library.find_all_utf16(text, "  ") == []
    assert library.find_all_utf16("a.b a+b", "a+b") == [(4, 3)]  # not a regex
