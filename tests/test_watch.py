"""Watch folders: which files are taken, when, and only once."""

import os
from pathlib import Path

from aloud import watch
from aloud.core import Job

from test_core_pipeline import _silent_wav, app  # noqa: F401  (fixture)


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def make(tmp_path, export="docx", clock=None):
    folder = tmp_path / "Sunday Recordings"
    folder.mkdir()
    queued = []
    settings = watch.Settings(folders=[folder], export=export)
    watcher = watch.Watcher(settings, lambda p, e: queued.append((p, e)) or True,
                            state_file=tmp_path / "state.json", clock=clock or Clock())
    return folder, watcher, queued


def age(path: Path, seconds_ago: float, now: float) -> None:
    os.utime(path, (now - seconds_ago, now - seconds_ago))


def test_a_new_finished_recording_is_queued_once(tmp_path):
    clock = Clock()
    folder, watcher, queued = make(tmp_path, clock=clock)
    sermon = folder / "sermon.mp3"
    sermon.write_bytes(b"audio")
    age(sermon, 60, clock.now)

    assert watcher.scan() == []            # first look: note its size
    assert watcher.scan() == [sermon]      # unchanged on the second: take it
    assert queued[0][1] == [("docx", "manuscript", str(folder / "Transcripts"))]
    assert watcher.scan() == []            # never twice


def test_a_file_still_copying_waits(tmp_path):
    clock = Clock()
    folder, watcher, queued = make(tmp_path, clock=clock)
    growing = folder / "service.m4a"
    growing.write_bytes(b"a")
    age(growing, 60, clock.now)
    watcher.scan()
    growing.write_bytes(b"ab")              # it grew between looks
    age(growing, 60, clock.now)
    assert watcher.scan() == []
    assert watcher.scan() == [growing]


def test_a_file_only_just_written_waits(tmp_path):
    clock = Clock()
    folder, watcher, _ = make(tmp_path, clock=clock)
    fresh = folder / "new.wav"
    fresh.write_bytes(b"x")
    age(fresh, 1, clock.now)
    watcher.scan()
    assert watcher.scan() == []


def test_files_already_there_are_left_alone_unless_asked(tmp_path):
    clock = Clock()
    folder, watcher, _ = make(tmp_path, clock=clock)
    old = folder / "2019 sermon.mp3"
    old.write_bytes(b"x")
    age(old, 9999, clock.now)
    assert watcher.adopt(folder) == 1
    watcher.scan()
    assert watcher.scan() == []

    other = tmp_path / "Archive"
    other.mkdir()
    (other / "a.mp3").write_bytes(b"x")
    age(other / "a.mp3", 9999, clock.now)
    watcher.settings.folders.append(other)
    watcher.adopt(other, include_existing=True)
    watcher.scan()
    assert watcher.scan() == [other / "a.mp3"]


def test_its_own_output_and_junk_are_ignored(tmp_path):
    clock = Clock()
    folder, watcher, _ = make(tmp_path, clock=clock)
    (folder / "Transcripts").mkdir()
    for name in ("Transcripts/sermon.docx", ".hidden.mp3", "~$lock.mp3", "notes.txt",
                 "clip.mp3.icloud"):
        (folder / name).write_bytes(b"x")
    watcher.scan()
    assert watcher.scan() == []


def test_what_was_done_survives_a_restart(tmp_path):
    clock = Clock()
    folder, watcher, _ = make(tmp_path, clock=clock)
    sermon = folder / "sermon.mp3"
    sermon.write_bytes(b"x")
    age(sermon, 60, clock.now)
    watcher.scan()
    watcher.scan()
    watcher.finished(sermon, ok=True)

    again = watch.Watcher(watcher.settings, lambda p, e: True,
                          state_file=tmp_path / "state.json", clock=clock)
    again.scan()
    assert again.scan() == [] and again.status(sermon) == "done"


def test_a_failure_is_recorded_and_not_retried(tmp_path):
    clock = Clock()
    folder, watcher, queued = make(tmp_path, clock=clock)
    broken = folder / "broken.mp3"
    broken.write_bytes(b"x")
    age(broken, 60, clock.now)
    watcher.scan()
    watcher.scan()
    watcher.finished(broken, ok=False, detail="ffmpeg could not read it")
    watcher.scan()
    assert len(queued) == 1 and watcher.status(broken) == "failed"


def test_no_export_means_library_only(tmp_path):
    settings = watch.Settings(folders=[tmp_path], export="")
    assert settings.exports_for(tmp_path / "a.mp3") == []


def test_a_watched_file_is_exported_next_to_itself(app, tmp_path):
    folder = tmp_path / "Recordings"
    folder.mkdir()
    source = _silent_wav(folder / "evening service.wav")
    job = Job(audio=source, deliver=False, source="file", label=source.name,
              origin="watch", exports=[("docx", "manuscript", str(folder / "Transcripts")),
                                       ("txt", "plain", str(folder / "Transcripts"))])
    seen = []
    app.add_observer(type("L", (), {"on_result": lambda _s, d: seen.append(d)})())
    app._run_job(job)
    written = seen[-1].exported
    assert [p.name for p in written] == ["evening service.docx", "evening service.txt"]
    assert all(p.parent == folder / "Transcripts" and p.stat().st_size for p in written)

    app._run_job(Job(audio=source, deliver=False, source="file", label=source.name,
                     exports=[("txt", "plain", str(folder / "Transcripts"))]))
    assert (folder / "Transcripts" / "evening service (2).txt").exists(), "never overwritten"


def test_a_job_that_finishes_during_hand_over_is_still_recorded(tmp_path):
    """The file is marked before it is handed over, so an instant result lands."""
    clock = Clock()
    folder = tmp_path / "In"
    folder.mkdir()
    sermon = folder / "sermon.mp3"
    sermon.write_bytes(b"x")
    age(sermon, 60, clock.now)
    watcher = None

    def queue(path, exports):
        watcher.finished(path, ok=False, detail="ffmpeg failed at once")
        return True

    watcher = watch.Watcher(watch.Settings(folders=[folder]), queue,
                            state_file=tmp_path / "state.json", clock=clock)
    watcher.scan()
    watcher.scan()
    assert watcher.status(sermon) == "failed"


def test_a_refused_hand_over_leaves_the_file_to_try_again(tmp_path):
    clock = Clock()
    folder = tmp_path / "In"
    folder.mkdir()
    sermon = folder / "sermon.mp3"
    sermon.write_bytes(b"x")
    age(sermon, 60, clock.now)
    watcher = watch.Watcher(watch.Settings(folders=[folder]), lambda p, e: False,
                            state_file=tmp_path / "state.json", clock=clock)
    watcher.scan()
    watcher.scan()
    assert watcher.status(sermon) == ""
