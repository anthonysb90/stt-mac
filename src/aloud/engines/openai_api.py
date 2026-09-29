"""Cloud transcription over an OpenAI-compatible /audio/transcriptions endpoint.

Kept as a first-class peer to the local engine for two reasons: it is the
fastest path on the Intel Mac, where whisper.cpp has no GPU to fall back on,
and it makes the engine boundary real rather than theoretical.

Groq serves the same endpoint shape with Whisper on its own hardware — faster
and much cheaper — so :class:`GroqEngine` is this class with other defaults.

Two things matter for files that do not matter for dictation:

* **The 25 MB upload cap.** A 16 kHz mono WAV is ~1.9 MB a minute, so anything
  over ~13 minutes was refused. Long audio is sent in chunks (ten minutes by
  default), and each chunk's timestamps are shifted back into place. Chunks
  are also what make progress and Cancel possible on an engine that otherwise
  answers once, at the end.
* **Timestamps.** Whisper models return segment timings when asked for
  ``verbose_json``. The newer ``gpt-4o-*-transcribe`` models do not support
  that format, so for them the transcript comes back as plain text and the
  timed export formats are not offered.

The request is built with urllib so the app gains no HTTP dependency. Audio is
uploaded, so this engine is off by default -- switching to it is an explicit
choice to send recordings to a third party.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .. import media
from ..corrections import bias_prompt
from ..secrets import describe_source, read_key
from ._http import common_explanation, multipart, request_json, upload_timeout
from .base import EngineError, Segment, Transcript, TranscriptionEngine

log = logging.getLogger(__name__)

#: Ten minutes of 16 kHz mono WAV is ~19 MB — under the 25 MB cap with room.
DEFAULT_CHUNK_SECONDS = 600.0


class OpenAIEngine(TranscriptionEngine):
    name = "openai"
    label = "OpenAI API (cloud)"
    needs_api_key = True
    api_key_env_default = "OPENAI_API_KEY"
    supports_bias = True
    cloud = True
    #: Chunked uploads report after each chunk; see the module docstring.
    supports_progress = True

    default_base_url = "https://api.openai.com/v1"
    default_model = "whisper-1"
    service = "OpenAI"

    # -- contract ----------------------------------------------------------

    def check(self) -> Tuple[bool, str]:
        env_var = self.api_key_env
        if not self._api_key():
            return False, (
                f"No {self.service} API key (${env_var} unset). Paste one under "
                f"Credentials in Settings, or run `aloud key {self.name}` — a "
                "Dock-launched app cannot see your shell environment."
            )
        source = describe_source(env_var, self.name)
        return True, f"{self._model()} via {self._base_url()} · key from {source}"

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
        chunks = self._chunks(wav_path)
        total = sum(c.duration for c in chunks)
        language = str(self.options.get("language", "") or "")
        parallel = max(1, int(self.options.get("parallel_uploads", 3) or 1))
        try:
            if parallel > 1 and len(chunks) > 1:
                texts, segments, language, warning = self._send_parallel(
                    chunks, bias_terms, on_progress, total, language, parallel)
            else:
                texts, segments, language, warning = self._send_in_order(
                    chunks, bias_terms, on_progress, total, language)
        finally:
            for chunk in chunks:
                if chunk.path != wav_path:
                    chunk.path.unlink(missing_ok=True)

        return Transcript(
            text=" ".join(texts).strip(),
            engine=self.name,
            duration=time.monotonic() - started,
            language=language,
            meta={"model": self._model(), "chunks": len(chunks), "warning": warning},
            segments=segments,
        )

    # -- sending the pieces -------------------------------------------------

    def _send_in_order(self, chunks, bias_terms, on_progress, total, language):
        """One piece after another, each prompted with the end of the last."""
        texts: List[str] = []
        segments: List[Segment] = []
        warning = ""
        for index, chunk in enumerate(chunks):
            # The previous chunk's tail is the best prompt for this one: it
            # carries names and spelling across the cut.
            context = texts[-1][-200:] if texts else ""
            try:
                payload = self._send(chunk.path, bias_terms, context)
            except EngineError as exc:
                if not texts:
                    raise
                warning = self._partial_warning(chunk, total, exc)
                break
            self._collect(payload, chunk, texts, segments)
            language = language or str(payload.get("language", "") or "")
            if on_progress is not None and not on_progress(
                " ".join(texts), chunk.offset + chunk.duration, total
            ):
                log.info("Cancelled after chunk %d of %d", index + 1, len(chunks))
                break
        return texts, segments, language, warning

    def _send_parallel(self, chunks, bias_terms, on_progress, total, language, workers):
        """Several pieces at once; results kept in order.

        Uploading is most of the time on a long file, and the service is
        happy to work on several pieces together. The cost is the chained
        prompt -- each piece can no longer see the end of the one before --
        which is why ``parallel_uploads: 1`` restores the one-at-a-time path.
        """
        from concurrent.futures import ThreadPoolExecutor

        texts: List[str] = []
        segments: List[Segment] = []
        warning = ""
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aloud-upload") as pool:
            futures = [pool.submit(self._send, c.path, bias_terms, "") for c in chunks]
            try:
                for index, (chunk, future) in enumerate(zip(chunks, futures)):
                    try:
                        payload = future.result()
                    except EngineError as exc:
                        if not texts:
                            raise
                        warning = self._partial_warning(chunk, total, exc)
                        break
                    self._collect(payload, chunk, texts, segments)
                    language = language or str(payload.get("language", "") or "")
                    if on_progress is not None and not on_progress(
                        " ".join(texts), chunk.offset + chunk.duration, total
                    ):
                        log.info("Cancelled after chunk %d of %d", index + 1, len(chunks))
                        break
            finally:
                for future in futures:
                    future.cancel()  # anything not yet started
        return texts, segments, language, warning

    def _collect(self, payload, chunk, texts, segments) -> None:
        piece = str(payload.get("text", "")).strip()
        if piece:
            texts.append(piece)
        segments.extend(s.shifted(chunk.offset) for s in self._segments(payload))

    @staticmethod
    def _partial_warning(chunk, total, exc) -> str:
        """Keep what was already transcribed -- and paid for."""
        from ..export import clock

        warning = (
            f"Stopped at {clock(chunk.offset)} of {clock(total)}: {exc} "
            "The text covers everything before that point."
        )
        log.warning("%s", warning)
        return warning

    # -- the request -------------------------------------------------------

    def _send(self, path: Path, bias_terms: Sequence[str], context: str = "") -> dict:
        fields = [
            ("model", self._model()),
            ("response_format", "verbose_json" if self._timed() else "json"),
        ]
        if self._timed():
            fields.append(("timestamp_granularities[]", "segment"))
        language = self.options.get("language", "")
        if language:
            fields.append(("language", str(language)))
        prompt = " ".join(p for p in (bias_prompt(bias_terms), context) if p).strip()
        if prompt:
            fields.append(("prompt", prompt))

        upload = media.compressed(path, str(self.options.get("upload_format", "flac")))
        try:
            body, content_type = multipart(fields, "file", upload.path)
        finally:
            upload.cleanup()
        return request_json(
            f"{self._base_url()}/audio/transcriptions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key()}",
                "Content-Type": content_type,
            },
            timeout=upload_timeout(float(self.options.get("timeout", 30)), len(body)),
            explain=common_explanation(self.service, self.name),
            service=self.service,
        )

    def _timed(self) -> bool:
        """Whether to ask for segment timings.

        Only Whisper models accept ``verbose_json``; the gpt-4o transcribe
        models reject it with a 400. ``timestamps: false`` in the config turns
        it off for a compatible server that does not implement it.
        """
        setting = self.options.get("timestamps", "auto")
        if setting in (False, "false", "off"):
            return False
        if setting in (True, "true", "on"):
            return True
        return "whisper" in self._model().lower()

    @staticmethod
    def _segments(payload: dict) -> List[Segment]:
        found = []
        for item in payload.get("segments") or []:
            try:
                text = str(item.get("text", "")).strip()
                if text:
                    found.append(Segment(float(item["start"]), float(item["end"]), text))
            except (KeyError, TypeError, ValueError):
                continue
        return found

    def _chunks(self, wav_path: Path):
        """Pieces small enough to upload. One piece when the file already is."""
        seconds = float(self.options.get("chunk_seconds", DEFAULT_CHUNK_SECONDS) or 0)
        if seconds <= 0 or wav_path.suffix.lower() != ".wav":
            return [media.Chunk(path=wav_path, offset=0.0, duration=media.duration_of(wav_path))]
        try:
            return media.split_wav(wav_path, seconds)
        except Exception as exc:  # an unreadable WAV: let the API say why
            log.info("Could not split %s (%s); sending it whole", wav_path.name, exc)
            return [media.Chunk(path=wav_path, offset=0.0, duration=0.0)]

    # -- options -----------------------------------------------------------

    def _model(self) -> str:
        return str(self.options.get("model") or self.default_model)

    def _base_url(self) -> str:
        return str(self.options.get("base_url") or self.default_base_url).rstrip("/")

    def _api_key(self) -> str:
        return read_key(self.api_key_env, self.name)


class GroqEngine(OpenAIEngine):
    """Whisper on Groq's hardware, through the same OpenAI-shaped endpoint.

    Much faster than OpenAI's own Whisper and a fraction of the price, which
    makes it the natural cloud choice for long files. Every model it serves is
    a Whisper model, so timestamps are always available.
    """

    name = "groq"
    label = "Groq Whisper (cloud)"
    api_key_env_default = "GROQ_API_KEY"
    default_base_url = "https://api.groq.com/openai/v1"
    default_model = "whisper-large-v3-turbo"
    service = "Groq"
