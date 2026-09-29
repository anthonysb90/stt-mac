"""AssemblyAI — asynchronous cloud transcription with speaker labels.

Built for recordings rather than dictation. The flow is three calls: upload the
audio, ask for a transcript of it, then poll until it is ready. That makes it a
poor dictation engine (every dictation pays for a round trip and a queue) and a
good file engine: no size limit worth worrying about, language detection, and
speaker labels that are included in the price rather than sold as an add-on.

While it waits the window stays indeterminate — AssemblyAI reports "queued" and
"processing", not a percentage — but Cancel works, because each poll asks
whether to carry on.

This uploads your audio, and it is never selected automatically.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .. import media
from .. import segments as seg
from ..secrets import describe_source, read_key
from ._http import common_explanation, request_json, upload_timeout
from .base import EngineError, Segment, Transcript, TranscriptionEngine

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.assemblyai.com/v2"
#: Seconds between status checks. Short enough that a two-minute file does not
#: sit finished for long; long enough not to hammer the API.
DEFAULT_POLL_SECONDS = 3.0


class AssemblyAIEngine(TranscriptionEngine):
    name = "assemblyai"
    label = "AssemblyAI (cloud)"
    needs_api_key = True
    api_key_env_default = "ASSEMBLYAI_API_KEY"
    supports_bias = True
    #: Punctuation and casing are applied server-side (format_text).
    handles_cleanup = True
    cloud = True
    #: Not a percentage, but a heartbeat: each poll reports in and can be
    #: told to stop, which is what makes Cancel work.
    supports_progress = True

    service = "AssemblyAI"

    # -- contract ----------------------------------------------------------

    def check(self) -> Tuple[bool, str]:
        if not self._api_key():
            return False, (
                "No AssemblyAI API key. Paste one under Credentials in Settings, "
                "or run `aloud key assemblyai`."
            )
        model = self.options.get("speech_model") or "default model"
        speakers = "speaker labels" if self.options.get("speaker_labels", True) else "no speaker labels"
        return True, f"{model} · {speakers} · key from {describe_source(self.api_key_env, self.name)}"

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

        started = time.monotonic()
        upload_url = self._upload(wav_path)
        job = self._post(
            "/transcript", json.dumps(self._request(upload_url, bias_terms)).encode()
        )
        job_id = job.get("id")
        if not job_id:
            raise EngineError("AssemblyAI accepted the upload but returned no transcript id.")

        deadline = time.monotonic() + self._max_wait(wav_path)
        poll = float(self.options.get("poll_seconds", DEFAULT_POLL_SECONDS))
        while True:
            result = self._get(f"/transcript/{job_id}")
            status = result.get("status")
            if status == "completed":
                break
            if status == "error":
                raise EngineError(f"AssemblyAI could not transcribe this: {result.get('error', 'unknown error')}")
            if on_progress is not None and not on_progress("", 0.0, 0.0):
                log.info("Stopped waiting for AssemblyAI transcript %s", job_id)
                return Transcript(text="", engine=self.name, duration=time.monotonic() - started)
            if time.monotonic() > deadline:
                raise EngineError(
                    f"AssemblyAI had not finished after {self._max_wait(wav_path) / 60:.0f} "
                    f"minutes (transcript {job_id}, status {status})."
                )
            time.sleep(poll)

        return Transcript(
            text=str(result.get("text") or "").strip(),
            engine=self.name,
            duration=time.monotonic() - started,
            language=str(result.get("language_code") or ""),
            meta={"id": job_id, "model": result.get("speech_model") or ""},
            segments=self._segments(result),
        )

    # -- request building --------------------------------------------------

    def _request(self, audio_url: str, bias_terms: Sequence[str]) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "audio_url": audio_url,
            "punctuate": True,
            "format_text": True,
            "speaker_labels": bool(self.options.get("speaker_labels", True)),
        }
        language = str(self.options.get("language", "") or "")
        if language:
            body["language_code"] = language
        else:
            body["language_detection"] = True
        model = self.options.get("speech_model")
        if model:
            body["speech_model"] = str(model)
        terms = [t.strip() for t in bias_terms if t.strip()]
        if terms:
            # Configurable because AssemblyAI has renamed this once already:
            # word_boost on the older models, keyterms_prompt on the newer.
            body[str(self.options.get("bias_field") or "word_boost")] = terms
        return body

    @staticmethod
    def _segments(result: Dict[str, Any]) -> List[Segment]:
        """Utterances when speakers were labelled, otherwise grouped words.

        AssemblyAI reports times in milliseconds.
        """
        utterances = result.get("utterances") or []
        if utterances:
            found = []
            for u in utterances:
                try:
                    found.append(Segment(
                        float(u["start"]) / 1000.0, float(u["end"]) / 1000.0,
                        str(u.get("text", "")).strip(), str(u.get("speaker") or ""),
                    ))
                except (KeyError, TypeError, ValueError):
                    continue
            # A long utterance is a paragraph, not a subtitle; exports split it.
            return [s for s in found if s.text]

        words = []
        for w in result.get("words") or []:
            try:
                words.append(seg.Word(
                    float(w["start"]) / 1000.0, float(w["end"]) / 1000.0,
                    str(w.get("text", "")), str(w.get("speaker") or ""),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return seg.from_words(words)

    # -- HTTP --------------------------------------------------------------

    def _upload(self, path: Path) -> str:
        upload = media.compressed(path, str(self.options.get("upload_format", "flac")))
        try:
            audio = upload.path.read_bytes()
        finally:
            upload.cleanup()
        reply = request_json(
            f"{self._base_url()}/upload",
            data=audio,
            method="POST",
            headers={"authorization": self._api_key(), "Content-Type": "application/octet-stream"},
            timeout=upload_timeout(float(self.options.get("timeout", 30)), len(audio)),
            explain=common_explanation(self.service, self.name),
            service=self.service,
        )
        url = reply.get("upload_url") if isinstance(reply, dict) else None
        if not url:
            raise EngineError("AssemblyAI did not return an upload URL.")
        return str(url)

    def _post(self, path: str, body: bytes) -> Dict[str, Any]:
        return request_json(
            f"{self._base_url()}{path}",
            data=body,
            method="POST",
            headers={"authorization": self._api_key(), "Content-Type": "application/json"},
            timeout=float(self.options.get("timeout", 30)),
            explain=common_explanation(self.service, self.name),
            service=self.service,
        )

    def _get(self, path: str) -> Dict[str, Any]:
        return request_json(
            f"{self._base_url()}{path}",
            headers={"authorization": self._api_key()},
            timeout=float(self.options.get("timeout", 30)),
            explain=common_explanation(self.service, self.name),
            service=self.service,
        )

    # -- options -----------------------------------------------------------

    def _max_wait(self, wav_path: Path) -> float:
        """How long to keep polling: generous, and longer for longer audio."""
        configured = self.options.get("max_wait_seconds")
        if configured:
            return float(configured)
        return 600.0 + 2.0 * media.duration_of(wav_path)

    def _base_url(self) -> str:
        return str(self.options.get("base_url") or DEFAULT_BASE_URL).rstrip("/")

    def _api_key(self) -> str:
        return read_key(self.api_key_env, self.name)
