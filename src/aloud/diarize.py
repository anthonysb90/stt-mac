"""Speaker labels on this Mac, for engines that do not give them.

Deepgram, AssemblyAI and ElevenLabs say who spoke when. Parakeet, Whisper and
whisper.cpp do not — so a sermon with a Q&A, or an interview, came back as one
voice unless it was sent to the cloud. This adds the labels locally, after
transcription, with sherpa-onnx's offline speaker diarization:

1. a *segmentation* model (pyannote 3.0) finds where each voice speaks;
2. an *embedding* model (CAM++, trained on VoxCeleb's many languages) turns
   each stretch into a voiceprint;
3. voiceprints are clustered into speakers — automatically, or into the number
   you say were there, which is more reliable when you know it.

Each transcript segment then takes the speaker it overlaps most. Nothing is
uploaded, and it runs on Intel and Apple Silicon alike (sherpa-onnx ships
native wheels for both), at several times real time on a CPU.

It is optional and set up on request (Models window → Speaker Detection): the
Python package is installed into Aloud's own environment and the two models
(about 35 MB) downloaded into the models folder. Until then nothing changes.
"""

from __future__ import annotations

import importlib.util
import logging
import subprocess
import tarfile
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .engines.base import Segment
from .paths import MODELS_DIR

log = logging.getLogger(__name__)

RELEASES = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
SEGMENTATION_URL = f"{RELEASES}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
EMBEDDING_URL = f"{RELEASES}/speaker-recongition-models/3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
PACKAGE = "sherpa-onnx"

#: Voices closer than this are the same speaker when the count is automatic.
#: Lower splits one speaker into several; higher merges different people.
DEFAULT_THRESHOLD = 0.5


def folder() -> Path:
    return MODELS_DIR / "speakers"


def segmentation_model() -> Path:
    return folder() / "segmentation.onnx"


def embedding_model() -> Path:
    return folder() / "embedding.onnx"


# ---------------------------------------------------------------------------
# Readiness and setup
# ---------------------------------------------------------------------------


def package_installed() -> bool:
    return importlib.util.find_spec("sherpa_onnx") is not None


def models_ready() -> bool:
    return segmentation_model().is_file() and embedding_model().is_file()


def ready() -> bool:
    return package_installed() and models_ready()


def status() -> str:
    """One line for the Models window."""
    if ready():
        return "Ready · labels speakers in files transcribed on this Mac"
    missing = []
    if not package_installed():
        missing.append("the sherpa-onnx package")
    if not models_ready():
        missing.append("two models (about 35 MB)")
    return "Not set up · needs " + " and ".join(missing)


def venv_python() -> Optional[Path]:
    """The Python of Aloud's own environment, where packages are installed."""
    from .updates import repo_root

    candidate = repo_root() / ".venv" / "bin" / "python"
    return candidate if candidate.exists() else None


def install_package(timeout: float = 600.0) -> Tuple[bool, str]:
    """pip-install sherpa-onnx into Aloud's environment. ``(ok, detail)``."""
    python = venv_python()
    if python is None:
        return False, "Could not find Aloud's Python environment (.venv) to install into."
    try:
        completed = subprocess.run(
            [str(python), "-m", "pip", "install", "--upgrade", PACKAGE],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not run pip: {exc}"
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout).strip().splitlines()[-3:]
        return False, "pip could not install sherpa-onnx:\n" + "\n".join(tail)
    importlib.invalidate_caches()
    return True, "Installed sherpa-onnx."


def _download(url: str, target: Path, on_progress, done: int, total: int) -> int:
    partial = target.with_name(target.name + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "Aloud"})
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            out.write(block)
            done += len(block)
            if on_progress is not None:
                on_progress(done, total)
    partial.replace(target)
    return done


def download_models(on_progress: Optional[Callable[[int, int], None]] = None) -> None:
    """Fetch both models into the models folder. Raises OSError on failure."""
    folder().mkdir(parents=True, exist_ok=True)
    total = 36_000_000  # approximate, for the bar
    done = 0
    if not segmentation_model().is_file():
        archive = folder() / "segmentation.tar.bz2"
        done = _download(SEGMENTATION_URL, archive, on_progress, done, total)
        try:
            with tarfile.open(archive, "r:bz2") as bundle:
                member = next((m for m in bundle.getmembers()
                               if m.isfile() and m.name.endswith("/model.onnx")), None)
                if member is None:
                    raise OSError("The segmentation download has no model.onnx in it.")
                source = bundle.extractfile(member)
                partial = segmentation_model().with_suffix(".partial")
                partial.write_bytes(source.read())
                partial.replace(segmentation_model())
        finally:
            archive.unlink(missing_ok=True)
    if not embedding_model().is_file():
        _download(EMBEDDING_URL, embedding_model(), on_progress, done, total)


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    start: float
    end: float
    speaker: str


def _samples(wav_path: Path):
    import numpy as np

    with wave.open(str(wav_path), "rb") as handle:
        if (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) != (16000, 1, 2):
            raise ValueError("expected 16 kHz mono 16-bit audio")
        frames = handle.readframes(handle.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def speaker_turns(wav_path: Path, speakers: int = 0, threshold: float = DEFAULT_THRESHOLD,
                  on_progress: Optional[Callable[[float], bool]] = None) -> List[Turn]:
    """Who spoke when. ``speakers`` 0 means "work it out".

    ``on_progress(fraction)`` returning False stops early (and returns []).
    """
    import sherpa_onnx

    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(segmentation_model()))),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding_model())),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=int(speakers) if speakers and speakers > 0 else -1,
            threshold=float(threshold)),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not config.validate():
        raise RuntimeError("The speaker models did not load; set them up again in Models.")
    engine = sherpa_onnx.OfflineSpeakerDiarization(config)
    stopped = [False]

    def callback(processed: int, chunks: int) -> int:
        if on_progress is not None and chunks:
            if not on_progress(processed / float(chunks)):
                stopped[0] = True
                return 1  # non-zero asks sherpa-onnx to stop
        return 0

    result = engine.process(_samples(wav_path), callback=callback)
    if stopped[0]:
        return []
    return [Turn(float(s.start), float(s.end), str(s.speaker))
            for s in result.sort_by_start_time()]


def assign(segments: Sequence[Segment], turns: Sequence[Turn]) -> List[Segment]:
    """Give each transcript segment the speaker it overlaps most.

    A segment no turn overlaps (a word in a gap the detector called silence)
    takes the speaker of the nearest turn, so every line has someone's name.
    """
    if not turns:
        return list(segments)
    out = []
    for segment in segments:
        best, best_overlap = None, 0.0
        for turn in turns:
            overlap = min(segment.end, turn.end) - max(segment.start, turn.start)
            if overlap > best_overlap:
                best, best_overlap = turn, overlap
        if best is None:
            middle = (segment.start + segment.end) / 2.0
            best = min(turns, key=lambda t: min(abs(t.start - middle), abs(t.end - middle)))
        out.append(Segment(segment.start, segment.end, segment.text, best.speaker))
    return out
