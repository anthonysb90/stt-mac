"""ElevenLabs Scribe — cloud transcription with word timings and speakers.

One request, one answer: the file is uploaded and the transcript comes back in
the same response, with every word timed and, when ``diarize`` is on, labelled
with who said it. Strong on accents and on languages other than English, which
is the reason to offer it alongside the others.

No progress while it works — the reply arrives all at once — so the window
says so rather than showing a bar that never moves.

This uploads your audio, and it is never selected automatically.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .. import media
from .. import segments as seg
from ..media import mime_type
from ..secrets import describe_source, read_key
from ._http import common_explanation, multipart, request_json, upload_timeout
from .base import EngineError, Segment, Transcript, TranscriptionEngine

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL = "scribe_v1"


class ElevenLabsEngine(TranscriptionEngine):
    name = "elevenlabs"
    label = "ElevenLabs Scribe (cloud)"
    needs_api_key = True
    api_key_env_default = "ELEVENLABS_API_KEY"
    handles_cleanup = True
    cloud = True

    service = "ElevenLabs"

    def check(self) -> Tuple[bool, str]:
        if not self._api_key():
            return False, (
                "No ElevenLabs API key. Paste one under Credentials in Settings, "
                "or run `aloud key elevenlabs`."
            )
        speakers = "speaker labels" if self.options.get("diarize", True) else "no speaker labels"
        return True, f"{self._model()} · {speakers} · key from {describe_source(self.api_key_env, self.name)}"

    def transcribe(
        self,
        wav_path: Path,
        *,
        bias_terms: Sequence[str] = (),
        on_progress: Optional[Callable[[str, float, float], bool]] = None,
    ) -> Transcript:
        ok, detail = self.check()
        if not ok:
            raise EngineError(detail)

        upload = media.compressed(wav_path, str(self.options.get("upload_format", "flac")))
        try:
            body, content_type = multipart(self._fields(), "file", upload.path,
                                           mime_type(upload.path))
        finally:
            upload.cleanup()
        started = time.monotonic()
        payload = request_json(
            f"{self._base_url()}/speech-to-text",
            data=body,
            method="POST",
            headers={"xi-api-key": self._api_key(), "Content-Type": content_type},
            timeout=upload_timeout(float(self.options.get("timeout", 60)), len(body)),
            explain=common_explanation(self.service, self.name),
            service=self.service,
        )
        return Transcript(
            text=str(payload.get("text") or "").strip(),
            engine=self.name,
            duration=time.monotonic() - started,
            language=str(payload.get("language_code") or ""),
            meta={"model": self._model()},
            segments=self._segments(payload),
        )

    def _fields(self) -> List[Tuple[str, str]]:
        fields = [
            ("model_id", self._model()),
            ("timestamps_granularity", "word"),
            ("diarize", "true" if self.options.get("diarize", True) else "false"),
            # "(laughter)" and "(music)" in a transcript are noise for notes
            # and subtitles alike; off unless asked for.
            ("tag_audio_events", "true" if self.options.get("tag_audio_events") else "false"),
        ]
        language = str(self.options.get("language", "") or "")
        if language:
            fields.append(("language_code", language))
        return fields

    @staticmethod
    def _segments(payload: Dict[str, Any]) -> List[Segment]:
        words = []
        for w in payload.get("words") or []:
            if w.get("type", "word") != "word":
                continue  # spacing and audio events carry no speech
            try:
                words.append(seg.Word(
                    float(w["start"]), float(w["end"]),
                    str(w.get("text", "")), str(w.get("speaker_id") or ""),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return seg.from_words(words)

    def _model(self) -> str:
        return str(self.options.get("model") or DEFAULT_MODEL)

    def _base_url(self) -> str:
        return str(self.options.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    def _api_key(self) -> str:
        return read_key(self.api_key_env, self.name)
