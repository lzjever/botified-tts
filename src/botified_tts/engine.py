from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, NoReturn

import numpy as np
from numpy.typing import NDArray

from botified_tts.config import Settings, require_cuda

EngineErrorCode = Literal["model_load_failed", "engine_error"]
WaveformChunk = NDArray[np.float32]
WARMUP_TEXT = "你好。"


class EngineError(RuntimeError):
    def __init__(self, code: EngineErrorCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class GenerationCompletion:
    generated_latents: bytes


EngineStreamItem = WaveformChunk | GenerationCompletion


class VoxCPMEngine:
    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._closed = False

    @classmethod
    async def create(cls, settings: Settings) -> VoxCPMEngine:
        require_cuda(settings.gpu_device)

        pool = None
        try:
            from huggingface_hub import snapshot_download
            from nanovllm_voxcpm.models.voxcpm2.server import (
                AsyncVoxCPM2ServerPool,
            )

            model_path = snapshot_download(
                repo_id=settings.model,
                revision=settings.model_revision,
                cache_dir=settings.data_dir / "model-cache",
            )
            pool = AsyncVoxCPM2ServerPool(
                model_path=model_path,
                devices=[settings.gpu_device],
                max_num_seqs=16,
                gpu_memory_utilization=0.8,
            )
            engine = cls(pool)
            await pool.wait_for_ready()
            async for _ in engine.generate(
                target_text=WARMUP_TEXT,
                max_generate_length=80,
                temperature=1.0,
                cfg_value=2.0,
                seed=0,
            ):
                pass
            return engine
        except asyncio.CancelledError:
            if pool is not None:
                with contextlib.suppress(Exception):
                    await pool.stop()
            raise
        except Exception as error:
            if pool is not None:
                with contextlib.suppress(Exception):
                    await pool.stop()
            raise EngineError(
                "model_load_failed",
                "VoxCPM2 model initialization failed",
            ) from error

    async def encode_reference(self, audio: bytes) -> bytes:
        return await self._encode(audio, role="reference")

    async def encode_prompt(self, audio: bytes) -> bytes:
        return await self._encode(audio, role="prompt")

    async def _encode(
        self,
        audio: bytes,
        *,
        role: Literal["reference", "prompt"],
    ) -> bytes:
        self._ensure_open()
        try:
            latents = await self._pool.encode_latents(audio, "wav", role=role)
        except Exception as error:
            raise EngineError("engine_error", "VoxCPM2 encoding failed") from error
        if not isinstance(latents, bytes) or not latents:
            raise EngineError(
                "engine_error",
                "VoxCPM2 encoding returned invalid latents",
            )
        return latents

    async def generate(
        self,
        *,
        target_text: str,
        prompt_latents: bytes | None = None,
        prompt_text: str = "",
        max_generate_length: int = 2000,
        temperature: float = 1.0,
        cfg_value: float = 2.0,
        ref_audio_latents: bytes | None = None,
        seed: int | None = None,
    ) -> AsyncIterator[EngineStreamItem]:
        self._ensure_open()
        raw_stream = self._pool.generate(
            target_text=target_text,
            prompt_latents=prompt_latents,
            prompt_text=prompt_text,
            max_generate_length=max_generate_length,
            temperature=temperature,
            cfg_value=cfg_value,
            ref_audio_latents=ref_audio_latents,
            seed=seed,
        )
        completion: GenerationCompletion | None = None
        active_error: BaseException | None = None
        try:
            try:
                async for item in raw_stream:
                    if completion is not None:
                        raise EngineError(
                            "engine_error",
                            "VoxCPM2 emitted data after completion",
                        )
                    if isinstance(item, np.ndarray):
                        if item.dtype != np.float32 or item.ndim != 1:
                            raise EngineError(
                                "engine_error",
                                "VoxCPM2 emitted an invalid waveform chunk",
                            )
                        yield item
                        continue
                    if isinstance(item, dict) and item.get("type") == "completion":
                        generated_latents = item.get("generated_latents")
                        if (
                            not isinstance(generated_latents, bytes)
                            or not generated_latents
                        ):
                            raise EngineError(
                                "engine_error",
                                "VoxCPM2 emitted invalid completion latents",
                            )
                        completion = GenerationCompletion(generated_latents)
                        continue
                    raise EngineError(
                        "engine_error",
                        "VoxCPM2 emitted an unknown stream item",
                    )
            except EngineError:
                raise
            except Exception as error:
                raise EngineError(
                    "engine_error",
                    "VoxCPM2 generation failed",
                ) from error

            if completion is None:
                raise EngineError(
                    "engine_error",
                    "VoxCPM2 stream ended without completion",
                )
            yield completion
        except BaseException as error:
            active_error = error
            raise
        finally:
            try:
                await raw_stream.aclose()
            except Exception as error:
                if active_error is None or isinstance(active_error, GeneratorExit):
                    raise EngineError(
                        "engine_error",
                        "VoxCPM2 stream cleanup failed",
                    ) from error

    async def wait_for_fatal(self) -> NoReturn:
        return await self._pool.wait_for_fatal()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._pool.stop()

    def _ensure_open(self) -> None:
        if self._closed:
            raise EngineError("engine_error", "VoxCPM2 engine is closed")
