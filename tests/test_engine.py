from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pytest

import botified_tts.engine as engine_module
from botified_tts.config import (
    CudaPreflightError,
    MAX_CONCURRENT_SYNTHESIS,
    Settings,
)
from botified_tts.engine import (
    EngineError,
    GenerationCompletion,
    VoxCPMEngine,
)

HUGGINGFACE_REVISION = "bffb3df5a29440629464e5e839f4d214c8714c3d"
MODELSCOPE_REVISION = "2e7c0dfff6646cef46c8bf106460a3dbce23a591"
WAVEFORM_SAMPLES = 7680


def _settings(
    tmp_path: Path,
    *,
    source: str = "huggingface",
    device: int = 0,
) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8000,
        model_source=source,  # type: ignore[arg-type]
        gpu_device=device,
        data_dir=tmp_path,
        api_key="test-secret",
        log_level="INFO",
    )


class _RawStream:
    def __init__(
        self,
        items: Iterable[object],
        *,
        close_error: Exception | None = None,
    ) -> None:
        self._items = iter(items)
        self._close_error = close_error
        self.closed = False

    def __aiter__(self) -> _RawStream:
        return self

    async def __anext__(self) -> object:
        try:
            item = next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


class _FakeTokenizer:
    def __init__(self, token_count: int = 5) -> None:
        self.token_count = token_count
        self.calls: list[str] = []

    def __call__(self, text: str) -> list[int]:
        self.calls.append(text)
        return list(range(self.token_count))


class _FakePool:
    instances: list[_FakePool] = []
    ready_error: BaseException | None = None
    model_info: dict[str, int] = {"sample_rate": 48000, "channels": 1}
    warmup_items: list[object] = [
        np.zeros(WAVEFORM_SAMPLES, dtype=np.float32),
        {"type": "completion", "generated_latents": b"warmup-latents"},
    ]

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.generate_calls: list[dict[str, object]] = []
        self.encode_calls: list[tuple[bytes, str, str]] = []
        self.model_info_calls = 0
        self.streams: list[_RawStream] = []
        self.close_error: Exception | None = None
        self.stop_calls = 0
        self.instances.append(self)

    async def wait_for_ready(self) -> None:
        if self.ready_error is not None:
            raise self.ready_error

    async def get_model_info(self) -> dict[str, int]:
        self.model_info_calls += 1
        return dict(self.model_info)

    def generate(self, **kwargs: object) -> _RawStream:
        self.generate_calls.append(kwargs)
        stream = _RawStream(
            list(self.warmup_items),
            close_error=self.close_error,
        )
        self.streams.append(stream)
        return stream

    async def encode_latents(
        self,
        audio: bytes,
        audio_format: str,
        role: str,
    ) -> bytes:
        self.encode_calls.append((audio, audio_format, role))
        return f"{role}-latents".encode()

    async def wait_for_fatal(self) -> None:
        raise RuntimeError("worker died")

    async def stop(self) -> None:
        self.stop_calls += 1


def _install_fake_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: str = "huggingface",
    download: Any,
    pool_class: type[_FakePool] = _FakePool,
) -> None:
    if source == "huggingface":
        huggingface_hub = ModuleType("huggingface_hub")
        huggingface_hub.snapshot_download = download  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "huggingface_hub", huggingface_hub)
        monkeypatch.setitem(sys.modules, "modelscope_hub", None)
    else:
        modelscope_hub = ModuleType("modelscope_hub")

        class FakeHubApi:
            def download_repo(self, **kwargs: object) -> str | Path:
                return download(**kwargs)

        modelscope_hub.HubApi = FakeHubApi  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "modelscope_hub", modelscope_hub)
        monkeypatch.setitem(sys.modules, "huggingface_hub", None)

    nano = ModuleType("nanovllm_voxcpm")
    nano.__path__ = []  # type: ignore[attr-defined]
    models = ModuleType("nanovllm_voxcpm.models")
    models.__path__ = []  # type: ignore[attr-defined]
    voxcpm2 = ModuleType("nanovllm_voxcpm.models.voxcpm2")
    voxcpm2.__path__ = []  # type: ignore[attr-defined]
    server = ModuleType("nanovllm_voxcpm.models.voxcpm2.server")
    server.AsyncVoxCPM2ServerPool = pool_class  # type: ignore[attr-defined]
    utils = ModuleType("nanovllm_voxcpm.models.voxcpm2.utils")
    transformers = ModuleType("transformers")
    tokenizer_loads: list[str] = []
    tokenizer_masks: list[object] = []

    class FakeLlamaTokenizerFast:
        @classmethod
        def from_pretrained(cls, model_path: str) -> object:
            tokenizer_loads.append(model_path)
            return cls()

    def mask_multichar_chinese_tokens(tokenizer: object) -> _FakeTokenizer:
        tokenizer_masks.append(tokenizer)
        return _FakeTokenizer(token_count=len("你好。"))

    transformers.LlamaTokenizerFast = FakeLlamaTokenizerFast  # type: ignore[attr-defined]
    utils.mask_multichar_chinese_tokens = mask_multichar_chinese_tokens  # type: ignore[attr-defined]
    transformers.tokenizer_loads = tokenizer_loads  # type: ignore[attr-defined]
    utils.tokenizer_masks = tokenizer_masks  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "transformers", transformers)
    monkeypatch.setitem(sys.modules, "nanovllm_voxcpm", nano)
    monkeypatch.setitem(sys.modules, "nanovllm_voxcpm.models", models)
    monkeypatch.setitem(sys.modules, "nanovllm_voxcpm.models.voxcpm2", voxcpm2)
    monkeypatch.setitem(
        sys.modules,
        "nanovllm_voxcpm.models.voxcpm2.server",
        server,
    )
    monkeypatch.setitem(
        sys.modules,
        "nanovllm_voxcpm.models.voxcpm2.utils",
        utils,
    )


@pytest.mark.parametrize("source", ["huggingface", "modelscope"])
def test_create_checks_cuda_before_runtime_imports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: str,
) -> None:
    def reject_cuda(device: int) -> None:
        raise CudaPreflightError("cuda_unavailable", "no CUDA")

    monkeypatch.setattr(engine_module, "require_cuda", reject_cuda)
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    monkeypatch.setitem(sys.modules, "modelscope_hub", None)
    monkeypatch.setitem(sys.modules, "nanovllm_voxcpm", None)
    monkeypatch.setitem(sys.modules, "transformers", None)

    with pytest.raises(CudaPreflightError, match="cuda_unavailable"):
        asyncio.run(VoxCPMEngine.create(_settings(tmp_path, source=source)))


@pytest.mark.parametrize(
    ("source", "repo_id", "revision"),
    [
        (
            "huggingface",
            "openbmb/VoxCPM2",
            HUGGINGFACE_REVISION,
        ),
        (
            "modelscope",
            "OpenBMB/VoxCPM2",
            MODELSCOPE_REVISION,
        ),
    ],
)
def test_create_downloads_exact_snapshot_and_completes_warmup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: str,
    repo_id: str,
    revision: str,
) -> None:
    selected_devices: list[int] = []
    download_calls: list[dict[str, object]] = []
    _FakePool.instances = []
    _FakePool.ready_error = None
    _FakePool.model_info = {"sample_rate": 48000, "channels": 1}
    _FakePool.warmup_items = [
        np.zeros(WAVEFORM_SAMPLES, dtype=np.float32),
        {"type": "completion", "generated_latents": b"warmup-latents"},
    ]

    monkeypatch.setattr(
        engine_module,
        "require_cuda",
        lambda device: selected_devices.append(device),
    )

    def download(**kwargs: object) -> str | Path:
        download_calls.append(kwargs)
        model_path = f"/models/{source}-voxcpm2"
        return Path(model_path) if source == "modelscope" else model_path

    _install_fake_runtime(
        monkeypatch,
        source=source,
        download=download,
    )

    engine = asyncio.run(
        VoxCPMEngine.create(
            _settings(tmp_path, source=source, device=2),
        )
    )
    pool = _FakePool.instances[0]

    assert selected_devices == [2]
    expected_download = {
        "repo_id": repo_id,
        "revision": revision,
        "cache_dir": tmp_path / "model-cache" / source,
    }
    if source == "modelscope":
        expected_download["repo_type"] = "model"
    assert download_calls == [expected_download]
    assert pool.kwargs == {
        "model_path": f"/models/{source}-voxcpm2",
        "devices": [2],
        "max_num_seqs": MAX_CONCURRENT_SYNTHESIS,
        "gpu_memory_utilization": 0.8,
    }
    assert len(pool.generate_calls) == 1
    assert pool.model_info_calls == 1
    assert pool.generate_calls[0]["target_text"]
    assert pool.generate_calls[0]["max_generate_length"] == 28
    transformers = sys.modules["transformers"]
    utils = sys.modules["nanovllm_voxcpm.models.voxcpm2.utils"]
    assert transformers.tokenizer_loads == [f"/models/{source}-voxcpm2"]  # type: ignore[attr-defined]
    assert len(utils.tokenizer_masks) == 1  # type: ignore[attr-defined]
    assert pool.streams[0].closed is True

    asyncio.run(engine.close())


@pytest.mark.parametrize("source", ["huggingface", "modelscope"])
def test_selected_source_download_failure_does_not_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: str,
) -> None:
    calls: list[str] = []
    _FakePool.instances = []
    monkeypatch.setattr(engine_module, "require_cuda", lambda device: None)

    def selected_download(**kwargs: object) -> str:
        calls.append(source)
        raise RuntimeError("selected source failed")

    other_source = "modelscope" if source == "huggingface" else "huggingface"

    def fallback_download(**kwargs: object) -> str:
        calls.append(other_source)
        return "/models/fallback-must-not-be-used"

    _install_fake_runtime(
        monkeypatch,
        source=source,
        download=selected_download,
    )
    if source == "huggingface":
        modelscope_hub = ModuleType("modelscope_hub")

        class FallbackHubApi:
            def download_repo(self, **kwargs: object) -> str:
                return fallback_download(**kwargs)

        modelscope_hub.HubApi = FallbackHubApi  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "modelscope_hub", modelscope_hub)
    else:
        huggingface_hub = ModuleType("huggingface_hub")
        huggingface_hub.snapshot_download = fallback_download  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "huggingface_hub", huggingface_hub)

    with pytest.raises(EngineError) as caught:
        asyncio.run(
            VoxCPMEngine.create(
                _settings(tmp_path, source=source),
            )
        )

    assert caught.value.code == "model_load_failed"
    assert calls == [source]
    assert _FakePool.instances == []


@pytest.mark.parametrize("failure_stage", ["ready", "warmup"])
def test_create_cleans_pool_and_reports_model_load_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_stage: str,
) -> None:
    _FakePool.instances = []
    _FakePool.ready_error = (
        RuntimeError("ready failed") if failure_stage == "ready" else None
    )
    _FakePool.model_info = {"sample_rate": 48000, "channels": 1}
    _FakePool.warmup_items = (
        [{"type": "completion", "generated_latents": b"warmup-latents"}]
        if failure_stage == "warmup"
        else [
            np.zeros(WAVEFORM_SAMPLES, dtype=np.float32),
            {"type": "completion", "generated_latents": b"warmup-latents"},
        ]
    )
    monkeypatch.setattr(engine_module, "require_cuda", lambda device: None)

    def download(**kwargs: object) -> str:
        return "/models/voxcpm2-snapshot"

    _install_fake_runtime(
        monkeypatch,
        download=download,
    )

    with pytest.raises(EngineError) as caught:
        asyncio.run(VoxCPMEngine.create(_settings(tmp_path)))

    assert caught.value.code == "model_load_failed"
    if _FakePool.instances:
        assert _FakePool.instances[0].stop_calls == 1


@pytest.mark.parametrize(
    "model_info",
    [
        {"sample_rate": 16000, "channels": 1},
        {"sample_rate": 48000, "channels": 2},
    ],
)
def test_create_rejects_incompatible_model_info(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    model_info: dict[str, int],
) -> None:
    _FakePool.instances = []
    _FakePool.ready_error = None
    _FakePool.model_info = model_info
    _FakePool.warmup_items = [
        np.zeros(WAVEFORM_SAMPLES, dtype=np.float32),
        {"type": "completion", "generated_latents": b"warmup-latents"},
    ]
    monkeypatch.setattr(engine_module, "require_cuda", lambda device: None)
    _install_fake_runtime(
        monkeypatch,
        download=lambda **kwargs: "/models/voxcpm2-snapshot",
    )

    with pytest.raises(EngineError) as caught:
        asyncio.run(VoxCPMEngine.create(_settings(tmp_path)))

    assert caught.value.code == "model_load_failed"
    pool = _FakePool.instances[0]
    assert pool.model_info_calls == 1
    assert pool.generate_calls == []
    assert pool.stop_calls == 1


def test_create_cleans_pool_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _FakePool.instances = []
    _FakePool.ready_error = asyncio.CancelledError()
    _FakePool.model_info = {"sample_rate": 48000, "channels": 1}
    monkeypatch.setattr(engine_module, "require_cuda", lambda device: None)
    _install_fake_runtime(
        monkeypatch,
        download=lambda **kwargs: "/models/voxcpm2-snapshot",
    )

    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(VoxCPMEngine.create(_settings(tmp_path)))
    finally:
        _FakePool.ready_error = None

    assert _FakePool.instances[0].stop_calls == 1


def test_generate_forwards_conditioning_and_emits_terminal_completion() -> None:
    pool = _FakePool()
    waveform = np.arange(WAVEFORM_SAMPLES, dtype=np.float32)
    pool.warmup_items = [
        waveform,
        {"type": "completion", "generated_latents": b"generated"},
    ]
    tokenizer = _FakeTokenizer(token_count=5)
    engine = VoxCPMEngine(pool, tokenizer)

    async def collect() -> list[object]:
        return [
            item
            async for item in engine.generate(
                target_text="hello",
                prompt_latents=b"prompt",
                prompt_text="previous",
                temperature=0.9,
                cfg_value=2.0,
                ref_audio_latents=b"reference",
                seed=42,
            )
        ]

    output = asyncio.run(collect())

    assert output[0] is waveform
    assert output[1] == GenerationCompletion(generated_latents=b"generated")
    assert pool.generate_calls == [
        {
            "target_text": "hello",
            "prompt_latents": b"prompt",
            "prompt_text": "previous",
            "max_generate_length": 40,
            "temperature": 0.9,
            "cfg_value": 2.0,
            "ref_audio_latents": b"reference",
            "seed": 42,
        }
    ]
    assert tokenizer.calls == ["hello"]
    assert pool.streams[0].closed is True


def test_generate_caps_official_budget_at_2000() -> None:
    pool = _FakePool()
    tokenizer = _FakeTokenizer(token_count=400)
    engine = VoxCPMEngine(pool, tokenizer)

    async def collect() -> None:
        async for _ in engine.generate(target_text="long target"):
            pass

    asyncio.run(collect())

    assert tokenizer.calls == ["long target"]
    assert pool.generate_calls[0]["max_generate_length"] == 2000


@pytest.mark.parametrize(
    "raw_items",
    [
        [],
        [np.zeros(WAVEFORM_SAMPLES, dtype=np.float64)],
        [np.zeros((1, WAVEFORM_SAMPLES), dtype=np.float32)],
        [
            np.zeros(WAVEFORM_SAMPLES - 1, dtype=np.float32),
            {"type": "completion", "generated_latents": b"done"},
        ],
        [{"type": "completion", "generated_latents": b"done"}],
        [
            {"type": "completion", "generated_latents": b"done"},
            np.zeros(WAVEFORM_SAMPLES, dtype=np.float32),
        ],
        [
            {"type": "completion", "generated_latents": b"one"},
            {"type": "completion", "generated_latents": b"two"},
        ],
        [{"type": "completion", "generated_latents": b""}],
        [{"type": "completion", "generated_latents": bytearray(b"bad")}],
        [{"type": "unknown"}],
    ],
)
def test_generate_rejects_invalid_raw_protocol(raw_items: list[object]) -> None:
    pool = _FakePool()
    pool.warmup_items = raw_items
    engine = VoxCPMEngine(pool, _FakeTokenizer())

    async def collect() -> None:
        async for _ in engine.generate(target_text="hello"):
            pass

    with pytest.raises(EngineError) as caught:
        asyncio.run(collect())

    assert caught.value.code == "engine_error"
    assert pool.streams[0].closed is True


def test_generate_closes_raw_stream_when_consumer_stops_early() -> None:
    pool = _FakePool()
    pool.warmup_items = [
        np.zeros(WAVEFORM_SAMPLES, dtype=np.float32),
        np.ones(WAVEFORM_SAMPLES, dtype=np.float32),
        {"type": "completion", "generated_latents": b"done"},
    ]
    engine = VoxCPMEngine(pool, _FakeTokenizer())

    async def close_early() -> None:
        stream = engine.generate(target_text="hello")
        await stream.__anext__()
        await stream.aclose()

    asyncio.run(close_early())

    assert pool.streams[0].closed is True


@pytest.mark.parametrize("close_early", [False, True])
def test_generate_maps_raw_stream_close_failure(close_early: bool) -> None:
    pool = _FakePool()
    pool.warmup_items = [
        np.zeros(WAVEFORM_SAMPLES, dtype=np.float32),
        {"type": "completion", "generated_latents": b"done"},
    ]
    pool.close_error = RuntimeError("close failed")
    engine = VoxCPMEngine(pool, _FakeTokenizer())

    async def consume() -> None:
        stream = engine.generate(target_text="hello")
        if close_early:
            await stream.__anext__()
            await stream.aclose()
        else:
            async for _ in stream:
                pass

    with pytest.raises(EngineError) as caught:
        asyncio.run(consume())

    assert caught.value.code == "engine_error"


def test_encode_rejects_empty_latents() -> None:
    pool = _FakePool()

    async def encode_empty(*args: object, **kwargs: object) -> bytes:
        return b""

    pool.encode_latents = encode_empty  # type: ignore[method-assign]
    engine = VoxCPMEngine(pool, _FakeTokenizer())

    with pytest.raises(EngineError) as caught:
        asyncio.run(engine.encode_reference(b"reference"))

    assert caught.value.code == "engine_error"


def test_encode_fatal_wait_and_close_delegate_to_pool() -> None:
    pool = _FakePool()
    engine = VoxCPMEngine(pool, _FakeTokenizer())

    async def exercise() -> None:
        assert await engine.encode_reference(b"reference") == b"reference-latents"
        assert await engine.encode_prompt(b"prompt") == b"prompt-latents"
        with pytest.raises(RuntimeError, match="worker died"):
            await engine.wait_for_fatal()
        await engine.close()
        await engine.close()

    asyncio.run(exercise())

    assert pool.encode_calls == [
        (b"reference", "wav", "reference"),
        (b"prompt", "wav", "prompt"),
    ]
    assert pool.stop_calls == 1
