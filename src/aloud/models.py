"""The local models you can download, and downloading them.

Until now a model was a string typed into Settings and fetched silently the
first time it was used — a 2.4 GB download with no progress, triggered by a
dictation. This module is the catalog behind the Models window instead: what
exists for each local engine, how big it is, what it is for, whether it is on
disk, and a downloader with real progress and a Cancel button.

Downloads go into Aloud's own models folder rather than the Hugging Face cache,
as plain directories an engine can load by path. That is what makes progress
exact (we stream the bytes ourselves), Cancel clean (the partial download is a
folder we own), and Delete honest (it removes exactly what was downloaded).
Weights that are already in the Hugging Face cache — from before this existed,
or from `aloud warm` — are found and reported too, so nothing is fetched twice.

No AppKit here; the window is :mod:`aloud.ui.models_window`.
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import platform
import shutil
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .paths import MODELS_DIR

log = logging.getLogger(__name__)

HF_BASE = "https://huggingface.co"
USER_AGENT = "Aloud (model manager)"
#: Bytes read per step. Big enough to be fast, small enough that Cancel and
#: the progress bar both respond within a fraction of a second.
READ_SIZE = 1 << 20

PARAKEET = "parakeet_mlx"
FASTER_WHISPER = "faster_whisper"
WHISPER_CPP = "whisper_cpp"

#: faster-whisper downloads exactly these from a CTranslate2 repository.
_FW_FILES = ("config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*")
_PARAKEET_FILES = ("config.json", "model.safetensors")


@dataclass(frozen=True)
class Model:
    """One downloadable model."""

    engine: str
    #: What the engine's ``model`` setting names when using the shared cache:
    #: a size name for faster-whisper, a repo id for Parakeet, a file name for
    #: whisper.cpp.
    model_id: str
    title: str
    size_mb: int
    languages: str
    note: str
    repo: str
    #: fnmatch patterns for the files to fetch, or one exact file name.
    files: Tuple[str, ...]
    #: The file whose presence means "downloaded".
    main_file: str
    recommended: bool = False

    @property
    def key(self) -> str:
        return f"{self.engine}:{self.model_id}"

    @property
    def size_label(self) -> str:
        return f"{self.size_mb / 1000:.1f} GB" if self.size_mb >= 1000 else f"{self.size_mb} MB"

    @property
    def local_path(self) -> Path:
        """Where our own download of this model lives."""
        if self.engine == WHISPER_CPP:
            return MODELS_DIR / self.main_file
        return MODELS_DIR / self.engine / self.repo.replace("/", "--")


CATALOG: Tuple[Model, ...] = (
    # -- Parakeet (Apple Silicon) --------------------------------------------
    Model(PARAKEET, "mlx-community/parakeet-tdt-0.6b-v3", "Parakeet TDT v3", 2500,
          "25 European languages",
          "The Apple Silicon default. Fastest local option, and does not invent "
          "text over silence.",
          "mlx-community/parakeet-tdt-0.6b-v3", _PARAKEET_FILES, "model.safetensors",
          recommended=True),
    Model(PARAKEET, "mlx-community/parakeet-tdt-0.6b-v2", "Parakeet TDT v2", 2500,
          "English",
          "English only. Slightly more accurate than v3 on English speech.",
          "mlx-community/parakeet-tdt-0.6b-v2", _PARAKEET_FILES, "model.safetensors"),
    # -- faster-whisper (Intel default; also Apple Silicon) -------------------
    Model(FASTER_WHISPER, "tiny.en", "Whisper Tiny (English)", 75, "English",
          "Fastest and least accurate. For older Intel Macs.",
          "Systran/faster-whisper-tiny.en", _FW_FILES, "model.bin"),
    Model(FASTER_WHISPER, "base.en", "Whisper Base (English)", 145, "English",
          "The Intel default: quick, and good enough for clear dictation.",
          "Systran/faster-whisper-base.en", _FW_FILES, "model.bin", recommended=True),
    Model(FASTER_WHISPER, "small.en", "Whisper Small (English)", 485, "English",
          "Noticeably better on names and accents; about 3× slower than Base.",
          "Systran/faster-whisper-small.en", _FW_FILES, "model.bin"),
    Model(FASTER_WHISPER, "medium.en", "Whisper Medium (English)", 1530, "English",
          "High accuracy. Best kept for files, not live dictation, on a CPU.",
          "Systran/faster-whisper-medium.en", _FW_FILES, "model.bin"),
    Model(FASTER_WHISPER, "large-v3-turbo", "Whisper Large v3 Turbo", 1620, "99 languages",
          "Near Large v3 accuracy at a fraction of the cost. The best all-round "
          "model for transcribing files in any language.",
          "mobiuslabsgmbh/faster-whisper-large-v3-turbo", _FW_FILES, "model.bin"),
    Model(FASTER_WHISPER, "large-v3", "Whisper Large v3", 3090, "99 languages",
          "The most accurate Whisper. Slow on CPU: expect well under real time.",
          "Systran/faster-whisper-large-v3", _FW_FILES, "model.bin"),
    # -- whisper.cpp (offline fallback, both architectures) ------------------
    Model(WHISPER_CPP, "ggml-base.en.bin", "whisper.cpp Base (English)", 142, "English",
          "Small and quick. The usual fallback model.",
          "ggerganov/whisper.cpp", ("ggml-base.en.bin",), "ggml-base.en.bin", recommended=True),
    Model(WHISPER_CPP, "ggml-small.en.bin", "whisper.cpp Small (English)", 466, "English",
          "Better accuracy for a modest size.",
          "ggerganov/whisper.cpp", ("ggml-small.en.bin",), "ggml-small.en.bin"),
    Model(WHISPER_CPP, "ggml-large-v3-turbo-q5_0.bin", "whisper.cpp Large v3 Turbo (compressed)",
          547, "99 languages",
          "Large v3 Turbo, quantised to a third of the size with little loss.",
          "ggerganov/whisper.cpp", ("ggml-large-v3-turbo-q5_0.bin",),
          "ggml-large-v3-turbo-q5_0.bin"),
    Model(WHISPER_CPP, "ggml-large-v3-turbo.bin", "whisper.cpp Large v3 Turbo", 1620,
          "99 languages", "Full-precision Large v3 Turbo.",
          "ggerganov/whisper.cpp", ("ggml-large-v3-turbo.bin",), "ggml-large-v3-turbo.bin"),
)

ENGINE_TITLES = {
    PARAKEET: "Parakeet · MLX",
    FASTER_WHISPER: "faster-whisper",
    WHISPER_CPP: "whisper.cpp",
}


def find(key: str) -> Optional[Model]:
    return next((m for m in CATALOG if m.key == key), None)


def for_engine(engine: str) -> List[Model]:
    return [m for m in CATALOG if m.engine == engine]


# ---------------------------------------------------------------------------
# Which engines can run here
# ---------------------------------------------------------------------------


def engine_available(engine: str) -> Tuple[bool, str]:
    """Whether this Mac can run ``engine`` at all, and if not, why.

    Cheaper than building the engine and asking it: nothing is loaded, and a
    whisper.cpp with no model yet — exactly the case the Models window exists
    for — still counts as available.
    """
    import importlib.util

    if engine == PARAKEET:
        if not (sys.platform == "darwin" and platform.machine() == "arm64"):
            return False, "Needs Apple Silicon"
        if importlib.util.find_spec("parakeet_mlx") is None:
            return False, "parakeet-mlx is not installed — run scripts/bootstrap.sh"
        return True, ""
    if engine == FASTER_WHISPER:
        if importlib.util.find_spec("faster_whisper") is None:
            return False, "faster-whisper is not installed — run scripts/bootstrap.sh"
        return True, ""
    if engine == WHISPER_CPP:
        from .engines.whisper_cpp import find_binary

        if find_binary() is None:
            return False, "whisper-cli is not installed — run scripts/bootstrap.sh --with-whisper-cpp"
        return True, ""
    return False, "Unknown engine"


# ---------------------------------------------------------------------------
# What is on disk
# ---------------------------------------------------------------------------


@dataclass
class Status:
    installed: bool
    #: "aloud" for our own download, "cache" for the Hugging Face cache.
    where: str = ""
    path: Optional[Path] = None
    bytes_on_disk: int = 0

    @property
    def label(self) -> str:
        if not self.installed:
            return "Not downloaded"
        size = self.bytes_on_disk / 1e9
        amount = f"{size:.1f} GB" if size >= 1 else f"{self.bytes_on_disk / 1e6:.0f} MB"
        return f"Downloaded · {amount}" + (" (shared cache)" if self.where == "cache" else "")


def status(model: Model) -> Status:
    """Whether ``model`` is on disk, and where. Never raises, never networks."""
    own = model.local_path
    if model.engine == WHISPER_CPP:
        if own.is_file() and own.stat().st_size > 0:
            return Status(True, "aloud", own, own.stat().st_size)
        return Status(False)
    if (own / model.main_file).is_file():
        return Status(True, "aloud", own, _tree_size(own))

    cached = hf_cache_snapshot(model)
    if cached is not None:
        return Status(True, "cache", cached, _tree_size(cached, follow=True))
    return Status(False)


def hf_cache_root() -> Path:
    """Hugging Face's cache directory, honouring its environment variables."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def hf_repo_dir(repo: str) -> Path:
    return hf_cache_root() / f"models--{repo.replace('/', '--')}"


def hf_cache_snapshot(model: Model) -> Optional[Path]:
    """The cached snapshot holding ``model``'s weights, if there is one."""
    snapshots = hf_repo_dir(model.repo) / "snapshots"
    try:
        for snapshot in sorted(snapshots.iterdir()):
            if (snapshot / model.main_file).exists():
                return snapshot
    except OSError:
        pass
    return None


def _tree_size(path: Path, follow: bool = False) -> int:
    """Total size under ``path``. The cache's snapshot files are symlinks."""
    total = 0
    for root, _dirs, files in os.walk(path, followlinks=follow):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


# ---------------------------------------------------------------------------
# Which one is in use
# ---------------------------------------------------------------------------


def setting_for(model: Model) -> str:
    """The value to store in ``engines.<engine>.model`` to use ``model``.

    Our own download is used by path. A copy that only exists in the Hugging
    Face cache is used by name, which is how the engine already finds it.
    """
    state = status(model)
    if state.where == "aloud" and state.path is not None:
        return str(state.path)
    if model.engine == WHISPER_CPP:
        return str(model.local_path)
    return model.model_id


def in_use(model: Model, configured: str, default: str = "") -> bool:
    """Whether the engine's ``model`` setting points at ``model``."""
    value = (configured or default or "").strip()
    if not value:
        return False
    if value in (model.model_id, model.repo):
        return True
    try:
        return Path(value).expanduser().resolve() == model.local_path.resolve()
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------


class Cancelled(Exception):
    """The download was stopped on request. Nothing partial is left behind."""


class DownloadError(RuntimeError):
    pass


ProgressFn = Callable[[int, int], bool]


def remote_files(model: Model, timeout: float = 30.0) -> List[Tuple[str, int]]:
    """``(path, size)`` for each file to fetch, from the Hugging Face API.

    Asking rather than hard-coding sizes is what makes the progress bar exact,
    and the pattern match copes with repositories that name their vocabulary
    file differently.
    """
    if model.engine == WHISPER_CPP:
        exact = [(name, 0) for name in model.files]
        sizes = {name: size for name, size in _tree(model.repo, timeout)}
        return [(name, sizes.get(name, 0)) for name, _ in exact]

    wanted = []
    for path, size in _tree(model.repo, timeout):
        if any(fnmatch.fnmatch(path, pattern) for pattern in model.files):
            wanted.append((path, size))
    if not any(path == model.main_file for path, _ in wanted):
        raise DownloadError(f"{model.repo} has no {model.main_file} — the repository may have moved.")
    return wanted


def _tree(repo: str, timeout: float) -> List[Tuple[str, int]]:
    url = f"{HF_BASE}/api/models/{repo}/tree/main"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            listing = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise DownloadError(f"Hugging Face has no repository called {repo}.") from exc
        raise DownloadError(f"Hugging Face answered HTTP {exc.code} for {repo}.") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DownloadError(f"Could not reach Hugging Face: {exc}") from exc
    files = []
    for item in listing if isinstance(listing, list) else []:
        if item.get("type") != "file":
            continue
        size = item.get("size") or (item.get("lfs") or {}).get("size") or 0
        files.append((str(item.get("path", "")), int(size)))
    return files


def download(model: Model, on_progress: Optional[ProgressFn] = None,
             timeout: float = 60.0) -> Path:
    """Fetch ``model`` into Aloud's models folder. Returns where it landed.

    Everything is written under a ``.partial`` name first and renamed into
    place only once complete, so an interrupted download — Cancel, a dropped
    connection, a full disk, quitting — never leaves something that looks
    installed. ``on_progress(done, total)`` returning False cancels.
    """
    files = remote_files(model, timeout=timeout)
    total = sum(size for _path, size in files) or model.size_mb * 1_000_000
    target = model.local_path
    staging = target.with_name(target.name + ".partial")
    _remove(staging)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    done = 0
    try:
        for path, _size in files:
            destination = staging if model.engine == WHISPER_CPP else staging / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            done = _fetch(model.repo, path, destination, done, total, on_progress, timeout)
        _remove(target)
        staging.rename(target)
    except BaseException:
        _remove(staging)
        raise
    log.info("Downloaded %s to %s", model.title, target)
    return target


def _fetch(repo: str, path: str, destination: Path, done: int, total: int,
           on_progress: Optional[ProgressFn], timeout: float) -> int:
    url = f"{HF_BASE}/{repo}/resolve/main/{urllib.parse.quote(path)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, \
                destination.open("wb") as out:
            while True:
                block = response.read(READ_SIZE)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if on_progress is not None and not on_progress(done, total):
                    raise Cancelled()
    except Cancelled:
        raise
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"Downloading {path} failed: HTTP {exc.code}.") from exc
    except OSError as exc:
        if getattr(exc, "errno", None) == 28:
            raise DownloadError("The disk is full.") from exc
        raise DownloadError(f"Downloading {path} failed: {exc}") from exc
    return done


def delete(model: Model) -> bool:
    """Remove ``model`` from disk, wherever it is. Returns whether it was there.

    A copy in the Hugging Face cache is removed too: the person asked for the
    space back, and a model that reappears as "downloaded" after Delete would
    be a lie.
    """
    removed = False
    if model.local_path.exists():
        _remove(model.local_path)
        removed = True
    if model.engine != WHISPER_CPP and hf_cache_snapshot(model) is not None:
        _remove(hf_repo_dir(model.repo))
        removed = True
    return removed


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists() or path.is_symlink():
        path.unlink(missing_ok=True)


class Download:
    """A download running on its own thread, for the window to watch.

    The window polls ``fraction``, ``error`` and ``finished`` rather than being
    called back: AppKit must only be touched on the main thread, and polling
    from a timer there keeps this class free of any knowledge of that.
    """

    def __init__(self, model: Model, on_done: Optional[Callable[["Download"], None]] = None) -> None:
        self.model = model
        self.done_bytes = 0
        self.total_bytes = model.size_mb * 1_000_000
        self.error = ""
        self.finished = False
        self.cancelled = False
        self._stop = threading.Event()
        self._on_done = on_done
        self._thread = threading.Thread(target=self._run, name=f"aloud-download-{model.model_id}",
                                        daemon=True)

    def start(self) -> "Download":
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._stop.set()

    @property
    def fraction(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return max(0.0, min(self.done_bytes / float(self.total_bytes), 1.0))

    def _progress(self, done: int, total: int) -> bool:
        self.done_bytes, self.total_bytes = done, total
        return not self._stop.is_set()

    def _run(self) -> None:
        try:
            download(self.model, self._progress)
        except Cancelled:
            self.cancelled = True
        except DownloadError as exc:
            self.error = str(exc)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            log.exception("Download of %s failed", self.model.title)
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.finished = True
            if self._on_done is not None:
                try:
                    self._on_done(self)
                except Exception:
                    log.exception("Download completion handler failed")


# ---------------------------------------------------------------------------
# What a row in the Models window shows
# ---------------------------------------------------------------------------


@dataclass
class RowView:
    """Everything the window needs to draw one model's row.

    Decided here, not in the view, so the rules — no Delete while downloading,
    no Use before it is on disk — are tested rather than eyeballed.
    """

    status: str
    #: "download", "cancel", or "" when the button should be disabled.
    action: str
    action_title: str
    can_delete: bool
    can_use: bool
    in_use: bool
    progress: Optional[float]
    #: "error" / "success" / "" — which colour the status line takes.
    tone: str = ""


def row_view(model: Model, state: Status, job: Optional[Download],
             configured: str, available: bool, default: str = "") -> RowView:
    using = state.installed and in_use(model, configured, default)
    if job is not None and not job.finished:
        done_mb = job.done_bytes / 1e6
        total_mb = job.total_bytes / 1e6
        return RowView(
            status=f"Downloading · {done_mb:,.0f} of {total_mb:,.0f} MB",
            action="cancel", action_title="Cancel",
            can_delete=False, can_use=False, in_use=using, progress=job.fraction,
        )
    if not available:
        return RowView(state.label, "", "Download", state.installed, False, using, None)
    tone, status_text = "", state.label
    if job is not None and job.error:
        tone, status_text = "error", f"Download failed — {job.error}"
    elif using:
        tone, status_text = "success", f"In use · {state.label}"
    return RowView(
        status=status_text,
        action="" if state.installed else "download",
        action_title="Downloaded" if state.installed else "Download",
        can_delete=state.installed,
        can_use=state.installed and not using,
        in_use=using,
        progress=None,
        tone=tone,
    )
