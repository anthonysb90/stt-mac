"""Transcribing while you speak: pauses, commits, previews, and the session."""

import math
from aloud.core import State
import struct
import threading
import time

import pytest

from aloud import live
from aloud.engines.base import Segment, Transcript

RATE = live.SAMPLE_RATE


def tone(seconds, amplitude=8000):
    """Speech stand-in: a loud 220 Hz tone."""
    n = int(RATE * seconds)
    return b"".join(struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 220 * i / RATE)))
                    for i in range(n))


def quiet(seconds, amplitude=40):
    n = int(RATE * seconds)
    return b"".join(struct.pack("<h", amplitude if i % 2 else -amplitude) for i in range(n))


class FakeEngine:
    """Names each call by its audio length, so tests can see what was sent."""

    def __init__(self):
        self.calls = []

    def __call__(self, pcm):
        seconds = len(pcm) / (RATE * 2)
        self.calls.append(seconds)
        return f"phrase{len(self.calls)}", []


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(**kwargs):
    engine = FakeEngine()
    clock = Clock()
    return live.LiveTranscriber(engine, clock=clock, **kwargs), engine, clock


# -- pauses and commits ---------------------------------------------------------


def test_a_pause_commits_the_phrase_before_it():
    t, engine, _ = make(previews=False)
    t.feed(quiet(0.5) + tone(1.5) + quiet(1.0))
    assert t.step() is True
    assert t.text == "phrase1" and len(engine.calls) == 1
    # About the phrase plus padding, not the silence around it.
    assert 1.5 <= engine.calls[0] <= 2.3


def test_nothing_is_committed_mid_phrase():
    t, engine, _ = make(previews=False)
    t.feed(tone(1.0) + quiet(0.3))  # a breath, not a pause
    assert t.step() is False and engine.calls == []


def test_silence_is_never_sent_to_the_engine():
    """Whisper invents words over silence; the cure is not to send any."""
    t, engine, _ = make(previews=True)
    for _ in range(10):
        t.feed(quiet(1.0))
        t.step()
    t.finish()
    assert engine.calls == [] and t.text == ""


def test_several_phrases_commit_in_order_with_their_times():
    t, engine, _ = make(previews=False)
    for _ in range(3):
        t.feed(tone(1.0) + quiet(1.0))
        t.step()
    assert t.text == "phrase1 phrase2 phrase3"
    starts = [p.start for p in t.pieces]
    assert starts == sorted(starts) and starts[1] >= 1.0
    assert all(s.text for s in t.segments)


def test_a_long_phrase_with_no_pause_is_cut_anyway():
    t, engine, _ = make(previews=False, max_piece=5.0)
    t.feed(tone(6.0))
    assert t.step() is True
    assert engine.calls[0] <= 5.1


def test_finish_transcribes_only_what_is_left():
    """The point: after you stop, only the last phrase remains to do."""
    t, engine, _ = make(previews=False)
    t.feed(tone(1.0) + quiet(1.0))
    t.step()
    t.feed(tone(0.8))
    t.finish()
    assert t.text == "phrase1 phrase2"
    assert engine.calls[1] < 1.5


# -- previews -----------------------------------------------------------------------


def test_the_phrase_in_progress_is_previewed_about_once_a_second():
    t, engine, clock = make(previews=True, preview_every=1.0)
    t.feed(tone(0.6))
    clock.now = 1.0
    assert t.step() is True and t.preview == "phrase1"
    t.feed(tone(0.3))
    clock.now = 1.5
    assert t.step() is False, "too soon for another preview"
    clock.now = 2.1
    t.step()
    assert len(engine.calls) == 2


def test_committing_clears_the_preview():
    t, _, clock = make(previews=True)
    t.feed(tone(0.6))
    clock.now = 1.0
    t.step()
    t.feed(quiet(1.0))
    t.step()
    assert t.preview == "" and t.pieces


def test_segments_from_the_engine_are_moved_to_session_time():
    calls = []

    def engine(pcm):
        calls.append(pcm)
        return "hello", [Segment(0.1, 0.5, "hello")]

    t = live.LiveTranscriber(engine, previews=False)
    t.feed(quiet(2.0) + tone(1.0) + quiet(1.0))
    for _ in range(3):
        t.step()
    assert t.segments[0].start == pytest.approx(0.1 + t.pieces[0].start)
    assert t.pieces[0].start > 1.0


# -- the session ------------------------------------------------------------------


class FakeRecorder:
    def __init__(self, audio):
        self.blocks = [audio[i:i + 3200] for i in range(0, len(audio), 3200)]
        self.fed = 0
        self.recording = False
        self.level = 0.0

    def start(self):
        self.recording = True

    def read_new(self, cursor):
        # Release audio gradually, as a microphone would.
        self.fed = min(len(self.blocks), self.fed + 20)
        return b"".join(self.blocks[cursor:self.fed]), self.fed

    def halt(self):
        self.recording = False

    def discard(self):
        self.blocks = []


class SessionEngine:
    name = "fake"
    label = "Fake"
    cloud = False
    supports_bias = False
    handles_cleanup = False

    def check(self):
        return True, "ready"

    def transcribe(self, path, bias_terms=(), on_progress=None):
        return Transcript(text="um, praise god", engine="fake")


@pytest.fixture
def controller(app):  # noqa: F811
    app.engine = SessionEngine()
    return app


from test_core_pipeline import app  # noqa: E402,F401  (fixture)


def test_a_session_streams_updates_then_a_cleaned_result(controller):
    controller.config.set("quick_dictate.previews", False)
    updates, finished = [], threading.Event()
    result = {}
    session = live.LiveSession(
        controller,
        on_update=lambda committed, preview: updates.append(committed),
        on_finished=lambda r: (result.update(r=r), finished.set()),
        recorder=FakeRecorder(tone(1.0) + quiet(1.0) + tone(1.0) + quiet(1.0)),
    )
    session.TICK = 0.01
    session.start()
    deadline = time.monotonic() + 5
    while len(updates) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    session.stop()
    assert finished.wait(5)
    assert updates and updates[0] == "Praise god"  # filler gone, capitalised
    r = result["r"]
    assert r.text.lower().count("praise god") == 2 and "um" not in r.text.lower()
    assert r.raw.startswith("um")
    assert r.seconds == pytest.approx(4.0, abs=0.1)


def test_a_cancelled_session_reports_nothing(controller):
    finished = threading.Event()
    session = live.LiveSession(controller, on_finished=lambda r: finished.set(),
                               recorder=FakeRecorder(tone(3.0)))
    session.TICK = 0.01
    session.start()
    session.cancel()
    session._thread.join(5)
    assert not finished.is_set()


def test_a_session_will_not_start_on_an_engine_that_is_not_ready(controller):
    class Broken(SessionEngine):
        def check(self):
            return False, "No API key"

    controller.engine = Broken()
    with pytest.raises(RuntimeError, match="No API key"):
        live.LiveSession(controller, recorder=FakeRecorder(b"")).start()


def test_cloud_engines_get_no_previews_by_default(controller):
    class Cloud(SessionEngine):
        cloud = True

    controller.engine = Cloud()
    assert live.LiveSession(controller, recorder=FakeRecorder(b"")).transcriber.previews is False


# -- the hotkey ---------------------------------------------------------------------


def _tap(app, tmp_path, name):
    from test_core_pipeline import _silent_wav

    app.recorder.wav_path = _silent_wav(tmp_path / name, seconds=0.1)
    app.begin_recording()
    app.finish_recording()


def test_two_quick_taps_open_quick_dictate(app, tmp_path):
    opened = []
    app.add_observer(type("O", (), {"on_quick_dictate_requested": lambda _s: opened.append(1)})())
    _tap(app, tmp_path, "a.wav")
    assert opened == []
    _tap(app, tmp_path, "b.wav")
    assert opened == [1]
    _tap(app, tmp_path, "c.wav")
    assert opened == [1], "a third tap starts a new pair"


def test_taps_far_apart_do_not_open_it(app, tmp_path, monkeypatch):
    opened = []
    app.add_observer(type("O", (), {"on_quick_dictate_requested": lambda _s: opened.append(1)})())
    clock = [100.0]
    monkeypatch.setattr("aloud.core.time.monotonic", lambda: clock[0])
    _tap(app, tmp_path, "a.wav")
    clock[0] += 2.0
    _tap(app, tmp_path, "b.wav")
    assert opened == []


def test_double_tap_can_be_turned_off(app, tmp_path):
    app.config.set("quick_dictate.double_tap", False)
    opened = []
    app.add_observer(type("O", (), {"on_quick_dictate_requested": lambda _s: opened.append(1)})())
    _tap(app, tmp_path, "a.wav")
    _tap(app, tmp_path, "b.wav")
    assert opened == []


def test_the_hotkey_stops_a_listening_quick_dictate(app):
    class Session:
        active = True
        stopped = False

        def stop(self):
            Session.stopped = True

    app.live = Session()
    app.begin_recording()
    assert Session.stopped and not app.recorder.recording


def test_a_chord_discards_the_recording_and_breaks_a_double_tap(app, tmp_path):
    opened = []
    app.add_observer(type("O", (), {"on_quick_dictate_requested": lambda _s: opened.append(1)})())
    _tap(app, tmp_path, "a.wav")
    app.begin_recording()
    app.discard_chord()          # Option+E: typing, not dictating
    assert app.state is State.IDLE and not app.recorder.recording
    _tap(app, tmp_path, "b.wav")
    assert opened == [], "the chord broke the pair"


# -- hotkey dictation, transcribed while the key is held -----------------------------


class StreamingRecorder:
    """A recorder the live path can read while it records."""

    def __init__(self, audio):
        self.audio = audio
        self.blocks = [audio[i:i + 3200] for i in range(0, len(audio), 3200)]
        self.recording = False
        self.level = 0.0
        self.given = 0

    def start(self):
        self.recording = True
        self.given = 0

    def read_new(self, cursor):
        self.given = min(len(self.blocks), self.given + 30)
        return b"".join(self.blocks[cursor:self.given]), self.given

    def stop_with_pcm(self, tmp=None):
        import tempfile
        import wave

        self.recording = False
        handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        handle.close()
        with wave.open(handle.name, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(self.audio)
        from pathlib import Path

        return Path(handle.name), self.audio

    def stop(self):
        return self.stop_with_pcm()[0]

    def cancel(self):
        self.recording = False


class PhraseEngine(SessionEngine):
    """Says which call it is, so the test can see phrases vs the whole file."""

    def __init__(self):
        self.calls = 0

    def transcribe(self, path, bias_terms=(), on_progress=None):
        self.calls += 1
        return Transcript(text=f"phrase{self.calls}.", engine="fake")


def _dictate(app, audio, hold=0.4):
    app.recorder = StreamingRecorder(audio)
    app.begin_recording()
    time.sleep(hold)  # the key is held; the stream works meanwhile
    app.finish_recording()
    job = app._jobs.get_nowait()
    seen = []
    app.add_observer(type("L", (), {"on_result": lambda _s, d: seen.append(d)})())
    app._run_job(job)
    return job, seen[-1]


def test_hotkey_dictation_is_transcribed_phrase_by_phrase(app):
    app.engine = PhraseEngine()
    job, dictation = _dictate(app, tone(1.0) + quiet(1.0) + tone(1.0) + quiet(0.3))
    assert job.live is not None
    assert dictation.text.lower() == "phrase1. phrase2."
    assert app.engine.calls == 2, "two phrases, and the whole WAV never sent"
    assert app.injector.delivered[-1].startswith("Phrase1. phrase2.")


def test_live_dictation_falls_back_to_the_whole_recording(app, monkeypatch):
    app.engine = PhraseEngine()
    monkeypatch.setattr("aloud.live.DictationStream.finish", lambda self, pcm, timeout=0: None)
    _job, dictation = _dictate(app, tone(1.0) + quiet(1.0))
    assert dictation.text  # from the WAV, transcribed the old way


def test_live_dictation_can_be_turned_off(app):
    app.config.set("dictation.live", False)
    app.engine = PhraseEngine()
    job, _ = _dictate(app, tone(1.0))
    assert job.live is None


def test_cancelling_stops_the_live_stream(app):
    app.engine = PhraseEngine()
    app.recorder = StreamingRecorder(tone(2.0))
    app.begin_recording()
    stream = app._stream
    app.cancel_recording()
    stream._thread.join(2)
    assert not stream._thread.is_alive() and app._stream is None


def test_phrases_are_found_even_when_the_audio_arrives_all_at_once():
    """A transcriber that fell behind must still split at every pause."""
    t, engine, _ = make(previews=False)
    t.feed(tone(1.0) + quiet(1.0) + tone(1.0) + quiet(1.0) + tone(0.5))
    while t.step():
        pass
    assert t.text == "phrase1 phrase2" and len(engine.calls) == 2
    t.finish()
    assert t.text == "phrase1 phrase2 phrase3"
