"""Speaker labels on this Mac."""

import os

import pytest

from aloud import diarize
from aloud.core import Job
from aloud.engines.base import Segment, Transcript, TranscriptionEngine
from aloud import engines

from test_core_pipeline import _silent_wav, app  # noqa: F401  (fixture)


def test_each_segment_takes_the_speaker_it_overlaps_most():
    turns = [diarize.Turn(0, 5, "0"), diarize.Turn(5, 10, "1")]
    segments = [Segment(0.5, 4.0, "Welcome."), Segment(4.5, 9.0, "Thank you."),
                Segment(12.0, 13.0, "Amen.")]
    out = diarize.assign(segments, turns)
    assert [s.speaker for s in out] == ["0", "1", "1"]
    assert [s.text for s in out] == ["Welcome.", "Thank you.", "Amen."]


def test_no_turns_changes_nothing():
    segments = [Segment(0, 1, "x")]
    assert diarize.assign(segments, []) == segments


class Plain(TranscriptionEngine):
    name = "plain"
    label = "Plain"

    def check(self):
        return True, ""

    def transcribe(self, wav_path, *, bias_terms=(), on_progress=None):
        return Transcript(text="Hello there. Hi.", engine="plain",
                          segments=[Segment(0, 2, "Hello there."), Segment(6, 7, "Hi.")])


@pytest.fixture
def plain(app, monkeypatch):
    monkeypatch.setitem(engines.REGISTRY, "plain", Plain)
    app.config.set("file_engine", "plain")
    return app


def run(app, tmp_path):
    seen, stages = [], []
    app.add_observer(type("L", (), {
        "on_result": lambda _s, d: seen.append(d),
        "on_job_stage": lambda _s, j, m: stages.append(m),
    })())
    app._run_job(Job(audio=_silent_wav(tmp_path / "talk.wav"), deliver=False,
                     source="file", label="talk.wav"))
    return seen[-1], stages


def test_files_get_local_speaker_labels_when_set_up(plain, tmp_path, monkeypatch):
    monkeypatch.setattr(diarize, "ready", lambda: True)
    monkeypatch.setattr(diarize, "speaker_turns", lambda wav, speakers=0, threshold=0.5,
                        on_progress=None: (on_progress(0.5), [diarize.Turn(0, 3, "0"),
                                                               diarize.Turn(5, 8, "1")])[1])
    dictation, stages = run(plain, tmp_path)
    assert [s.speaker for s in dictation.segments] == ["0", "1"]
    assert stages[0] == "Identifying speakers…" and "50%" in stages[1]


def test_nothing_happens_until_it_is_set_up(plain, tmp_path, monkeypatch):
    monkeypatch.setattr(diarize, "ready", lambda: False)
    dictation, stages = run(plain, tmp_path)
    assert not any(s.speaker for s in dictation.segments) and stages == []


def test_a_failure_keeps_the_transcript(plain, tmp_path, monkeypatch):
    monkeypatch.setattr(diarize, "ready", lambda: True)
    monkeypatch.setattr(diarize, "speaker_turns", lambda *a, **k: 1 / 0)
    dictation, _ = run(plain, tmp_path)
    assert dictation.text and not any(s.speaker for s in dictation.segments)


def test_it_can_be_switched_off(plain, tmp_path, monkeypatch):
    plain.config.set("speakers.local", False)
    monkeypatch.setattr(diarize, "ready", lambda: True)
    monkeypatch.setattr(diarize, "speaker_turns", lambda *a, **k: 1 / 0)
    dictation, stages = run(plain, tmp_path)
    assert stages == []


@pytest.mark.skipif(not os.environ.get("ALOUD_SPEAKER_MODELS"),
                    reason="set ALOUD_SPEAKER_MODELS to a folder holding the real models")
def test_the_real_models_tell_two_voices_apart(tmp_path, monkeypatch):
    """Runs sherpa-onnx for real on two synthetic voices, 5 s each, twice."""
    import math
    import random
    import struct
    import wave
    from pathlib import Path

    monkeypatch.setattr(diarize, "folder", lambda: Path(os.environ["ALOUD_SPEAKER_MODELS"]))
    random.seed(1)
    samples = []
    for f0 in (110, 230, 110, 230):
        for i in range(16000 * 5):
            t = i / 16000
            v = sum(math.sin(2 * math.pi * f0 * k * t) / k for k in range(1, 8))
            samples.append(int(6000 * v * (0.5 + 0.5 * math.sin(6 * math.pi * t))
                               + random.gauss(0, 200)))
    path = tmp_path / "two.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"".join(struct.pack("<h", max(-32768, min(32767, s))) for s in samples))
    turns = diarize.speaker_turns(path, speakers=2)
    assert [round(t.start) for t in turns] == [0, 5, 10, 15]
    assert turns[0].speaker == turns[2].speaker != turns[1].speaker == turns[3].speaker
