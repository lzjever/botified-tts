from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from botified_tts.audio import InvalidWaveform, float32_to_pcm_s16le
from botified_tts.engine import EngineError, GenerationCompletion, VoxCPMEngine
from botified_tts.schemas import (
    DesignVoice,
    InvalidSynthesisOptions,
    ProfileVoice,
    SynthesisOptions,
)
from botified_tts.voices import (
    InvalidVoice,
    LatentRole,
    VoiceSnapshot,
    VoiceStore,
)

_LOGGER = logging.getLogger("uvicorn.error.botified_tts")
_PCM_BYTES_PER_SECOND = 48_000 * 2


@dataclass(slots=True)
class SynthesisSummary:
    id: str
    ttfb_started_at: float | None
    voice_type: str | None = None
    mode: str | None = None
    accepted_chars: int = 0
    segments: int = 0
    first_audio_at: float | None = None
    audio_bytes: int = 0
    generation_seconds: float = 0.0

    def set_options(self, options: SynthesisOptions) -> None:
        if isinstance(options.voice, ProfileVoice):
            self.voice_type = "profile"
            self.mode = options.mode or "controllable"
        elif isinstance(options.voice, DesignVoice):
            self.voice_type = "design"
            self.mode = None
        else:
            self.voice_type = "default"
            self.mode = None

    def accept_text(self, text: str) -> None:
        if self.ttfb_started_at is None:
            self.ttfb_started_at = time.monotonic()
        self.accepted_chars += len(text)

    def record_segment(self) -> None:
        self.segments += 1

    def record_generation(self, seconds: float) -> None:
        self.generation_seconds += seconds

    def record_pcm(self, pcm: bytes) -> None:
        if self.first_audio_at is None:
            self.first_audio_at = time.monotonic()
        self.audio_bytes += len(pcm)

    def terminal(self, result: str) -> dict[str, object]:
        ttfb: float | None = None
        audio_duration: float | None = None
        rtf: float | None = None
        if self.first_audio_at is not None:
            if self.ttfb_started_at is not None:
                ttfb = self.first_audio_at - self.ttfb_started_at
            audio_duration = self.audio_bytes / _PCM_BYTES_PER_SECOND
            if audio_duration > 0:
                rtf = self.generation_seconds / audio_duration
        return {
            "id": self.id,
            "voice_type": self.voice_type,
            "mode": self.mode,
            "accepted_chars": self.accepted_chars,
            "segments": self.segments,
            "ttfb": ttfb,
            "audio_duration": audio_duration,
            "rtf": rtf,
            "result": result,
        }

    def log_terminal(self, result: str) -> None:
        _LOGGER.info(
            json.dumps(
                self.terminal(result),
                separators=(",", ":"),
                sort_keys=True,
            )
        )


class SpeechService:
    def __init__(
        self,
        engine: VoxCPMEngine,
        voices: VoiceStore,
    ) -> None:
        self._engine = engine
        self._voices = voices

    async def synthesize(
        self,
        options: SynthesisOptions,
        segments: AsyncIterator[str],
        *,
        summary: SynthesisSummary | None = None,
    ) -> AsyncIterator[bytes]:
        if summary is not None:
            summary.set_options(options)
        reference_latents: bytes | None = None
        prompt_latents: bytes | None = None
        prompt_text = ""

        voice = options.voice
        if isinstance(voice, ProfileVoice):
            snapshot = self._voices.get_snapshot(voice.id)
            if snapshot is None:
                raise InvalidVoice("voice profile does not exist")

            mode = options.mode or "controllable"
            if mode == "faithful":
                if (
                    not snapshot.metadata.prompt_text
                    or not snapshot.metadata.prompt_text.strip()
                ):
                    raise InvalidSynthesisOptions(
                        "faithful mode requires an exact transcript"
                    )
                prompt_text = snapshot.metadata.prompt_text

            reference_latents = await self._voice_latent(
                snapshot,
                "reference",
            )
            if mode == "faithful":
                prompt_latents = await self._voice_latent(
                    snapshot,
                    "prompt",
                )

        control = _control_instruction(options)
        first_segment = True

        async for segment in segments:
            if summary is not None:
                summary.record_segment()
            target_text = (
                f"({control}){segment}"
                if first_segment and control
                else segment
            )
            first_segment = False
            generation = self._engine.generate(
                target_text=target_text,
                prompt_latents=prompt_latents,
                prompt_text=prompt_text,
                ref_audio_latents=reference_latents,
            )
            completion: GenerationCompletion | None = None
            try:
                iterator = aiter(generation)
                while True:
                    started_at = time.monotonic()
                    try:
                        item = await anext(iterator)
                    except StopAsyncIteration:
                        if summary is not None:
                            summary.record_generation(
                                time.monotonic() - started_at
                            )
                        break
                    except BaseException:
                        if summary is not None:
                            summary.record_generation(
                                time.monotonic() - started_at
                            )
                        raise
                    if summary is not None:
                        summary.record_generation(
                            time.monotonic() - started_at
                        )
                    if isinstance(item, GenerationCompletion):
                        completion = item
                    else:
                        try:
                            pcm = float32_to_pcm_s16le(item)
                        except InvalidWaveform as error:
                            raise EngineError(
                                "engine_error",
                                "VoxCPM2 emitted an invalid waveform",
                            ) from error
                        if summary is not None:
                            summary.record_pcm(pcm)
                        yield pcm
            finally:
                await generation.aclose()

            if completion is None:
                raise EngineError(
                    "engine_error",
                    "VoxCPM2 segment ended without completion",
                )
            prompt_latents = completion.generated_latents
            prompt_text = target_text

    async def _voice_latent(
        self,
        snapshot: VoiceSnapshot,
        role: LatentRole,
    ) -> bytes:
        voice_id = snapshot.metadata.id
        cached = self._voices.get_cached_latent(voice_id, role)
        if cached is not None:
            return cached

        if role == "reference":
            latents = await self._engine.encode_reference(
                snapshot.reference_wav
            )
        else:
            latents = await self._engine.encode_prompt(
                snapshot.reference_wav
            )
        self._voices.cache_latent(voice_id, role, latents)
        return latents


def _control_instruction(options: SynthesisOptions) -> str:
    parts: list[str] = []
    if isinstance(options.voice, DesignVoice):
        parts.append(_clean_control(options.voice.description))
    if options.style is not None:
        parts.append(_clean_control(options.style))
    return "; ".join(part for part in parts if part)


def _clean_control(value: str) -> str:
    return value.translate(str.maketrans("", "", "()（）")).strip()
