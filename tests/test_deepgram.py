"""The Deepgram engine: request shaping, keyterm gating, and failure messages.

No network here — what is worth testing is the query we build and the way we
read the response, both of which are easy to get subtly wrong and impossible to
notice until a dictation comes back empty.
"""

import json
import urllib.error
import urllib.parse
from io import BytesIO

import pytest

from aloud.engines import build
from aloud.engines.base import EngineError
from aloud.engines.deepgram import DeepgramEngine


def params(engine, bias=()):
    """The query string the engine would send, as a dict of lists."""
    return urllib.parse.parse_qs(engine._query(bias), keep_blank_values=True)


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setattr("aloud.secrets.KEYS_DIR", tmp_path / "keys")
    from aloud.config import Config

    return build("deepgram", Config().engine_options("deepgram"))


# -- readiness ---------------------------------------------------------------


def test_a_missing_key_is_reported_not_raised(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setattr("aloud.secrets.KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr("aloud.secrets._read_keychain", lambda _n: "")
    ok, detail = DeepgramEngine({}).check()
    assert not ok
    assert "aloud key deepgram" in detail


def test_readiness_names_the_cleanup_and_where_the_key_came_from(engine):
    ok, detail = engine.check()
    assert ok
    assert "nova-3" in detail and "smart_format" in detail
    assert "$DEEPGRAM_API_KEY" in detail


def test_transcribe_refuses_without_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setattr("aloud.secrets.KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr("aloud.secrets._read_keychain", lambda _n: "")
    with pytest.raises(EngineError):
        DeepgramEngine({}).transcribe(tmp_path / "x.wav")


# -- cleanup parameters ------------------------------------------------------


def test_cleanup_flags_are_sent_as_explicit_booleans(engine):
    sent = params(engine)
    assert sent["smart_format"] == ["true"]
    assert sent["punctuate"] == ["true"]
    assert sent["numerals"] == ["true"]
    assert sent["dictation"] == ["true"]
    # Filler removal is expressed as filler_words=false, not by omission.
    assert sent["filler_words"] == ["false"]


def test_a_flag_left_out_of_the_config_is_left_out_of_the_request():
    engine = DeepgramEngine({"model": "nova-3", "smart_format": True})
    sent = params(engine)
    assert "punctuate" not in sent and "profanity_filter" not in sent


def test_the_model_and_language_are_always_sent(engine):
    sent = params(engine)
    assert sent["model"] == ["nova-3"] and sent["language"] == ["en"]


# -- keyterm prompting -------------------------------------------------------


def test_dictionary_terms_become_repeated_keyterm_parameters(engine):
    sent = params(engine, ["Anthropic", "Claude Code"])
    assert sent["keyterm"] == ["Anthropic", "Claude Code"]


def test_multi_word_terms_stay_one_keyterm(engine):
    query = engine._query(["Claude Code"])
    assert query.count("keyterm=") == 1


def test_blank_terms_are_dropped(engine):
    assert params(engine, ["  ", "", "Vercel"])["keyterm"] == ["Vercel"]


def test_keyterms_are_withheld_from_models_that_reject_them():
    """Deepgram 400s on keyterm with anything but Nova-3."""
    engine = DeepgramEngine({"model": "nova-2", "language": "en"})
    assert "keyterm" not in params(engine, ["Anthropic"])


def test_keyterms_are_withheld_from_the_multilingual_model():
    engine = DeepgramEngine({"model": "nova-3", "language": "multi"})
    assert "keyterm" not in params(engine, ["Anthropic"])


def test_the_dictionary_is_never_sent_as_deepgram_replace_pairs(engine):
    """Corrections stay local, where whole-word matching is guaranteed."""
    assert "replace" not in params(engine, ["Anthropic"])


# -- responses ---------------------------------------------------------------


def _payload(transcript, confidence=0.97, paragraphs=None):
    alternative = {"transcript": transcript, "confidence": confidence}
    if paragraphs is not None:
        alternative["paragraphs"] = {"transcript": paragraphs}
    return {"results": {"channels": [{"alternatives": [alternative]}]}}


def test_the_transcript_is_read_from_the_nested_result(engine):
    text, confidence = engine._extract(_payload("  Hello there.  "))
    assert text == "Hello there." and confidence == pytest.approx(0.97)


def test_paragraph_text_wins_because_it_keeps_the_line_breaks(engine):
    text, _ = engine._extract(_payload("one two", paragraphs="one\n\ntwo"))
    assert text == "one\n\ntwo"


@pytest.mark.parametrize("payload", [{}, {"results": {}}, {"results": {"channels": []}}])
def test_a_response_with_no_transcript_is_a_clear_error(engine, payload):
    with pytest.raises(EngineError, match="no transcript"):
        engine._extract(payload)


def test_a_full_request_round_trip(engine, monkeypatch, tmp_path):
    wav = tmp_path / "speech.wav"
    wav.write_bytes(b"RIFF....WAVE")
    seen = {}

    class FakeResponse(BytesIO):
        def __enter__(self): return self
        def __exit__(self, *_): return False

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["headers"] = request.headers
        seen["body"] = request.data
        return FakeResponse(json.dumps(_payload("we shipped Claude Code")).encode())

    monkeypatch.setattr("aloud.engines.deepgram.urllib.request.urlopen", fake_urlopen)
    transcript = engine.transcribe(wav, bias_terms=["Claude Code"])

    assert transcript.text == "we shipped Claude Code"
    assert transcript.engine == "deepgram"
    assert transcript.meta["keyterms"] == 1
    assert seen["body"] == b"RIFF....WAVE"
    assert seen["headers"]["Authorization"] == "Token test-key"
    assert seen["headers"]["Content-type"] == "audio/wav"
    assert "keyterm=Claude+Code" in seen["url"] or "keyterm=Claude%20Code" in seen["url"]


# -- failure messages --------------------------------------------------------


def _http_error(code, body=b""):
    return urllib.error.HTTPError("u", code, "reason", {}, BytesIO(body))


@pytest.mark.parametrize(
    "code,body,expected",
    [
        (401, b"", "API key"),
        (402, b"", "credit"),
        (429, b"", "rate-limiting"),
        (400, b"keyterm not supported", "Nova-3"),
        (500, b"boom", "HTTP 500"),
    ],
)
def test_http_failures_say_what_to_do_about_them(code, body, expected):
    assert expected in DeepgramEngine._explain(_http_error(code, body))
