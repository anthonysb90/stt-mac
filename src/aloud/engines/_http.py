"""The HTTP plumbing every cloud engine shares.

urllib rather than requests, so the app gains no HTTP dependency. Each engine
still explains its own errors — a 401 from Deepgram and a 401 from AssemblyAI
want different advice — so this only moves bytes and decodes JSON.
"""

from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple, Union

from .base import EngineError

Field = Tuple[str, str]


def multipart(
    fields: Union[Dict[str, str], Iterable[Field]],
    file_field: str,
    path: Path,
    content_type: str = "",
) -> Tuple[bytes, str]:
    """Encode a multipart/form-data body. Returns ``(body, content_type)``.

    ``fields`` may be a list of pairs, because some APIs take a repeated field
    (``timestamp_granularities[]``) and a dict cannot hold two of the same key.
    """
    pairs = fields.items() if isinstance(fields, dict) else fields
    boundary = f"----aloud{uuid.uuid4().hex}"
    parts: list = []
    for name, value in pairs:
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    kind = content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{path.name}"\r\n'
        f"Content-Type: {kind}\r\n\r\n".encode()
    )
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def upload_timeout(base: float, size_bytes: int) -> float:
    """A timeout that allows for the upload, not only the reply.

    A flat 30 seconds was tuned for a five-second dictation. An hour-long file
    is over 100 MB as 16 kHz WAV, and on an ordinary home uplink that alone
    takes minutes. Two seconds per megabyte on top of the configured value is
    generous for any real connection and still catches a hang.
    """
    return float(base) + 2.0 * (size_bytes / 1_000_000)


def request_json(
    url: str,
    *,
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    method: str = "GET",
    timeout: float = 30.0,
    explain=None,
    service: str = "the service",
) -> Any:
    """Send a request and decode the JSON reply.

    ``explain(code, body)`` turns an HTTP error into a sentence worth showing;
    without it the status and the start of the body are shown instead.
    """
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        message = explain(exc.code, body) if explain else None
        raise EngineError(message or f"HTTP {exc.code} from {service}: {body}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EngineError(f"Could not reach {service}: {exc}") from exc
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise EngineError(f"{service} sent back something that is not JSON") from exc


def common_explanation(service: str, key_name: str):
    """The advice every key-based service shares, for :func:`request_json`."""

    def explain(code: int, body: str) -> Optional[str]:
        if code == 401:
            return (
                f"{service} rejected the API key (401). Replace it under "
                f"Credentials in Settings, or re-run `aloud key {key_name}`."
            )
        if code == 402:
            return f"{service} reports no credit remaining (402)."
        if code == 413:
            return f"{service} refused the file as too large (413)."
        if code == 429:
            return f"{service} is rate-limiting this key (429). Try again shortly."
        return None

    return explain
