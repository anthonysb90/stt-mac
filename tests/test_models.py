"""The model catalog, the downloader, and the rules behind the Models window."""

from pathlib import Path

import pytest

from aloud import models
from aloud.engines import REGISTRY

from fakeserver import FakeServer

PARAKEET_V3 = "parakeet_mlx:mlx-community/parakeet-tdt-0.6b-v3"


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    cache = tmp_path / "hf"
    monkeypatch.setattr("aloud.models.MODELS_DIR", models_dir)
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    return models_dir, cache


@pytest.fixture
def hub(monkeypatch, dirs):
    """A local server shaped like Hugging Face's tree API and file URLs."""
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("NO_PROXY", "*")
    fake = FakeServer().start()
    monkeypatch.setattr("aloud.models.HF_BASE", fake.url)
    monkeypatch.setattr("aloud.models.READ_SIZE", 1000)
    yield fake
    fake.stop()


def serve_repo(hub, repo, files):
    hub.routes[("GET", f"/api/models/{repo}/tree/main")] = (200, [
        {"type": "file", "path": name, "size": len(data)} for name, data in files.items()
    ] + [{"type": "directory", "path": "extra"}])
    for name, data in files.items():
        hub.routes[("GET", f"/{repo}/resolve/main/{name}")] = (200, data)


# -- the catalog --------------------------------------------------------------


def test_catalog_keys_are_unique():
    keys = [m.key for m in models.CATALOG]
    assert len(keys) == len(set(keys))


def test_every_catalog_engine_is_a_real_local_engine():
    for model in models.CATALOG:
        assert model.engine in REGISTRY and not REGISTRY[model.engine].cloud


def test_each_engine_has_one_recommended_model():
    for engine in (models.PARAKEET, models.FASTER_WHISPER, models.WHISPER_CPP):
        assert sum(m.recommended for m in models.for_engine(engine)) == 1, engine


def test_the_engine_defaults_are_in_the_catalog():
    """The model a fresh install uses must be one the window can show."""
    from aloud.config import DEFAULTS

    for engine in (models.PARAKEET, models.FASTER_WHISPER):
        default = DEFAULTS["engines"][engine]["model"]
        assert any(m.model_id == default for m in models.for_engine(engine)), engine


def test_whisper_cpp_downloads_land_where_the_engine_looks(dirs):
    """find_model() globs ggml-*.bin at the top of the models folder."""
    models_dir, _ = dirs
    for model in models.for_engine(models.WHISPER_CPP):
        assert model.local_path.parent == models_dir
        assert model.local_path.name.startswith("ggml-") and model.local_path.suffix == ".bin"


# -- downloading --------------------------------------------------------------


def test_download_fetches_only_the_files_the_engine_reads(hub, dirs):
    model = models.find("faster_whisper:base.en")
    serve_repo(hub, model.repo, {
        "config.json": b"{}", "model.bin": b"w" * 5000, "tokenizer.json": b"{}",
        "vocabulary.txt": b"a\nb", "README.md": b"not needed",
    })
    seen = []

    target = models.download(model, lambda done, total: seen.append((done, total)) or True)

    assert target == model.local_path
    assert sorted(p.name for p in target.iterdir()) == [
        "config.json", "model.bin", "tokenizer.json", "vocabulary.txt"]
    assert seen[-1][0] == seen[-1][1] == 2 + 5000 + 2 + 3  # exact, from the API sizes
    assert models.status(model).installed and models.status(model).where == "aloud"


def test_whisper_cpp_downloads_one_file(hub, dirs):
    model = models.find("whisper_cpp:ggml-base.en.bin")
    serve_repo(hub, model.repo, {"ggml-base.en.bin": b"g" * 3000, "ggml-tiny.bin": b"t"})
    target = models.download(model)
    assert target.is_file() and target.read_bytes() == b"g" * 3000
    assert models.status(model).bytes_on_disk == 3000


def test_cancelling_leaves_nothing_that_looks_installed(hub, dirs):
    model = models.find("faster_whisper:base.en")
    serve_repo(hub, model.repo, {"config.json": b"{}", "model.bin": b"w" * 10000})

    with pytest.raises(models.Cancelled):
        models.download(model, lambda done, _total: done < 3000)

    models_dir, _ = dirs
    assert not models.status(model).installed
    assert not any(models_dir.rglob("*partial*"))
    assert not model.local_path.exists()


def test_a_missing_repository_is_explained(hub, dirs):
    with pytest.raises(models.DownloadError, match="no repository"):
        models.download(models.find("faster_whisper:base.en"))


def test_a_repository_without_the_weights_is_refused(hub, dirs):
    model = models.find("faster_whisper:base.en")
    serve_repo(hub, model.repo, {"config.json": b"{}"})
    with pytest.raises(models.DownloadError, match="model.bin"):
        models.download(model)


def test_the_background_download_reports_progress_and_errors(hub, dirs):
    model = models.find("faster_whisper:base.en")
    serve_repo(hub, model.repo, {"config.json": b"{}", "model.bin": b"w" * 4000})
    finished = []
    job = models.Download(model, on_done=finished.append).start()
    job._thread.join(5)
    assert job.finished and not job.error and job.fraction == 1.0
    assert finished == [job]

    failing = models.Download(models.find("faster_whisper:tiny.en")).start()
    failing._thread.join(5)
    assert failing.finished and "no repository" in failing.error


# -- what is on disk ----------------------------------------------------------


def make_cached(cache: Path, model: models.Model) -> Path:
    blobs = cache / f"models--{model.repo.replace('/', '--')}" / "blobs"
    snapshot = cache / f"models--{model.repo.replace('/', '--')}" / "snapshots" / "abc123"
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    (blobs / "deadbeef").write_bytes(b"x" * 1234)
    (snapshot / model.main_file).symlink_to(blobs / "deadbeef")
    return snapshot


def test_weights_already_in_the_hugging_face_cache_are_found(dirs):
    _, cache = dirs
    model = models.find(PARAKEET_V3)
    snapshot = make_cached(cache, model)
    state = models.status(model)
    assert state.installed and state.where == "cache" and state.path == snapshot
    assert state.bytes_on_disk == 1234
    assert "shared cache" in state.label
    # Used by name, which is how the engine already finds cached weights.
    assert models.setting_for(model) == model.model_id


def test_our_own_copy_is_used_by_path(dirs):
    model = models.find(PARAKEET_V3)
    model.local_path.mkdir(parents=True)
    (model.local_path / "model.safetensors").write_bytes(b"w")
    assert models.setting_for(model) == str(model.local_path)


def test_delete_removes_both_copies(dirs):
    _, cache = dirs
    model = models.find(PARAKEET_V3)
    make_cached(cache, model)
    model.local_path.mkdir(parents=True)
    (model.local_path / "model.safetensors").write_bytes(b"w")

    assert models.delete(model)
    assert not models.status(model).installed
    assert not models.delete(model)


# -- which model is in use ----------------------------------------------------


def test_in_use_matches_a_name_a_repo_or_a_path(dirs):
    model = models.find(PARAKEET_V3)
    assert models.in_use(model, model.model_id)
    assert models.in_use(model, "", default=model.model_id)
    assert models.in_use(model, str(model.local_path))
    assert not models.in_use(model, "mlx-community/parakeet-tdt-0.6b-v2")
    assert not models.in_use(model, "")


# -- the window's rules -------------------------------------------------------


def installed(where="aloud"):
    return models.Status(True, where, Path("/x"), 2_500_000_000)


def test_a_missing_model_offers_download_only(dirs):
    view = models.row_view(models.find(PARAKEET_V3), models.Status(False), None, "", True)
    assert view.action == "download" and not view.can_delete and not view.can_use


def test_a_downloaded_model_can_be_used_and_deleted(dirs):
    model = models.find(PARAKEET_V3)
    view = models.row_view(model, installed(), None, "mlx-community/parakeet-tdt-0.6b-v2", True)
    assert view.action == "" and view.can_delete and view.can_use and not view.in_use


def test_the_model_in_use_says_so_and_cannot_be_used_again(dirs):
    model = models.find(PARAKEET_V3)
    view = models.row_view(model, installed(), None, model.model_id, True)
    assert view.in_use and not view.can_use and view.tone == "success"
    assert view.status.startswith("In use")


def test_nothing_can_be_deleted_or_used_mid_download(dirs):
    model = models.find(PARAKEET_V3)
    job = models.Download(model)
    job.done_bytes, job.total_bytes = 1_000_000_000, 2_000_000_000
    view = models.row_view(model, models.Status(False), job, "", True)
    assert view.action == "cancel" and view.progress == 0.5
    assert not view.can_delete and not view.can_use
    assert "1,000 of 2,000 MB" in view.status


def test_a_failed_download_shows_why(dirs):
    model = models.find(PARAKEET_V3)
    job = models.Download(model)
    job.finished, job.error = True, "The disk is full."
    view = models.row_view(model, models.Status(False), job, "", True)
    assert view.tone == "error" and "The disk is full." in view.status
    assert view.action == "download"  # and it can be retried


def test_an_engine_this_mac_cannot_run_offers_nothing_but_delete(dirs):
    model = models.find(PARAKEET_V3)
    assert models.row_view(model, models.Status(False), None, "", False).action == ""
    stale = models.row_view(model, installed(), None, "", False)
    assert stale.can_delete and not stale.can_use


def test_parakeet_is_unavailable_off_apple_silicon(monkeypatch):
    monkeypatch.setattr("aloud.models.platform.machine", lambda: "x86_64")
    ok, reason = models.engine_available(models.PARAKEET)
    assert not ok and "Apple Silicon" in reason


def test_advice_differs_between_the_two_kinds_of_mac():
    turbo = models.find("faster_whisper:large-v3-turbo")
    base = models.find("faster_whisper:base.en")
    assert "files" in models.advice(turbo, "x86_64")
    assert "dictation" in models.advice(base, "x86_64")
    assert models.advice(base, "arm64") == "", "Parakeet is the dictation pick there"
    assert "dictation" in models.advice(models.find(PARAKEET_V3), "arm64")


def test_every_piece_of_advice_names_a_real_model():
    for (arch, key) in models.ADVICE:
        assert arch in ("arm64", "x86_64") and models.find(key) is not None, key
