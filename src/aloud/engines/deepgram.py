"""Deepgram — transcription and cleanup in one call.

Worth being precise about what "cleanup with Deepgram" can and cannot mean.
Deepgram has no text-in/cleaner-text-out endpoint: ``/v1/read`` (Text
Intelligence) *analyses* text — summary, topics, intents, sentiment — and
returns analysis, not a rewritten transcript. So there is no way to transcribe
locally with Parakeet and then send the text to Deepgram to be tidied.

What Deepgram does have is cleanup *at transcription time*, on ``/v1/listen``,
and it covers exactly the work this app was doing by hand afterwards:

===================  ====================================================
``smart_format``     punctuation, capitalisation, and entity formatting
                     for dates, times, money and phone numbers
``punctuate``        sentence punctuation on its own
``paragraphs``       paragraph breaks in long dictation
``filler_words``     set false to drop "uh" and "um"
``numerals``         "twenty twenty six" becomes 2026
``measurements``     spoken units become abbreviations
``dictation``        spoken "period" / "new line" become real punctuation
``profanity_filter`` masks recognised profanity
===================  ====================================================

Because that overlaps the local post-processing stage, this engine declares
``handles_cleanup``; the pipeline then skips the local steps that would
duplicate it rather than running both over the same text.

Biasing is the other reason to want Deepgram here. Nova-3's **keyterm
prompting** is a purpose-built vocabulary channel rather than a prompt prefix,
so the Dictionary's terms are passed as ``keyterm`` parameters and none of the
drift risk that comes with stuffing words into a Whisper prompt applies.

Deepgram's ``replace`` parameter is deliberately *not* used. It is a plain
find-and-replace with none of the whole-word or longest-match guarantees in
:mod:`aloud.corrections`, and handing it the Dictionary would reintroduce
precisely the "don't corrupt real words" problem the correction pass exists to
avoid. Corrections stay local, where they are tested.

This uploads your audio. That is the trade, and it is why the engine is never
selected automatically.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from ..media import mime_type
from ..secrets import describe_source, read_key
from .base import EngineError, Transcript, TranscriptionEngine

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepgram.com/v1"
DEFAULT_MODEL = "nova-3"

#: Boolean cleanup switches, in the order Deepgram documents them. Each maps
#: straight to a query parameter of the same name.
CLEANUP_FLAGS = (
    "smart_format",
    "punctuate",
    "paragraphs",
    "filler_words",
    "numerals",
    "measurements",
    "dictation",
    "profanity_filter",
)

#: Keyterm prompting is a Nova-3 feature, and the API rejects it outright when
#: combined with the multilingual model.
KEYTERM_MODELS = ("nova-3",)
KEYTERM_INCOMPATIBLE_LANGUAGES = ("multi",)


class DeepgramEngine(TranscriptionEngine):
    name = "deepgram"
    label = "Deepgram (cloud)"
    needs_api_key = True
    supports_bias = True
    #: Deepgram punctuates, capitalises and strips fillers server-side, so the
    #: local post-processing stage stands down for those steps.
    handles_cleanup = True

    # -- contract ----------------------------------------------------------

    def check(self) -> Tuple[bool, str]:
        if not self._api_key():
            return False, (
                "No Deepgram API key. Run `aloud key deepgram` to store one — "
                "a Dock-launched app cannot see your shell environment."
            )
        detail = f"{self._model()} · {self._cleanup_summary()} · key from {self.key_source()}"
        if not self._keyterms_supported():
            detail += " · keyterm prompting unavailable on this model"
        return True, detail

    def transcribe(self, wav_path: Path, *, bias_terms: Sequence[str] = ()) -> Transcript:
        ok, detail = self.check()
        if not ok:
            raise EngineError(detail)

        url = f"{self._base_url()}/listen?{self._query(bias_terms)}"
        request = urllib.request.Request(
            url,
            data=wav_path.read_bytes(),
            method="POST",
            headers={
                "Authorization": f"Token {self._api_key()}",
                # Dictations are always WAV, but an imported file can be
                # anything ffmpeg declined to convert.
                "Content-Type": mime_type(wav_path),
            },
        )

        started = time.monotonic()
        try:
            with urllib.request.urlopen(
                request, timeout=float(self.options.get("timeout", 30))
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise EngineError(self._explain(exc)) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise EngineError(f"Deepgram request failed: {exc}") from exc

        text, confidence = self._extract(payload)
        return Transcript(
            text=text,
            engine=self.name,
            duration=time.monotonic() - started,
            language=str(self.options.get("language", "")),
            meta={
                "model": self._model(),
                "confidence": confidence,
                "keyterms": len(self._keyterms(bias_terms)),
            },
        )

    # -- request building --------------------------------------------------

    def _query(self, bias_terms: Sequence[str]) -> str:
        pairs: List[Tuple[str, str]] = [("model", self._model())]

        language = str(self.options.get("language", "") or "")
        if language:
            pairs.append(("language", language))

        for flag in CLEANUP_FLAGS:
            if flag in self.options:
                pairs.append((flag, "true" if self.options[flag] else "false"))

        # Repeat the parameter once per term; Deepgram treats each occurrence
        # as its own keyterm and a multi-word phrase as a single unit.
        for keyterm in self._keyterms(bias_terms):
            pairs.append(("keyterm", keyterm))

        return urllib.parse.urlencode(pairs)

    def _keyterms(self, bias_terms: Sequence[str]) -> List[str]:
        if not self._keyterms_supported():
            return []
        return [term.strip() for term in bias_terms if term.strip()]

    def _keyterms_supported(self) -> bool:
        model = self._model()
        language = str(self.options.get("language", "") or "")
        return model.startswith(KEYTERM_MODELS) and language not in KEYTERM_INCOMPATIBLE_LANGUAGES

    # -- response handling -------------------------------------------------

    @staticmethod
    def _extract(payload: Dict[str, Any]) -> Tuple[str, float]:
        """Pull the transcript out of Deepgram's nested result shape."""
        try:
            alternative = payload["results"]["channels"][0]["alternatives"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise EngineError("Deepgram returned a response with no transcript in it.") from exc

        # With `paragraphs` on, the readable text lives under the paragraphs
        # object and the flat transcript loses the line breaks.
        paragraphs = alternative.get("paragraphs") or {}
        text = paragraphs.get("transcript") or alternative.get("transcript") or ""
        return str(text).strip(), float(alternative.get("confidence", 0.0) or 0.0)

    @staticmethod
    def _explain(exc: urllib.error.HTTPError) -> str:
        body = exc.read().decode("utf-8", "replace")[:400]
        if exc.code == 401:
            return "Deepgram rejected the API key (401). Re-run `aloud key deepgram`."
        if exc.code == 402:
            return "Deepgram reports no credit remaining (402)."
        if exc.code == 429:
            return "Deepgram is rate-limiting this key (429). Try again shortly."
        if exc.code == 400 and "keyterm" in body:
            return (
                "Deepgram rejected the keyterm parameters (400) — they need a "
                "Nova-3 model and a single language. Set engines.deepgram.model "
                "to nova-3, or turn off dictionary.bias.enabled."
            )
        return f"HTTP {exc.code} from Deepgram: {body}"

    # -- options -----------------------------------------------------------

    def _base_url(self) -> str:
        return str(self.options.get("base_url", DEFAULT_BASE_URL)).rstrip("/")

    def _model(self) -> str:
        return str(self.options.get("model") or DEFAULT_MODEL)

    def _api_key(self) -> str:
        return read_key(str(self.options.get("api_key_env", "DEEPGRAM_API_KEY")), self.name)

    def key_source(self) -> str:
        return describe_source(
            str(self.options.get("api_key_env", "DEEPGRAM_API_KEY")), self.name
        )

    def _cleanup_summary(self) -> str:
        """Which cleanup features are on, for the menu and `aloud doctor`."""
        on = [flag for flag in CLEANUP_FLAGS if self.options.get(flag)]
        if self.options.get("filler_words") is False:
            on.append("no fillers")
        return ", ".join(on) if on else "no cleanup"
