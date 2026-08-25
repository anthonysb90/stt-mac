"""The two in-process engines: readiness reporting and lazy loading.

Neither model can be loaded here (no MLX, no CTranslate2, no Mac), so these
cover the parts that must behave correctly *before* a model exists — which is
exactly the state a half-bootstrapped machine is in.
"""

import pytest

from aloud.engines import faster_whisper as fw_module
from aloud.engines import parakeet_mlx as pk_module
from aloud.engines.base import EngineError


# -- Parakeet ---------------------------------------------------------------


def test_parakeet_reports_the_architecture_requirement(monkeypatch):
    monkeypatch.setattr(pk_module, "supported", lambda: False)
    ok, detail = pk_module.ParakeetMLXEngine().check()
    assert not ok
    assert "Apple Silicon" in detail


def test_parakeet_reports_a_missing_package(monkeypatch):
    monkeypatch.setattr(pk_module, "supported", lambda: True)
    monkeypatch.setattr(pk_module.importlib.util, "find_spec", lambda _name: None)
    ok, detail = pk_module.ParakeetMLXEngine().check()
    assert not ok
    assert "parakeet-mlx is not installed" in detail


def test_parakeet_reports_missing_ffmpeg(monkeypatch):
    from aloud import media

    monkeypatch.setattr(pk_module, "supported", lambda: True)
    monkeypatch.setattr(pk_module.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(media, "ffmpeg_path", lambda: None)
    ok, detail = pk_module.ParakeetMLXEngine().check()
    assert not ok
    assert "ffmpeg" in detail
    # Telling someone to install what they already installed wastes their time.
    assert "PATH" in detail


def test_parakeet_finds_ffmpeg_outside_the_shell_path(monkeypatch):
    """A Dock launch has no Homebrew prefix on PATH; `which` alone said no.

    That is not hypothetical: it shipped, and produced "ffmpeg not found" in
    the app on a machine whose terminal found ffmpeg instantly.
    """
    from aloud import media

    monkeypatch.setattr(pk_module, "supported", lambda: True)
    monkeypatch.setattr(pk_module.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(media.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        media.Path, "is_file", lambda self: str(self) == "/opt/homebrew/bin/ffmpeg"
    )
    ok, _detail = pk_module.ParakeetMLXEngine().check()
    assert ok, "the Homebrew prefix must be searched directly"


def test_parakeet_uses_the_configured_model_id():
    engine = pk_module.ParakeetMLXEngine({"model": "mlx-community/parakeet-tdt-0.6b-v2"})
    assert engine._model_id().endswith("v2")
    assert pk_module.ParakeetMLXEngine()._model_id() == pk_module.DEFAULT_MODEL


def test_parakeet_transcribe_refuses_before_it_can_load(tmp_path, monkeypatch):
    monkeypatch.setattr(pk_module, "supported", lambda: False)
    with pytest.raises(EngineError):
        pk_module.ParakeetMLXEngine().transcribe(tmp_path / "x.wav")


def test_parakeet_warm_up_never_raises(monkeypatch):
    """Warm-up runs on a background thread at startup; it must not crash the app."""
    monkeypatch.setattr(pk_module, "supported", lambda: False)
    pk_module.ParakeetMLXEngine().warm_up()


# -- faster-whisper ---------------------------------------------------------


def test_faster_whisper_reports_a_missing_package(monkeypatch):
    monkeypatch.setattr(fw_module.importlib.util, "find_spec", lambda _name: None)
    ok, detail = fw_module.FasterWhisperEngine().check()
    assert not ok
    assert "faster-whisper is not installed" in detail


def test_faster_whisper_reports_model_and_precision(monkeypatch):
    monkeypatch.setattr(fw_module.importlib.util, "find_spec", lambda _name: object())
    ok, detail = fw_module.FasterWhisperEngine({"model": "small.en"}).check()
    assert ok
    assert "small.en" in detail and "int8" in detail


def test_faster_whisper_warm_up_never_raises(monkeypatch):
    monkeypatch.setattr(fw_module.importlib.util, "find_spec", lambda _name: None)
    fw_module.FasterWhisperEngine().warm_up()


# -- the resident-model contract both share ---------------------------------


class _FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, _path):
        self.calls += 1
        return type("Result", (), {"text": "  hello world  "})()


def test_parakeet_loads_the_model_once_and_reuses_it(tmp_path, monkeypatch):
    """The whole point of an in-process engine: no reload per dictation."""
    engine = pk_module.ParakeetMLXEngine()
    loads = []

    def fake_load():
        loads.append(1)
        return engine._model

    engine._model = _FakeModel()
    monkeypatch.setattr(engine, "_load", fake_load)

    for _ in range(3):
        transcript = engine.transcribe(tmp_path / "x.wav")

    assert transcript.text == "hello world"  # stripped
    assert transcript.engine == "parakeet_mlx"
    assert engine._model.calls == 3
    assert len(loads) == 3  # _load called each time, but it is a cheap no-op


# -- Parakeet: streaming a file, and surviving a parakeet-mlx that cannot ----


class _StreamingParakeetStream:
    """Stands in for parakeet-mlx's streaming context.

    Each chunk of audio adds a word, which is what a growing transcript looks
    like from the outside.
    """

    def __init__(self, fail_on_add=False):
        self.chunks = []
        self.fail_on_add = fail_on_add
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True
        return False

    def add_audio(self, chunk):
        if self.fail_on_add:
            raise TypeError("add_audio() takes different arguments")
        self.chunks.append(chunk)

    @property
    def result(self):
        return type("R", (), {"text": " ".join(f"word{n}" for n in range(len(self.chunks)))})


class _StreamingParakeet:
    def __init__(self, stream=None, whole="one pass text"):
        self.stream = stream
        self.whole = whole
        self.whole_calls = 0

    def transcribe_stream(self):
        if self.stream is None:
            raise AttributeError("no transcribe_stream in this build")
        return self.stream

    def transcribe(self, _path):
        self.whole_calls += 1
        return type("R", (), {"text": self.whole})


def _write_wav(path, seconds=3.0, rate=16000, channels=1, width=2):
    import struct
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 1000) * int(rate * seconds) * channels)


@pytest.fixture
def fake_mlx(monkeypatch):
    """A stand-in for mlx.core, whose array() we only need to be a no-op."""
    import sys
    import types

    core = types.ModuleType("mlx.core")
    core.array = lambda values: values
    package = types.ModuleType("mlx")
    package.core = core
    monkeypatch.setitem(sys.modules, "mlx", package)
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    return core


def _ready_engine(monkeypatch, model, **options):
    engine = pk_module.ParakeetMLXEngine(options)
    engine._model = model
    monkeypatch.setattr(pk_module, "supported", lambda: True)
    return engine


def test_parakeet_streams_a_file_and_reports_progress(tmp_path, monkeypatch, fake_mlx):
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=30.0)
    stream = _StreamingParakeetStream()
    engine = _ready_engine(monkeypatch, _StreamingParakeet(stream), stream_chunk_seconds=10.0)

    seen = []
    result = engine.transcribe(wav, on_progress=lambda text, done, total: seen.append(
        (text, round(done, 3), round(total, 3))
    ) or True)

    assert len(stream.chunks) == 3, "30s at 10s a chunk"
    assert [done for _text, done, _total in seen] == [10.0, 20.0, 30.0]
    assert all(total == 30.0 for *_rest, total in seen)
    assert result.text == "word0 word1 word2"
    assert result.meta["mode"] == "streamed"
    assert stream.closed, "the streaming context must be exited"


def test_parakeet_progress_never_overshoots_the_length(tmp_path, monkeypatch, fake_mlx):
    """A final short chunk must not report more audio than the file holds."""
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=25.0)
    engine = _ready_engine(
        monkeypatch, _StreamingParakeet(_StreamingParakeetStream()), stream_chunk_seconds=10.0
    )

    seen = []
    engine.transcribe(wav, on_progress=lambda text, done, total: seen.append(done) or True)
    assert seen[-1] == 25.0
    assert max(seen) <= 25.0


def test_parakeet_stops_when_progress_asks_it_to(tmp_path, monkeypatch, fake_mlx):
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=60.0)
    stream = _StreamingParakeetStream()
    engine = _ready_engine(monkeypatch, _StreamingParakeet(stream), stream_chunk_seconds=10.0)

    engine.transcribe(wav, on_progress=lambda *_a: False)
    assert len(stream.chunks) == 1, "cancelled after the first chunk"


def test_parakeet_dictation_takes_the_single_pass_path(tmp_path, monkeypatch, fake_mlx):
    """No progress callback means nobody is watching, so accuracy wins."""
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=3.0)
    model = _StreamingParakeet(_StreamingParakeetStream())
    engine = _ready_engine(monkeypatch, model)

    result = engine.transcribe(wav)
    assert result.text == "one pass text"
    assert result.meta["mode"] == "whole"
    assert model.whole_calls == 1


def test_parakeet_falls_back_when_the_build_cannot_stream(tmp_path, monkeypatch, fake_mlx):
    """An older parakeet-mlx must cost the live words, not the transcript."""
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=20.0)
    model = _StreamingParakeet(stream=None)
    engine = _ready_engine(monkeypatch, model)

    seen = []
    result = engine.transcribe(wav, on_progress=lambda *a: seen.append(a) or True)
    assert result.text == "one pass text"
    assert result.meta["mode"] == "whole"
    assert model.whole_calls == 1
    assert seen == [("", 0.0, 0.0)], "an indeterminate bar, not a stalled one"


def test_parakeet_falls_back_when_add_audio_has_another_shape(tmp_path, monkeypatch, fake_mlx):
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=20.0)
    model = _StreamingParakeet(_StreamingParakeetStream(fail_on_add=True))
    engine = _ready_engine(monkeypatch, model)

    result = engine.transcribe(wav, on_progress=lambda *_a: True)
    assert result.text == "one pass text"
    assert model.whole_calls == 1


def test_parakeet_falls_back_when_mlx_is_missing(tmp_path, monkeypatch):
    """No fake_mlx fixture here: the import itself has to fail."""
    import sys

    monkeypatch.setitem(sys.modules, "mlx.core", None)
    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=20.0)
    model = _StreamingParakeet(_StreamingParakeetStream())
    engine = _ready_engine(monkeypatch, model)

    result = engine.transcribe(wav, on_progress=lambda *_a: True)
    assert result.text == "one pass text"
    assert model.whole_calls == 1


def test_parakeet_falls_back_on_audio_it_cannot_read(tmp_path, monkeypatch, fake_mlx):
    """Stereo or 8-bit means the file never went through media.prepare."""
    wav = tmp_path / "stereo.wav"
    _write_wav(wav, seconds=5.0, channels=2)
    model = _StreamingParakeet(_StreamingParakeetStream())
    engine = _ready_engine(monkeypatch, model)

    result = engine.transcribe(wav, on_progress=lambda *_a: True)
    assert result.text == "one pass text"
    assert model.whole_calls == 1


def test_parakeet_declares_progress_support():
    """The transcribe view decides whether to show words from this flag."""
    assert pk_module.ParakeetMLXEngine.supports_progress is True


def test_parakeet_stream_chunk_seconds_rejects_nonsense():
    for bad in ("", None, 0, -5, "abc"):
        engine = pk_module.ParakeetMLXEngine({"stream_chunk_seconds": bad})
        assert engine._chunk_seconds() == pk_module.DEFAULT_STREAM_CHUNK_SECONDS
    assert pk_module.ParakeetMLXEngine({"stream_chunk_seconds": 4})._chunk_seconds() == 4.0


# -- Parakeet: the model download, and its error messages -------------------


class _HubStub:
    """Stands in for huggingface_hub, which is not installed off a Mac."""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def snapshot_download(self, repo_id, **kwargs):
        self.calls.append((repo_id, kwargs))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def hub(monkeypatch):
    """Install a fake huggingface_hub and hand the test its stub."""
    import sys
    import types

    def install(stub):
        module = types.ModuleType("huggingface_hub")
        module.snapshot_download = stub.snapshot_download
        monkeypatch.setitem(sys.modules, "huggingface_hub", module)
        return stub

    return install


def test_parakeet_downloads_only_the_files_it_reads(hub, tmp_path):
    stub = hub(_HubStub(result=str(tmp_path)))
    engine = pk_module.ParakeetMLXEngine()
    assert engine._resolve("mlx-community/parakeet-tdt-0.6b-v3") == str(tmp_path)
    repo_id, kwargs = stub.calls[0]
    assert repo_id == "mlx-community/parakeet-tdt-0.6b-v3"
    assert kwargs["allow_patterns"] == ["config.json", "model.safetensors"]


def test_parakeet_uses_a_local_directory_untouched(hub, tmp_path):
    """A model id that is a real folder must not go near the network."""
    stub = hub(_HubStub(error=AssertionError("must not be called")))
    engine = pk_module.ParakeetMLXEngine()
    assert engine._resolve(str(tmp_path)) == str(tmp_path)
    assert stub.calls == []


@pytest.mark.parametrize(
    "error, expected",
    [
        (type("RepositoryNotFoundError", (Exception,), {})("404 Client Error"),
         "no model called"),
        (type("GatedRepoError", (Exception,), {})("403 Forbidden"),
         "gated"),
        (type("ConnectionError", (Exception,), {})("failed to connect"),
         "Could not reach Hugging Face"),
    ],
)
def test_parakeet_explains_why_a_download_failed(hub, error, expected):
    """The whole point: parakeet-mlx would have reported none of these.

    Its from_pretrained wraps the download in `except Exception` and retries
    the model id as a local path, so every one of these arrives as
    "[Errno 2] No such file or directory: 'mlx-community/parakeet-...'".
    """
    hub(_HubStub(error=error))
    engine = pk_module.ParakeetMLXEngine()
    with pytest.raises(EngineError) as caught:
        engine._resolve("mlx-community/parakeet-tdt-0.6b-v3")
    assert expected in str(caught.value)
    assert "No such file or directory" not in str(caught.value)


def test_parakeet_reports_a_full_disk_as_a_full_disk(hub):
    error = OSError(28, "No space left on device")
    hub(_HubStub(error=error))
    with pytest.raises(EngineError) as caught:
        pk_module.ParakeetMLXEngine()._resolve("mlx-community/parakeet-tdt-0.6b-v3")
    assert "disk space" in str(caught.value)


def test_parakeet_keeps_an_unrecognised_failure_intact(hub):
    """An error we have no advice for must still say what actually happened."""
    hub(_HubStub(error=ValueError("something entirely new")))
    with pytest.raises(EngineError) as caught:
        pk_module.ParakeetMLXEngine()._resolve("mlx-community/parakeet-tdt-0.6b-v3")
    assert "something entirely new" in str(caught.value)
    assert "ValueError" in str(caught.value)


def test_parakeet_says_when_the_hub_is_not_installed(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    with pytest.raises(EngineError) as caught:
        pk_module.ParakeetMLXEngine()._resolve("mlx-community/parakeet-tdt-0.6b-v3")
    assert "huggingface_hub" in str(caught.value)


def test_parakeet_check_reports_whether_the_weights_are_cached(hub, monkeypatch):
    """`(not loaded yet)` said nothing about whether the download had happened."""
    monkeypatch.setattr(pk_module, "supported", lambda: True)
    monkeypatch.setattr(pk_module.importlib.util, "find_spec", lambda _n: object())
    from aloud import media

    monkeypatch.setattr(media, "ffmpeg_path", lambda: "/opt/homebrew/bin/ffmpeg")

    hub(_HubStub(error=Exception("not in cache")))
    ok, detail = pk_module.ParakeetMLXEngine().check()
    assert ok and "not downloaded yet" in detail

    hub(_HubStub(result="/somewhere"))
    ok, detail = pk_module.ParakeetMLXEngine().check()
    assert ok and "downloaded, not loaded yet" in detail


def test_parakeet_cache_check_never_raises(hub):
    """check() runs on every menu open and must not be able to throw."""
    hub(_HubStub(error=RuntimeError("boom")))
    assert pk_module.ParakeetMLXEngine()._cached() is False


def test_parakeet_load_goes_through_resolve():
    """_resolve is the whole fix; from_pretrained(model_id) undoes it.

    Handing the bare model id to from_pretrained puts the download back inside
    parakeet-mlx's `except Exception`, where the real cause is discarded.
    """
    import ast
    from pathlib import Path

    source = Path(pk_module.__file__).with_suffix(".py").read_text()
    load = next(
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "_load"
    )
    body = ast.unparse(load)
    assert "self._resolve(model_id)" in body
    assert "from_pretrained(source)" in body


# -- Parakeet: cache first, network only when the weights are really missing -


class _CacheAwareHub:
    """A hub where the cache and the network can succeed independently."""

    def __init__(self, cached=None, remote=None, remote_error=None):
        self.cached = cached
        self.remote = remote
        self.remote_error = remote_error
        self.calls = []

    def snapshot_download(self, repo_id, **kwargs):
        offline = kwargs.get("local_files_only", False)
        self.calls.append("cache" if offline else "network")
        if offline:
            if self.cached is None:
                raise FileNotFoundError("not in the cache")
            return self.cached
        if self.remote_error is not None:
            raise self.remote_error
        return self.remote


def test_parakeet_uses_cached_weights_without_asking_the_network(hub, tmp_path):
    """Once warmed, the app must not need a connection to start dictating."""
    stub = hub(_CacheAwareHub(cached=str(tmp_path)))
    engine = pk_module.ParakeetMLXEngine()
    assert engine._resolve("mlx-community/parakeet-tdt-0.6b-v3") == str(tmp_path)
    assert stub.calls == ["cache"], "a cache hit must end there"


def test_parakeet_downloads_when_the_cache_is_empty(hub, tmp_path):
    stub = hub(_CacheAwareHub(cached=None, remote=str(tmp_path)))
    engine = pk_module.ParakeetMLXEngine()
    assert engine._resolve("mlx-community/parakeet-tdt-0.6b-v3") == str(tmp_path)
    assert stub.calls == ["cache", "network"]


def test_parakeet_reports_the_download_error_not_the_cache_miss(hub):
    """The cache miss is expected and says nothing; the download says why."""
    hub(_CacheAwareHub(
        cached=None,
        remote_error=type("ConnectionError", (Exception,), {})("no route to host"),
    ))
    with pytest.raises(EngineError) as caught:
        pk_module.ParakeetMLXEngine()._resolve("mlx-community/parakeet-tdt-0.6b-v3")
    message = str(caught.value)
    assert "Could not reach Hugging Face" in message
    assert "not in the cache" not in message


# -- a failed load must not be a permanent one -------------------------------


def test_parakeet_retries_after_a_transient_failure(hub, tmp_path, monkeypatch):
    """One launch with no network used to brick the engine until restart."""
    monkeypatch.setattr(pk_module, "supported", lambda: True)
    monkeypatch.setattr(pk_module.importlib.util, "find_spec", lambda _n: object())
    from aloud import media

    monkeypatch.setattr(media, "ffmpeg_path", lambda: "/opt/homebrew/bin/ffmpeg")

    engine = pk_module.ParakeetMLXEngine()
    engine._load_error = "Could not reach Hugging Face earlier"

    # The stale error must not pre-empt a fresh attempt...
    hub(_CacheAwareHub(cached=str(tmp_path)))
    import sys, types

    fake = types.ModuleType("parakeet_mlx")
    fake.from_pretrained = lambda source: object()
    monkeypatch.setitem(sys.modules, "parakeet_mlx", fake)

    assert engine._load() is not None
    assert engine._load_error == ""

    # ...while check() still reports it honestly *between* attempts.
    engine2 = pk_module.ParakeetMLXEngine()
    engine2._load_error = "old failure"
    ok, detail = engine2.check()
    assert not ok and "old failure" in detail


def test_faster_whisper_clears_its_error_on_retry_too():
    import ast
    from pathlib import Path

    source = Path(fw_module.__file__).with_suffix(".py").read_text()
    load = next(
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "_load"
    )
    assert "self._load_error = ''" in ast.unparse(load), (
        "a stale _load_error makes check() refuse forever; clear it per attempt"
    )


# -- every MLX touch on one thread ------------------------------------------


def test_mlx_thread_runs_work_on_a_single_thread():
    """MLX binds its streams to the thread that first touches it.

    Warm-up runs on a startup thread and transcription on the job worker, so
    without this the second one hits "There is no Stream(cpu, 1) in current
    thread". Both must land on the same thread.
    """
    import threading

    worker = pk_module._MLXThread()
    seen = []

    def record():
        seen.append(threading.current_thread().ident)

    threads = [
        threading.Thread(target=lambda: worker.call(record)) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(seen) == 4
    assert len(set(seen)) == 1, "all work must land on one thread"
    assert seen[0] != threading.current_thread().ident, "and not the caller's"


def test_mlx_thread_returns_values_and_re_raises_errors():
    worker = pk_module._MLXThread()
    assert worker.call(lambda a, b: a + b, 2, b=3) == 5

    class Boom(RuntimeError):
        pass

    def explode():
        raise Boom("from the MLX thread")

    with pytest.raises(Boom, match="from the MLX thread"):
        worker.call(explode)


def test_mlx_thread_runs_inline_when_already_on_it():
    """Routed methods call each other; re-queueing would deadlock."""
    worker = pk_module._MLXThread()

    def outer():
        return worker.call(lambda: "inner ran")

    assert worker.call(outer) == "inner ran"


def test_warm_up_and_transcribe_share_the_mlx_thread(hub, tmp_path, monkeypatch):
    """The real bug: two entry points, two threads, one angry scheduler."""
    import threading

    monkeypatch.setattr(pk_module, "supported", lambda: True)
    monkeypatch.setattr(pk_module.importlib.util, "find_spec", lambda _n: object())
    from aloud import media

    monkeypatch.setattr(media, "ffmpeg_path", lambda: "/opt/homebrew/bin/ffmpeg")
    hub(_CacheAwareHub(cached=str(tmp_path)))

    threads = []

    class _Recording:
        def transcribe(self, _path):
            threads.append(("transcribe", threading.current_thread().ident))
            return type("R", (), {"text": "hi"})

    import sys, types

    fake = types.ModuleType("parakeet_mlx")

    def from_pretrained(_source):
        threads.append(("load", threading.current_thread().ident))
        return _Recording()

    fake.from_pretrained = from_pretrained
    monkeypatch.setitem(sys.modules, "parakeet_mlx", fake)

    engine = pk_module.ParakeetMLXEngine()
    # Warm up from one thread, transcribe from another, as the app does.
    warm = threading.Thread(target=engine.warm_up)
    warm.start()
    warm.join(timeout=5)

    wav = tmp_path / "clip.wav"
    _write_wav(wav, seconds=1.0)
    result = []
    worker = threading.Thread(target=lambda: result.append(engine.transcribe(wav)))
    worker.start()
    worker.join(timeout=5)

    assert result and result[0].text == "hi"
    idents = {ident for _stage, ident in threads}
    assert len(threads) == 2, f"expected a load and a transcribe, got {threads}"
    assert len(idents) == 1, "the model must load and run on the same thread"


def test_transcribe_is_routed_through_the_mlx_thread():
    """A direct call would put MLX back on whichever thread happened to call."""
    import ast
    from pathlib import Path

    source = Path(pk_module.__file__).with_suffix(".py").read_text(encoding="utf-8")
    node = next(
        n for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == "transcribe"
    )
    body = ast.unparse(node)
    assert "self._mlx(" in body, "transcribe must hand off to the MLX thread"
