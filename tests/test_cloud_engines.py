"""The cloud engines, against a local server that plays each service.

No real network. What is checked is what these engines can get wrong without
anyone noticing until a real file fails: the request each one sends, how long
audio is split to fit an upload limit, how timings are read back and stitched
together, and what the error says when the service refuses.
"""

import json
import wave
from pathlib import Path

import pytest

from aloud import media
from aloud.engines import build
from aloud.engines.base import EngineError

from fakeserver import FakeServer


@pytest.fixture
def server(monkeypatch, tmp_path):
    # Bypass the sandbox's HTTPS proxy for the loopback server.
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setattr("aloud.secrets.KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr("aloud.secrets._read_keychain", lambda _n: "")
    fake = FakeServer().start()
    yield fake
    fake.stop()


def wav(path: Path, seconds: float) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


def form_fields(body: bytes) -> dict:
    """Pull the simple text fields out of a multipart body."""
    fields = {}
    for part in body.split(b"--")[1:]:
        if b'name="' not in part or b"filename=" in part:
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        fields.setdefault(name, []).append(value.rstrip(b"\r\n").decode())
    return fields


# -- chunking -----------------------------------------------------------------


def test_a_short_wav_is_one_chunk_pointing_at_the_original(tmp_path):
    original = wav(tmp_path / "a.wav", 5)
    chunks = media.split_wav(original, 600)
    assert len(chunks) == 1 and chunks[0].path == original
    assert chunks[0].duration == pytest.approx(5.0)


def test_a_long_wav_is_split_with_offsets(tmp_path):
    chunks = media.split_wav(wav(tmp_path / "a.wav", 25), 10)
    try:
        assert [c.offset for c in chunks] == [0.0, 10.0, 20.0]
        assert [round(c.duration, 3) for c in chunks] == [10.0, 10.0, 5.0]
        with wave.open(str(chunks[1].path), "rb") as handle:
            assert handle.getframerate() == 16000 and handle.getnchannels() == 1
    finally:
        for c in chunks:
            c.path.unlink(missing_ok=True)


# -- OpenAI / Groq ------------------------------------------------------------


def openai_reply(text, segments):
    return {"text": text, "language": "english",
            "segments": [{"start": a, "end": b, "text": t} for a, b, t in segments]}


def test_whisper_models_ask_for_timings_and_get_segments(server, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    server.routes[("POST", "/v1/audio/transcriptions")] = (200, openai_reply(
        "Hello there.", [(0.0, 1.5, " Hello there.")]))
    engine = build("openai", {"base_url": server.url + "/v1", "model": "whisper-1"})

    transcript = engine.transcribe(wav(tmp_path / "a.wav", 2))

    sent = form_fields(server.requests[0].body)
    assert sent["response_format"] == ["verbose_json"]
    assert sent["timestamp_granularities[]"] == ["segment"]
    assert server.requests[0].headers["authorization"] == "Bearer sk-test"
    assert transcript.text == "Hello there."
    assert [(s.start, s.end, s.text) for s in transcript.segments] == [(0.0, 1.5, "Hello there.")]


def test_gpt4o_models_are_not_asked_for_timings(server, monkeypatch, tmp_path):
    """They reject verbose_json with a 400; plain json is the only option."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    server.routes[("POST", "/v1/audio/transcriptions")] = (200, {"text": "Hi."})
    engine = build("openai", {"base_url": server.url + "/v1", "model": "gpt-4o-transcribe"})

    transcript = engine.transcribe(wav(tmp_path / "a.wav", 1))

    sent = form_fields(server.requests[0].body)
    assert sent["response_format"] == ["json"]
    assert "timestamp_granularities[]" not in sent
    assert transcript.text == "Hi." and transcript.segments == []


def test_long_audio_is_sent_in_chunks_and_stitched_back_together(server, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    replies = iter([
        openai_reply("First part.", [(0.0, 4.0, "First part.")]),
        openai_reply("Second part.", [(1.0, 3.0, "Second part.")]),
    ])
    server.routes[("POST", "/v1/audio/transcriptions")] = lambda _r: (200, next(replies))
    engine = build("openai", {"base_url": server.url + "/v1", "chunk_seconds": 10})
    progress = []

    transcript = engine.transcribe(
        wav(tmp_path / "a.wav", 15),
        on_progress=lambda text, done, total: progress.append((text, done, total)) or True,
    )

    assert len(server.requests) == 2
    assert transcript.text == "First part. Second part."
    # The second chunk's timings are moved back to where it sits in the file.
    assert [(s.start, s.end) for s in transcript.segments] == [(0.0, 4.0), (11.0, 13.0)]
    assert [round(p[1]) for p in progress] == [10, 15] and progress[-1][2] == pytest.approx(15)
    # The first chunk's words carry into the second as context.
    assert "First part." in form_fields(server.requests[1].body)["prompt"][0]
    # And no temporary chunk is left behind.
    assert not list(Path(media.tempfile.gettempdir()).glob("aloud-chunk-*"))


def test_cancelling_stops_before_the_next_chunk(server, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    server.routes[("POST", "/v1/audio/transcriptions")] = (200, openai_reply("Part.", []))
    engine = build("openai", {"base_url": server.url + "/v1", "chunk_seconds": 10})
    transcript = engine.transcribe(wav(tmp_path / "a.wav", 25), on_progress=lambda *_: False)
    assert len(server.requests) == 1
    assert transcript.text == "Part."


def test_groq_is_the_same_shape_with_its_own_defaults(server, monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    server.routes[("POST", "/openai/v1/audio/transcriptions")] = (200, openai_reply(
        "Hola.", [(0.0, 1.0, "Hola.")]))
    engine = build("groq", {"base_url": server.url + "/openai/v1"})

    transcript = engine.transcribe(wav(tmp_path / "a.wav", 1))

    sent = form_fields(server.requests[0].body)
    assert sent["model"] == ["whisper-large-v3-turbo"]
    assert sent["response_format"] == ["verbose_json"]
    assert "language" not in sent  # blank by default: Groq detects it
    assert server.requests[0].headers["authorization"] == "Bearer gsk-test"
    assert transcript.engine == "groq" and transcript.segments[0].text == "Hola."


def test_a_rejected_key_says_how_to_fix_it(server, monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "bad")
    server.routes[("POST", "/v1/audio/transcriptions")] = (401, {"error": "invalid"})
    engine = build("groq", {"base_url": server.url + "/v1"})
    with pytest.raises(EngineError, match="aloud key groq"):
        engine.transcribe(wav(tmp_path / "a.wav", 1))


# -- Deepgram -----------------------------------------------------------------


def test_deepgram_word_timings_become_segments_with_speakers(server, monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg")
    words = [
        {"word": "hello", "punctuated_word": "Hello.", "start": 0.0, "end": 0.4, "speaker": 0},
        {"word": "hi", "punctuated_word": "Hi.", "start": 0.6, "end": 0.9, "speaker": 1},
    ]
    server.routes[("POST", "/v1/listen")] = (200, {"results": {"channels": [{"alternatives": [
        {"transcript": "Hello. Hi.", "confidence": 0.9, "words": words}]}]}})
    engine = build("deepgram", {"base_url": server.url + "/v1", "diarize": True})

    transcript = engine.transcribe(wav(tmp_path / "a.wav", 1))

    assert "diarize=true" in server.requests[0].path
    assert [(s.text, s.speaker) for s in transcript.segments] == [("Hello.", "0"), ("Hi.", "1")]


# -- AssemblyAI ---------------------------------------------------------------


def assemblyai_routes(server, statuses, result):
    server.routes[("POST", "/v2/upload")] = (200, {"upload_url": "https://cdn/upload/1"})
    server.routes[("POST", "/v2/transcript")] = (200, {"id": "t1", "status": "queued"})
    replies = iter(statuses)

    def status(_request):
        state = next(replies, "completed")
        return (200, dict(result, status=state) if state == "completed" else {"status": state})

    server.routes[("GET", "/v2/transcript/t1")] = status


def test_assemblyai_uploads_requests_polls_and_reads_utterances(server, monkeypatch, tmp_path):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "aai")
    assemblyai_routes(server, ["queued", "processing"], {
        "text": "Welcome. Thank you.", "language_code": "en",
        "utterances": [
            {"speaker": "A", "start": 0, "end": 1500, "text": "Welcome."},
            {"speaker": "B", "start": 1800, "end": 2600, "text": "Thank you."},
        ],
    })
    engine = build("assemblyai", {"base_url": server.url + "/v2", "poll_seconds": 0})
    beats = []

    transcript = engine.transcribe(
        wav(tmp_path / "a.wav", 3), bias_terms=["Congregational Holiness"],
        on_progress=lambda *a: beats.append(a) or True,
    )

    upload, create = server.requests[0], server.requests[1]
    assert upload.headers["authorization"] == "aai"
    request = create.json()
    assert request["audio_url"] == "https://cdn/upload/1"
    assert request["speaker_labels"] is True
    assert request["language_detection"] is True and "language_code" not in request
    assert request["word_boost"] == ["Congregational Holiness"]
    assert len(beats) == 2  # one heartbeat per unfinished poll
    assert transcript.text == "Welcome. Thank you."
    assert [(s.start, s.end, s.speaker) for s in transcript.segments] == [
        (0.0, 1.5, "A"), (1.8, 2.6, "B")]


def test_assemblyai_falls_back_to_words_without_speaker_labels(server, monkeypatch, tmp_path):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "aai")
    assemblyai_routes(server, [], {"text": "One. Two.", "words": [
        {"text": "One.", "start": 0, "end": 400}, {"text": "Two.", "start": 500, "end": 900}]})
    engine = build("assemblyai", {"base_url": server.url + "/v2", "poll_seconds": 0,
                                  "speaker_labels": False, "language": "es"})
    transcript = engine.transcribe(wav(tmp_path / "a.wav", 1))
    assert server.requests[1].json()["language_code"] == "es"
    assert [s.text for s in transcript.segments] == ["One.", "Two."]


def test_assemblyai_can_be_cancelled_while_waiting(server, monkeypatch, tmp_path):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "aai")
    assemblyai_routes(server, ["processing"] * 50, {"text": "never"})
    engine = build("assemblyai", {"base_url": server.url + "/v2", "poll_seconds": 0})
    transcript = engine.transcribe(wav(tmp_path / "a.wav", 1), on_progress=lambda *_: False)
    assert transcript.text == ""
    assert sum(1 for r in server.requests if r.method == "GET") == 1


def test_assemblyai_reports_its_own_failure(server, monkeypatch, tmp_path):
    monkeypatch.setenv("ASSEMBLYAI_API_KEY", "aai")
    server.routes[("POST", "/v2/upload")] = (200, {"upload_url": "u"})
    server.routes[("POST", "/v2/transcript")] = (200, {"id": "t1"})
    server.routes[("GET", "/v2/transcript/t1")] = (200, {"status": "error", "error": "no audio"})
    engine = build("assemblyai", {"base_url": server.url + "/v2", "poll_seconds": 0})
    with pytest.raises(EngineError, match="no audio"):
        engine.transcribe(wav(tmp_path / "a.wav", 1))


# -- ElevenLabs ---------------------------------------------------------------


def test_elevenlabs_sends_its_fields_and_reads_words(server, monkeypatch, tmp_path):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi")
    server.routes[("POST", "/v1/speech-to-text")] = (200, {
        "language_code": "eng", "text": "Good morning. Amen.",
        "words": [
            {"text": "Good", "start": 0.0, "end": 0.3, "type": "word", "speaker_id": "speaker_0"},
            {"text": " ", "start": 0.3, "end": 0.35, "type": "spacing", "speaker_id": "speaker_0"},
            {"text": "morning.", "start": 0.35, "end": 0.8, "type": "word", "speaker_id": "speaker_0"},
            {"text": "(applause)", "start": 0.9, "end": 1.5, "type": "audio_event"},
            {"text": "Amen.", "start": 1.6, "end": 2.0, "type": "word", "speaker_id": "speaker_1"},
        ],
    })
    engine = build("elevenlabs", {"base_url": server.url + "/v1"})

    transcript = engine.transcribe(wav(tmp_path / "a.wav", 2))

    request = server.requests[0]
    assert request.headers["xi-api-key"] == "xi"
    sent = form_fields(request.body)
    assert sent["model_id"] == ["scribe_v1"]
    assert sent["diarize"] == ["true"] and sent["tag_audio_events"] == ["false"]
    assert transcript.language == "eng"
    assert [(s.text, s.speaker) for s in transcript.segments] == [
        ("Good morning.", "speaker_0"), ("Amen.", "speaker_1")]


# -- the registry -------------------------------------------------------------


@pytest.mark.parametrize("name", ["openai", "groq", "deepgram", "assemblyai", "elevenlabs"])
def test_every_cloud_engine_says_so(name):
    from aloud.engines import REGISTRY

    assert REGISTRY[name].cloud and REGISTRY[name].needs_api_key


@pytest.mark.parametrize("name", ["parakeet_mlx", "faster_whisper", "whisper_cpp", "mock"])
def test_local_engines_do_not_claim_to_upload(name):
    from aloud.engines import REGISTRY

    assert not REGISTRY[name].cloud


def test_cloud_engines_have_config_defaults():
    from aloud.config import DEFAULTS

    for name in ("groq", "assemblyai", "elevenlabs"):
        assert name in DEFAULTS["engines"], name
        assert DEFAULTS["engines"][name]["api_key_env"]
