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
from botified_tts.config import CudaPreflightError, Settings
from botified_tts.engine import (
    EngineError,
    GenerationCompletion,
    VoxCPMEngine,
)

MODEL_REVISION = "bffb3df5a29440629464e5e839f4d214c8714c3d"


def _settings(tmp_path: Path, *, device: int = 0) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8000,
        model="openbmb/VoxCPM2",
        model_revision=MODEL_REVISION,
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


class _FakePool:
    instances: list[_FakePool] = []
    ready_error: BaseException | None = None
    warmup_items: list[object] = [
        np.zeros(8, dtype=np.float32),
        {"type": "completion", "generated_latents": b"warmup-latents"},
    ]

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.generate_calls: list[dict[str, object]] = []
        self.encode_calls: list[tuple[bytes, str, str]] = []
        self.streams: list[_RawStream] = []
        self.close_error: Exception | None = None
        self.stop_calls = 0
        self.instances.append(self)

    async def wait_for_ready(self) -> None:
        if self.ready_error is not None:
            raise self.ready_error

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
    snapshot_download: Any,
    pool_class: type[_FakePool] = _FakePool,
) -> None:
    huggingface_hub = ModuleType("huggingface_hub")
    huggingface_hub.snapshot_download = snapshot_download  # type: ignore[attr-defined]

    nano = ModuleType("nanovllm_voxcpm")
    nano.__path__ = []  # type: ignore[attr-defined]
    models = ModuleType("nanovllm_voxcpm.models")
    models.__path__ = []  # type: ignore[attr-defined]
    voxcpm2 = ModuleType("nanovllm_voxcpm.models.voxcpm2")
    voxcpm2.__path__ = []  # type: ignore[attr-defined]
    server = ModuleType("nanovllm_voxcpm.models.voxcpm2.server")
    server.AsyncVoxCPM2ServerPool = pool_class  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "huggingface_hub", huggingface_hub)
    monkeypatch.setitem(sys.modules, "nanovllm_voxcpm", nano)
    monkeypatch.setitem(sys.modules, "nanovllm_voxcpm.models", models)
    monkeypatch.setitem(sys.modules, "nanovllm_voxcpm.models.voxcpm2", voxcpm2)
    monkeypatch.setitem(
        sys.modules,
        "nanovllm_voxcpm.models.voxcpm2.server",
        server,
    )


def test_create_checks_cuda_before_runtime_imports(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def reject_cuda(device: int) -> None:
        raise CudaPreflightError("cuda_unavailable", "no CUDA")

    monkeypatch.setattr(engine_module, "require_cuda", reject_cuda)
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    monkeypatch.setitem(sys.modules, "nanovllm_voxcpm", None)

    with pytest.raises(CudaPreflightError, match="cuda_unavailable"):
        asyncio.run(VoxCPMEngine.create(_settings(tmp_path)))


def test_create_downloads_exact_snapshot_and_completes_warmup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[object] = []
    _FakePool.instances = []
    _FakePool.ready_error = None
    _FakePool.warmup_items = [
        np.zeros(8, dtype=np.float32),
        {"type": "completion", "generated_latents": b"warmup-latents"},
    ]

    monkeypatch.setattr(
        engine_module,
        "require_cuda",
        lambda device: events.append(("cuda", device)),
    )

    def snapshot_download(**kwargs: object) -> str:
        events.append(("download", kwargs))
        return "/models/voxcpm2-snapshot"

    _install_fake_runtime(
        monkeypatch,
        snapshot_download=snapshot_download,
    )

    engine = asyncio.run(VoxCPMEngine.create(_settings(tmp_path, device=2)))
    pool = _FakePool.instances[0]

    assert events == [
        ("cuda", 2),
        (
            "download",
            {
                "repo_id": "openbmb/VoxCPM2",
                "revision": MODEL_REVISION,
                "cache_dir": tmp_path / "model-cache",
            },
        ),
    ]
    assert pool.kwargs == {
        "model_path": "/models/voxcpm2-snapshot",
        "devices": [2],
        "max_num_seqs": 16,
        "gpu_memory_utilization": 0.8,
    }
    assert len(pool.generate_calls) == 1
    assert pool.generate_calls[0]["target_text"]
    assert pool.streams[0].closed is True

    asyncio.run(engine.close())


@pytest.mark.parametrize("failure_stage", ["download", "ready", "warmup"])
def test_create_cleans_pool_and_reports_model_load_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_stage: str,
) -> None:
    _FakePool.instances = []
    _FakePool.ready_error = (
        RuntimeError("ready failed") if failure_stage == "ready" else None
    )
    _FakePool.warmup_items = (
        []
        if failure_stage == "warmup"
        else [
            np.zeros(8, dtype=np.float32),
            {"type": "completion", "generated_latents": b"warmup-latents"},
        ]
    )
    monkeypatch.setattr(engine_module, "require_cuda", lambda device: None)

    def snapshot_download(**kwargs: object) -> str:
        if failure_stage == "download":
            raise RuntimeError("download failed")
        return "/models/voxcpm2-snapshot"

    _install_fake_runtime(
        monkeypatch,
        snapshot_download=snapshot_download,
    )

    with pytest.raises(EngineError) as caught:
        asyncio.run(VoxCPMEngine.create(_settings(tmp_path)))

    assert caught.value.code == "model_load_failed"
    if _FakePool.instances:
        assert _FakePool.instances[0].stop_calls == 1


def test_create_cleans_pool_when_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _FakePool.instances = []
    _FakePool.ready_error = asyncio.CancelledError()
    monkeypatch.setattr(engine_module, "require_cuda", lambda device: None)
    _install_fake_runtime(
        monkeypatch,
        snapshot_download=lambda **kwargs: "/models/voxcpm2-snapshot",
    )

    try:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(VoxCPMEngine.create(_settings(tmp_path)))
    finally:
        _FakePool.ready_error = None

    assert _FakePool.instances[0].stop_calls == 1


def test_generate_forwards_conditioning_and_emits_terminal_completion() -> None:
    pool = _FakePool()
    waveform = np.arange(8, dtype=np.float32)
    pool.warmup_items = [
        waveform,
        {"type": "completion", "generated_latents": b"generated"},
    ]
    engine = VoxCPMEngine(pool)

    async def collect() -> list[object]:
        return [
            item
            async for item in engine.generate(
                target_text="hello",
                prompt_latents=b"prompt",
                prompt_text="previous",
                max_generate_length=120,
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
            "max_generate_length": 120,
            "temperature": 0.9,
            "cfg_value": 2.0,
            "ref_audio_latents": b"reference",
            "seed": 42,
        }
    ]
    assert pool.streams[0].closed is True


@pytest.mark.parametrize(
    "raw_items",
    [
        [],
        [np.zeros(8, dtype=np.float64)],
        [np.zeros((1, 8), dtype=np.float32)],
        [
            {"type": "completion", "generated_latents": b"done"},
            np.zeros(8, dtype=np.float32),
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
    engine = VoxCPMEngine(pool)

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
        np.zeros(8, dtype=np.float32),
        np.ones(8, dtype=np.float32),
        {"type": "completion", "generated_latents": b"done"},
    ]
    engine = VoxCPMEngine(pool)

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
        np.zeros(8, dtype=np.float32),
        {"type": "completion", "generated_latents": b"done"},
    ]
    pool.close_error = RuntimeError("close failed")
    engine = VoxCPMEngine(pool)

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
    engine = VoxCPMEngine(pool)

    with pytest.raises(EngineError) as caught:
        asyncio.run(engine.encode_reference(b"reference"))

    assert caught.value.code == "engine_error"


def test_encode_fatal_wait_and_close_delegate_to_pool() -> None:
    pool = _FakePool()
    engine = VoxCPMEngine(pool)

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
