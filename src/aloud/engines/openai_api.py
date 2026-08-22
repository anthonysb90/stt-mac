"""Cloud transcription over an OpenAI-compatible /audio/transcriptions endpoint.

Kept as a first-class peer to the local engine for two reasons: it is the
fastest path on the Intel Mac, where whisper.cpp has no GPU to fall back on,
and it makes the engine boundary real rather than theoretical.

The request is built with urllib so the app gains no HTTP dependency. Audio is
uploaded, so this engine is off by default -- switching to it is an explicit
choice to send recordings to a third party.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Sequence, Tuple

from ..corrections import bias_prompt
from ..secrets import describe_source, read_key
from .base import EngineError, Transcript, TranscriptionEngine

log = logging.getLogger(__name__)


def _multipart(fields: dict[str, str], file_field: str, path: Path) -> Tuple[bytes, str]:
    """Encode a multipart/form-data body. Returns ``(body, content_type)``."""
    boundary = f"----aloud{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode()
    )
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class OpenAIEngine(TranscriptionEngine):
    name = "openai"
    label = "OpenAI API (cloud)"
    needs_api_key = True
    supports_bias = True

    # -- contract ----------------------------------------------------------

    def check(self) -> Tuple[bool, str]:
        env_var = str(self.options.get("api_key_env", "OPENAI_API_KEY"))
        if not self._api_key():
            return False, (
                f"No API key (${env_var} unset). Run `aloud key openai` to store "
                "one — a Dock-launched app cannot see your shell environment."
            )
        source = describe_source(env_var, self.name)
        return True, f"{self.options.get('model', 'whisper-1')} via {self._base_url()} · key from {source}"

    def transcribe(self, wav_path: Path, *, bias_terms: Sequence[str] = ()) -> Transcript:
        ok, detail = self.check()
        if not ok:
            raise EngineError(detail)

        fields = {
            "model": str(self.options.get("model", "whisper-1")),
            "response_format": "json",
        }
        language = self.options.get("language", "")
        if language:
            fields["language"] = language
        prompt = bias_prompt(bias_terms)
        if prompt:
            fields["prompt"] = prompt

        body, content_type = _multipart(fields, "file", wav_path)
        request = urllib.request.Request(
            f"{self._base_url()}/audio/transcriptions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": content_type,
            },
        )

        started = time.monotonic()
        try:
            with urllib.request.urlopen(
                request, timeout=float(self.options.get("timeout", 30))
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            raise EngineError(f"HTTP {exc.code} from the transcription API: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise EngineError(f"Transcription request failed: {exc}") from exc

        return Transcript(
            text=str(payload.get("text", "")).strip(),
            engine=self.name,
            duration=time.monotonic() - started,
            language=language,
            meta={"model": fields["model"]},
        )

    # -- internals ---------------------------------------------------------

    def _base_url(self) -> str:
        return str(self.options.get("base_url", "https://api.openai.com/v1")).rstrip("/")

    def _api_key(self) -> str:
        return read_key(str(self.options.get("api_key_env", "OPENAI_API_KEY")), self.name)
