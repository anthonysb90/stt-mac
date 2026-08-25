"""End-to-end wiring: hotkey edge -> recording -> engine -> delivery.

Exercises :class:`aloud.core.DictationController` -- the pipeline with no user
interface attached -- so the real state machine, queue, dictionary and
post-processing run without a microphone, a model, or a window.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from aloud.core import DictationController, Job, State
from aloud.config import Config


def _silent_wav(path: Path, seconds: float = 1.0, rate: int = 16000) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * int(rate * seconds))
    return path


class FakeRecorder:
    """Stands in for the PortAudio recorder."""

    def __init__(self, wav_path: Path) -> None:
        self.wav_path = wav_path
        self.recording = False
        self.starts = 0

    def start(self) -> None:
        self.recording = True
        self.starts += 1

    def stop(self):
        self.recording = False
        return self.wav_path

    def cancel(self) -> None:
        self.recording = False


class CapturingInjector:
    def __init__(self) -> None:
        self.delivered: list[str] = []

    def deliver(self, text: str) -> None:
        self.delivered.append(text)


def _rebuild(app):
    """Recompile the ruleset after a test edits the dictionary."""
    from aloud import corrections

    return corrections.ruleset_for(app.dictionary)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr("aloud.history.HISTORY_FILE", tmp_path / "history.jsonl")
    monkeypatch.setattr("aloud.history.ensure_dirs", lambda: None)
    monkeypatch.setattr("aloud.dictionary.DICTIONARY_FILE", tmp_path / "dictionary.json")
    monkeypatch.setattr("aloud.dictionary.ensure_dirs", lambda: None)

    config = Config()
    config.set("engine", "mock")
    config.set("engines.mock.text", "um, hello there new line friend")
    config.set("feedback.sounds", False)
    config.set("feedback.notify_on_error", False)

    monkeypatch.setattr("aloud.core.Recorder", lambda **_kwargs: FakeRecorder(tmp_path / "x.wav"))
    instance = DictationController(config)
    instance.recorder = FakeRecorder(_silent_wav(tmp_path / "speech.wav"))
    instance.injector = CapturingInjector()
    return instance


def test_press_starts_recording_and_release_queues_a_job(app):
    app.begin_recording()
    assert app.state is State.RECORDING
    assert app.recorder.recording

    app.finish_recording()
    assert app.state is State.TRANSCRIBING
    assert app._jobs.qsize() == 1


def test_second_press_while_recording_is_ignored(app):
    app.begin_recording()
    app.begin_recording()
    assert app.recorder.starts == 1


def test_release_without_a_press_does_nothing(app):
    app.finish_recording()
    assert app.state is State.IDLE
    assert app._jobs.qsize() == 0


def test_transcript_is_post_processed_before_delivery(app, tmp_path):
    wav = _silent_wav(tmp_path / "job.wav")
    app._transcribe_and_deliver(Job(audio=wav))
    # Filler stripped, spoken command expanded, first letter capitalised.
    assert app.injector.delivered == ["Hello there\nfriend"]
    assert app.state is State.IDLE


def test_short_recordings_are_discarded(app, tmp_path):
    app.recorder = FakeRecorder(_silent_wav(tmp_path / "blip.wav", seconds=0.1))
    app.begin_recording()
    app.finish_recording()
    assert app.state is State.IDLE
    assert app._jobs.qsize() == 0


def test_empty_transcript_is_not_delivered(app, tmp_path):
    app.config.set("engines.mock.text", "")
    app.engine.options["text"] = ""
    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "quiet.wav")))
    assert app.injector.delivered == []


def test_dictation_is_written_to_history(app, tmp_path):
    from aloud import history

    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    entries = history.recent(5)
    assert entries and entries[0]["text"] == "Hello there\nfriend"


# -- dictionary integration --------------------------------------------------


def test_the_dictionary_corrects_before_the_text_is_delivered(app, tmp_path):
    app.config.set("engines.mock.text", "we shipped cloud code today")
    app.engine.options["text"] = "we shipped cloud code today"
    app.dictionary.add_correction("cloud code", "Claude Code")
    app._ruleset = _rebuild(app)

    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    assert app.injector.delivered == ["We shipped Claude Code today"]


def test_a_correction_that_fires_is_written_to_history(app, tmp_path):
    from aloud import history

    app.config.set("engines.mock.text", "open cloud code")
    app.engine.options["text"] = "open cloud code"
    app.dictionary.add_correction("cloud code", "Claude Code")
    app._ruleset = _rebuild(app)

    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    entry = history.recent(1)[0]
    assert entry["corrections"] == [
        {"entry": app.dictionary.entries[0].id, "from": "cloud code", "to": "Claude Code", "at": 5}
    ]
    assert entry["raw"] == "open cloud code"


def test_history_stays_lean_when_nothing_was_corrected(app, tmp_path):
    from aloud import history

    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    entry = history.recent(1)[0]
    assert "corrections" not in entry and "raw" not in entry


def test_entries_count_their_hits(app, tmp_path):
    app.config.set("engines.mock.text", "cloud code and cloud code")
    app.engine.options["text"] = "cloud code and cloud code"
    entry = app.dictionary.add_correction("cloud code", "Claude Code")
    app._ruleset = _rebuild(app)

    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    assert app.dictionary.get(entry.id).hits == 2


def test_bias_terms_reach_an_engine_that_supports_them(app, tmp_path):
    app.dictionary.add_term("Supabase")
    transcript = app.engine.transcribe(
        _silent_wav(tmp_path / "job.wav"), bias_terms=app.bias_terms()
    )
    assert transcript.meta["bias"] == ["Supabase"]


def test_no_bias_is_sent_to_an_engine_that_cannot_use_it(app):
    app.dictionary.add_term("Supabase")
    app.engine.supports_bias = False
    assert app.bias_terms() == []


def test_biasing_can_be_turned_off_without_disabling_corrections(app, tmp_path):
    app.config.set("dictionary.bias.enabled", False)
    app.config.set("engines.mock.text", "cloud code")
    app.engine.options["text"] = "cloud code"
    app.dictionary.add_term("Supabase")
    app.dictionary.add_correction("cloud code", "Claude Code")
    app._ruleset = _rebuild(app)

    assert app.bias_terms() == []
    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    assert app.injector.delivered == ["Claude Code"]


def test_disabling_the_dictionary_turns_off_both_mechanisms(app, tmp_path):
    app.config.set("dictionary.enabled", False)
    app.config.set("engines.mock.text", "cloud code")
    app.engine.options["text"] = "cloud code"
    app.dictionary.add_correction("cloud code", "Claude Code")
    app._ruleset = _rebuild(app)

    assert app.bias_terms() == []
    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    assert app.injector.delivered == ["Cloud code"]


# -- cleanup ownership -------------------------------------------------------


def test_local_cleanup_runs_when_the_engine_does_not_do_it(app):
    options = app.postprocess_options()
    assert options["strip_fillers"] is True
    assert options["commands"]


def test_local_cleanup_stands_down_when_the_engine_handles_it(app):
    """Deepgram punctuates and strips fillers server-side; doing it twice hurts."""
    app.engine.handles_cleanup = True
    options = app.postprocess_options()
    assert options["strip_fillers"] is False
    assert options["capitalize_first"] is False
    assert options["commands"] == {}
    # Whitespace tidying is cheap and idempotent, so it always runs.
    assert options["collapse_whitespace"] is True


def test_deferring_cleanup_does_not_disable_corrections(app, tmp_path):
    app.engine.handles_cleanup = True
    app.config.set("engines.mock.text", "We shipped cloud code today.")
    app.engine.options["text"] = "We shipped cloud code today."
    app.dictionary.add_correction("cloud code", "Claude Code")
    app._ruleset = _rebuild(app)

    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    assert app.injector.delivered == ["We shipped Claude Code today."]


def test_the_engine_keeps_its_own_capitalisation_when_it_owns_cleanup(app, tmp_path):
    app.engine.handles_cleanup = True
    app.config.set("engines.mock.text", "iPhone settings")
    app.engine.options["text"] = "iPhone settings"
    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    assert app.injector.delivered == ["iPhone settings"]


# -- observers ---------------------------------------------------------------


class Recorder:
    def __init__(self):
        self.states = []
        self.results = []
        self.errors = []

    def on_state(self, state):
        self.states.append(state)

    def on_result(self, dictation):
        self.results.append(dictation)

    def on_error(self, title, message):
        self.errors.append((title, message))


def test_observers_see_the_state_machine(app, tmp_path):
    watcher = Recorder()
    app.add_observer(watcher)
    app.begin_recording()
    app.finish_recording()
    assert State.RECORDING in watcher.states
    assert State.TRANSCRIBING in watcher.states


def test_observers_receive_the_finished_dictation(app, tmp_path):
    watcher = Recorder()
    app.add_observer(watcher)
    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    assert len(watcher.results) == 1
    assert watcher.results[0].text == app.injector.delivered[0]
    assert app.last is watcher.results[0]


def test_an_observer_that_raises_does_not_break_the_pipeline(app, tmp_path):
    class Broken:
        def on_result(self, _dictation):
            raise RuntimeError("boom")

    app.add_observer(Broken())
    watcher = Recorder()
    app.add_observer(watcher)
    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))
    assert len(watcher.results) == 1


def test_a_removed_observer_stops_hearing_about_it(app):
    watcher = Recorder()
    app.add_observer(watcher)
    app.remove_observer(watcher)
    app.begin_recording()
    assert watcher.states == []


def test_toggle_starts_then_stops(app):
    app.toggle()
    assert app.state is State.RECORDING
    app.toggle()
    assert app.state is State.TRANSCRIBING


def test_cancelling_discards_the_recording(app):
    app.begin_recording()
    app.cancel_recording()
    assert app.state is State.IDLE
    assert app._jobs.qsize() == 0


def test_a_dictation_reports_whether_the_dictionary_touched_it(app, tmp_path):
    app.config.set("engines.mock.text", "open cloud code")
    app.engine.options["text"] = "open cloud code"
    app.dictionary.add_correction("cloud code", "Claude Code")
    app._ruleset = _rebuild(app)
    app._transcribe_and_deliver(Job(audio=_silent_wav(tmp_path / "job.wav")))

    assert app.last.was_corrected
    assert app.last.correction_summary == "cloud code → Claude Code"
    assert app.last.raw == "open cloud code"


# -- importing an audio file -------------------------------------------------


def _prepared(path, converted=False, temporary=False):
    from aloud.media import Prepared

    return Prepared(path=path, temporary=temporary, converted=converted, original=path)


def test_an_imported_file_is_never_typed_into_the_focused_app(app, tmp_path, monkeypatch):
    """The whole reason a job carries `deliver`.

    Transcribing an hour-long recording must not dump it into whatever
    document happens to be open.
    """
    source = _silent_wav(tmp_path / "meeting.wav", seconds=2)
    monkeypatch.setattr("aloud.media.prepare", lambda p: _prepared(source))

    app.transcribe_file(source)
    job = app._jobs.get()
    assert job.deliver is False
    assert job.source == "file"
    assert job.label == "meeting.wav"

    app._transcribe_and_deliver(job)
    assert app.injector.delivered == []


def test_an_imported_file_still_reaches_the_history_and_observers(app, tmp_path, monkeypatch):
    from aloud import history

    source = _silent_wav(tmp_path / "memo.wav", seconds=2)
    monkeypatch.setattr("aloud.media.prepare", lambda p: _prepared(source))
    watcher = Recorder()
    app.add_observer(watcher)

    app.transcribe_file(source)
    app._transcribe_and_deliver(app._jobs.get())

    assert len(watcher.results) == 1
    assert watcher.results[0].source == "file"
    assert watcher.results[0].label == "memo.wav"
    assert history.recent(1)[0]["text"] == watcher.results[0].text


def test_a_converted_file_is_cleaned_up_afterwards(app, tmp_path, monkeypatch):
    """The temporary WAV ffmpeg wrote must not be left behind."""
    original = tmp_path / "voice.m4a"
    original.write_bytes(b"not really an m4a")
    converted = _silent_wav(tmp_path / "converted.wav", seconds=2)
    removed = []

    prepared = _prepared(converted, converted=True, temporary=True)
    monkeypatch.setattr(prepared, "cleanup", lambda: removed.append(converted))
    monkeypatch.setattr("aloud.media.prepare", lambda p: prepared)

    app.transcribe_file(original)
    job = app._jobs.get()
    app._drain_one_for_test(job)
    assert removed == [converted]


def test_a_microphone_recording_is_still_deleted_after_use(app, tmp_path):
    wav = _silent_wav(tmp_path / "dictation.wav")
    app._drain_one_for_test(Job(audio=wav, deliver=True, source="microphone"))
    assert not wav.exists()


def test_an_unreadable_file_reports_instead_of_crashing(app, tmp_path, monkeypatch):
    """Conversion now happens on the worker, so its failure surfaces there."""
    from aloud.media import MediaError

    def explode(_path):
        raise MediaError("ffmpeg could not read broken.mp3: Invalid data")

    monkeypatch.setattr("aloud.media.prepare", explode)
    watcher = Recorder()
    app.add_observer(watcher)

    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio")
    app.transcribe_file(broken)
    assert app._jobs.qsize() == 1, "queued; the conversion has not run yet"
    app._drain_one_for_test(app._jobs.get())
    assert watcher.errors and "Invalid data" in watcher.errors[0][1]


def test_a_missing_file_is_refused_before_queueing(app, tmp_path):
    watcher = Recorder()
    app.add_observer(watcher)
    app.transcribe_file(tmp_path / "nowhere.mp3")
    assert app._jobs.qsize() == 0
    assert watcher.errors and "No such file" in watcher.errors[0][1]


# -- the states that used to leave the microphone hot -----------------------


def test_error_recovery_does_not_clobber_a_new_recording(app):
    """The 3s ERROR->IDLE timer must stand down if dictation resumed.

    It used to set IDLE unconditionally. Press the hotkey inside the window
    and the timer fired over RECORDING; releasing then found the wrong state
    and never stopped the recorder — a hot microphone until quit.
    """
    app._fail("boom", "it broke")
    assert app.state is State.ERROR

    app.begin_recording()
    assert app.state is State.RECORDING

    app._recover_from_error()  # the timer firing, without the 3s wait
    assert app.state is State.RECORDING, "recovery must only clear ERROR"

    app.finish_recording()
    assert not app.recorder.recording, "the recorder must actually stop"
    assert app._jobs.qsize() == 1


def test_error_recovery_still_clears_a_stale_error(app):
    app._fail("boom", "it broke")
    app._recover_from_error()
    assert app.state is State.IDLE


def test_importing_a_file_while_recording_is_refused_without_breaking_it(app, tmp_path):
    """The refusal must not change state: ERROR would orphan the recorder too."""
    audio = _silent_wav(tmp_path / "clip.wav")
    watcher = Recorder()
    app.add_observer(watcher)

    app.begin_recording()
    app.transcribe_file(audio)

    assert app.state is State.RECORDING, "the dictation in progress survives"
    assert app._jobs.qsize() == 0, "the file was not queued"
    assert watcher.errors and "recording" in watcher.errors[0][1]

    app.finish_recording()
    assert not app.recorder.recording
    assert app._jobs.qsize() == 1, "the dictation still went through"


def test_file_conversion_runs_on_the_worker_not_the_caller(app, tmp_path, monkeypatch):
    """transcribe_file() is called from the main thread; ffmpeg is not quick."""
    calls = []

    def fake_prepare(path):
        calls.append(path)
        from aloud.media import Prepared
        wav = _silent_wav(tmp_path / "prepared.wav")
        return Prepared(path=wav, temporary=False, converted=True, original=path)

    monkeypatch.setattr("aloud.media.prepare", fake_prepare)
    source = tmp_path / "talk.mp3"
    source.write_bytes(b"fake")

    app.transcribe_file(source)
    assert calls == [], "queueing must not convert"
    app._drain_one_for_test(app._jobs.get())
    assert calls == [source], "the worker converts"


# -- granting Accessibility while the app runs ------------------------------


def test_the_hotkey_is_rebuilt_when_accessibility_arrives(app, monkeypatch):
    """The tap must exist before the grant does, and a tap built untrusted is
    deaf to other apps for the life of the process. Granting used to require
    quitting and reopening -- something to know rather than to notice."""
    from aloud import permissions

    trusted = {"value": False}
    monkeypatch.setattr(
        permissions, "accessibility_trusted", lambda prompt=False: trusted["value"]
    )
    app.ACCESSIBILITY_POLL_SECONDS = 0.01

    class Watcher:
        def __init__(self):
            self.granted = 0

        def on_accessibility_granted(self):
            self.granted += 1

    watcher = Watcher()
    app.add_observer(watcher)
    app._watch_for_accessibility()

    import time as _time

    trusted["value"] = True
    for _ in range(200):
        if watcher.granted:
            break
        _time.sleep(0.01)

    assert watcher.granted == 1, "the grant arriving must rebuild the hotkey"
    app.shutdown()


def test_no_watcher_runs_when_access_is_already_granted(app, monkeypatch):
    """Nothing to wait for; do not leave a thread polling forever."""
    import threading

    from aloud import permissions

    monkeypatch.setattr(permissions, "accessibility_trusted", lambda prompt=False: True)
    before = {t.name for t in threading.enumerate()}
    app._watch_for_accessibility()
    after = {t.name for t in threading.enumerate()}
    assert "aloud-permissions" not in (after - before)


def test_shutdown_stops_the_permission_watcher(app, monkeypatch):
    import threading
    import time as _time

    from aloud import permissions

    monkeypatch.setattr(permissions, "accessibility_trusted", lambda prompt=False: False)
    app.ACCESSIBILITY_POLL_SECONDS = 0.01
    app._watch_for_accessibility()
    app.shutdown()

    for _ in range(200):
        if not any(t.name == "aloud-permissions" for t in threading.enumerate()):
            return
        _time.sleep(0.01)
    raise AssertionError("the watcher thread outlived shutdown")


def test_the_app_reinstalls_the_hotkey_on_the_main_thread():
    """CFRunLoop sources attach to the thread that adds them."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "src" / "aloud" / "app.py"
    ).read_text(encoding="utf-8")
    node = next(
        n for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == "on_accessibility_granted"
    )
    body = ast.unparse(node)
    assert "run_on_main" in body
    assert "install_hotkey" in body


def test_start_arms_the_permission_watcher():
    """Testing the watcher directly proves nothing if start() never calls it."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent / "src" / "aloud" / "core.py"
    ).read_text(encoding="utf-8")
    node = next(
        n for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == "start"
    )
    assert "_watch_for_accessibility" in ast.unparse(node)
