"""Files through the pipeline: their own engine, and timings that survive it."""

from pathlib import Path

import pytest

from aloud import engines
from aloud.core import Job
from aloud.engines.base import Segment, Transcript, TranscriptionEngine

from test_core_pipeline import _silent_wav, app  # noqa: F401  (fixture)


class TimedEngine(TranscriptionEngine):
    """A file engine that reports segments, like the real ones do."""

    name = "timed"
    label = "Timed (test)"
    supports_progress = True
    instances = 0

    def __init__(self, options=None):
        super().__init__(options)
        TimedEngine.instances += 1
        self.calls = 0

    def check(self):
        return True, "ready"

    def transcribe(self, wav_path, *, bias_terms=(), on_progress=None):
        self.calls += 1
        if on_progress is not None:
            on_progress("um, welcome to cloud code", 5.0, 10.0)
        return Transcript(
            text="um, welcome to cloud code. new line Thanks.",
            engine=self.name,
            segments=[
                Segment(0.0, 4.0, "um, welcome to cloud code.", "A"),
                Segment(4.5, 6.0, "um", "B"),
                Segment(6.0, 7.0, "Thanks.", "B"),
            ],
        )


@pytest.fixture
def timed(monkeypatch):
    TimedEngine.instances = 0
    monkeypatch.setitem(engines.REGISTRY, TimedEngine.name, TimedEngine)
    return TimedEngine


def file_job(path: Path) -> Job:
    return Job(audio=path, deliver=False, source="file", label=path.name, prepare=False)


def results_of(app):
    seen = []
    app.add_observer(type("Listener", (), {"on_result": lambda _self, d: seen.append(d)})())
    return seen


# -- which engine -------------------------------------------------------------


def test_files_use_the_dictation_engine_by_default(app):
    assert app.file_engine_setting() == "same"
    assert app.files_engine() is app.engine


def test_naming_the_dictation_engine_reuses_it_rather_than_loading_twice(app):
    app.config.set("file_engine", app.engine.name)
    assert app.files_engine() is app.engine


def test_a_separate_file_engine_is_built_once_and_kept(app, timed):
    app.use_file_engine("timed")
    first = app.files_engine()
    assert isinstance(first, TimedEngine) and first is not app.engine
    assert app.files_engine() is first
    assert timed.instances == 1


def test_the_file_engine_is_not_built_until_a_file_needs_it(app, timed, monkeypatch):
    monkeypatch.setattr(app.config, "save", lambda: None)
    app.use_file_engine("timed")
    assert timed.instances == 0


def test_switching_the_dictation_engine_drops_the_cached_file_engine(app, timed, monkeypatch):
    monkeypatch.setattr("aloud.core.threading.Thread.start", lambda self: None)
    app.use_file_engine("timed")
    first = app.files_engine()
    app.use_engine("mock")
    assert app.files_engine() is not first


def test_dictation_still_uses_the_dictation_engine(app, timed, tmp_path):
    app.use_file_engine("timed")
    seen = results_of(app)
    app._drain_one_for_test(Job(audio=_silent_wav(tmp_path / "d.wav"), deliver=True))
    assert seen[-1].engine == "mock"
    assert app.injector.delivered  # typed into the focused app, as before


def test_a_file_goes_to_the_file_engine_and_is_never_typed(app, timed, tmp_path):
    app.use_file_engine("timed")
    seen = results_of(app)
    app._drain_one_for_test(file_job(_silent_wav(tmp_path / "talk.wav")))
    assert seen[-1].engine == "timed" and seen[-1].engine_label == "Timed (test)"
    assert app.files_engine().calls == 1
    assert app.injector.delivered == []


def test_progress_is_reported_from_the_file_engine(app, timed, tmp_path):
    app.use_file_engine("timed")
    progress = []
    app.add_observer(type("P", (), {
        "on_progress": lambda _s, job, text, done, total: progress.append((done, total))
    })())
    app._drain_one_for_test(file_job(_silent_wav(tmp_path / "talk.wav")))
    assert progress == [(5.0, 10.0)]


def test_an_unknown_file_engine_fails_the_file_not_the_app(app, tmp_path):
    app.config.set("file_engine", "no_such_engine")
    errors = []
    app.add_observer(type("E", (), {"on_job_failed": lambda _s, j, t, m: errors.append(m)})())
    app._drain_one_for_test(file_job(_silent_wav(tmp_path / "talk.wav")))
    assert errors and "no_such_engine" in errors[0]


# -- timings through the pipeline --------------------------------------------


def test_segments_get_the_same_corrections_as_the_text(app, timed, tmp_path):
    """A subtitle file must not say "cloud code" beside a transcript that doesn't."""
    app.dictionary.add_correction("cloud code", "Claude Code")
    app.reload_rules()
    app.use_file_engine("timed")
    seen = results_of(app)

    app._drain_one_for_test(file_job(_silent_wav(tmp_path / "talk.wav")))

    dictation = seen[-1]
    assert "Claude Code" in dictation.text
    assert dictation.segments[0].text == "Welcome to Claude Code."
    # Fillers are stripped per segment, and a segment left empty is dropped.
    assert [s.text for s in dictation.segments] == ["Welcome to Claude Code.", "Thanks."]
    assert [(s.start, s.end, s.speaker) for s in dictation.segments] == [
        (0.0, 4.0, "A"), (6.0, 7.0, "B")]


def test_an_engine_without_timings_gives_no_segments(app, tmp_path):
    seen = results_of(app)
    app._drain_one_for_test(file_job(_silent_wav(tmp_path / "talk.wav")))
    assert seen[-1].segments == []


# -- local engines report timings --------------------------------------------


def test_whisper_cpp_output_becomes_segments():
    from aloud.engines.whisper_cpp import clean_output, parse_segments

    raw = (
        "[00:00:00.000 --> 00:00:04.200]   Grace and peace to you.\n"
        "[00:00:04.200 --> 00:01:02.500]   [MUSIC]\n"
        "[01:00:00.000 --> 01:00:03.000]   Amen.\n"
    )
    assert [(s.start, s.end, s.text) for s in parse_segments(raw)] == [
        (0.0, 4.2, "Grace and peace to you."), (3600.0, 3603.0, "Amen.")]
    assert clean_output(raw) == "Grace and peace to you. Amen."


def test_whisper_cpp_keeps_timestamps_in_its_output(tmp_path):
    from aloud.engines.whisper_cpp import WhisperCppEngine

    engine = WhisperCppEngine({})
    engine.binary, engine.model = Path("/bin/whisper-cli"), Path("/m/ggml-base.en.bin")
    assert "--no-timestamps" not in engine._build_command(tmp_path / "a.wav")


def test_parakeet_sentences_become_segments():
    from types import SimpleNamespace as NS

    from aloud.engines.parakeet_mlx import ParakeetMLXEngine

    result = NS(text="One. Two.", sentences=[
        NS(text=" One.", start=0.0, end=0.8), NS(text="Two.", start=1.0, end=1.5),
        NS(text="bad", start=None, end=1.0),
    ])
    assert [(s.start, s.end, s.text) for s in ParakeetMLXEngine._sentences(result)] == [
        (0.0, 0.8, "One."), (1.0, 1.5, "Two.")]
    assert ParakeetMLXEngine._sentences("a bare string") == []
    assert ParakeetMLXEngine._sentences(None) == []


def test_faster_whisper_keeps_segment_timings(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    from aloud.engines.faster_whisper import FasterWhisperEngine

    class Model:
        def transcribe(self, *_a, **_k):
            parts = [NS(text=" Hello.", start=0.0, end=1.0), NS(text=" ", start=1.0, end=1.2),
                     NS(text=" Bye.", start=2.0, end=2.5)]
            return iter(parts), NS(duration=3.0, language="en")

    engine = FasterWhisperEngine({})
    monkeypatch.setattr(engine, "_load", lambda: Model())
    transcript = engine.transcribe(tmp_path / "a.wav")
    assert transcript.text == "Hello. Bye."
    assert [(s.start, s.end, s.text) for s in transcript.segments] == [
        (0.0, 1.0, "Hello."), (2.0, 2.5, "Bye.")]


# -- memory: long files, and replaced engines ----------------------------------


def test_parakeet_holds_samples_as_16_bit_not_python_floats(tmp_path):
    """An hour as Python floats was ~1.8 GB; as 16-bit samples it is ~115 MB."""
    import array

    from aloud.engines.parakeet_mlx import ParakeetMLXEngine

    samples, rate = ParakeetMLXEngine._read_wav(_silent_wav(tmp_path / "a.wav", seconds=2))
    assert isinstance(samples, array.array) and samples.typecode == "h"
    assert rate == 16000 and len(samples) == 32000
    chunks = list(ParakeetMLXEngine._chunks(samples, 16000))
    assert len(chunks) == 2 and len(chunks[0]) == 16000
    assert all(-1.0 <= float(v) <= 1.0 for v in list(chunks[0])[:10])


def test_parakeet_will_not_stream_audio_at_the_wrong_rate(tmp_path):
    from aloud.engines.parakeet_mlx import ParakeetMLXEngine, _StreamingUnavailable

    with pytest.raises(_StreamingUnavailable, match="16 kHz"):
        ParakeetMLXEngine._read_wav(_silent_wav(tmp_path / "a.wav", rate=44100))


def test_the_mlx_thread_lets_go_of_its_last_result():
    """It used to keep the model alive after warm-up, and so every old engine."""
    import gc
    import weakref

    from aloud.engines.parakeet_mlx import _MLXThread

    class Model:
        pass

    thread = _MLXThread()
    model = thread.call(Model)
    ref = weakref.ref(model)
    thread.call(lambda: None)  # the thread is now waiting for more work
    del model
    gc.collect()
    assert ref() is None
    thread.stop()
    thread._thread.join(2)
    assert not thread._thread.is_alive()


def test_replacing_the_engine_releases_the_old_one(app, monkeypatch):
    monkeypatch.setattr("aloud.core.threading.Thread.start", lambda self: None)
    closed = []
    old = app.engine
    monkeypatch.setattr(old, "close", lambda: closed.append(old), raising=False)
    app.use_engine("mock")
    assert closed == [old] and app.engine is not old


def test_replacing_the_file_engine_releases_it_but_not_the_dictation_engine(app, timed):
    app.use_file_engine("timed")
    file_engine = app.files_engine()
    closed = []
    file_engine.close = lambda: closed.append("file")
    app.engine.close = lambda: closed.append("dictation")
    app.reset_file_engine()
    assert closed == ["file"]
