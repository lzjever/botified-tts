from __future__ import annotations

from collections.abc import AsyncIterator

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
    ) -> AsyncIterator[bytes]:
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
                async for item in generation:
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
