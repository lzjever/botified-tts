from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import numpy as np
import pytest

from botified_tts.engine import GenerationCompletion
from botified_tts.schemas import (
    DesignVoice,
    InvalidSynthesisOptions,
    ProfileVoice,
    SynthesisOptions,
)
from botified_tts.speech import SpeechService
from botified_tts.voices import (
    InvalidVoice,
    VoiceMetadata,
    VoiceSnapshot,
)


VOICE_ID = "voice_" + "a" * 32
WAVEFORM = np.zeros(7680, dtype=np.float32)


@dataclass
class StreamPlan:
    items: list[object]
    close_error: Exception | None = None


class FakeStream:
    def __init__(self, plan: StreamPlan) -> None:
        self._plan = plan
        self._offset = 0
        self.closed = False

    def __aiter__(self) -> FakeStream:
        return self

    async def __anext__(self) -> object:
        if self._offset >= len(self._plan.items):
            raise StopAsyncIteration
        item = self._plan.items[self._offset]
        self._offset += 1
        if isinstance(item, BaseException):
            raise item
        return item

    async def aclose(self) -> None:
        self.closed = True
        if self._plan.close_error is not None:
            raise self._plan.close_error


class FakeEngine:
    def __init__(
        self,
        *,
        plans: list[StreamPlan] | None = None,
        on_encode_reference: Callable[[bytes], None] | None = None,
    ) -> None:
        self._plans = plans or []
        self._on_encode_reference = on_encode_reference
        self.reference_audio: list[bytes] = []
        self.prompt_audio: list[bytes] = []
        self.generate_calls: list[dict[str, object]] = []
        self.streams: list[FakeStream] = []

    async def encode_reference(self, audio: bytes) -> bytes:
        self.reference_audio.append(audio)
        if self._on_encode_reference is not None:
            self._on_encode_reference(audio)
        return b"reference-latents-" + audio

    async def encode_prompt(self, audio: bytes) -> bytes:
        self.prompt_audio.append(audio)
        return b"prompt-latents-" + audio

    def generate(self, **kwargs: object) -> FakeStream:
        assert all(stream.closed for stream in self.streams)
        call_index = len(self.generate_calls)
        self.generate_calls.append(kwargs)
        if call_index < len(self._plans):
            plan = self._plans[call_index]
        else:
            plan = StreamPlan(
                [
                    WAVEFORM,
                    GenerationCompletion(
                        generated_latents=f"generated-{call_index}".encode()
                    ),
                ]
            )
        stream = FakeStream(plan)
        self.streams.append(stream)
        return stream


class FakeVoiceStore:
    def __init__(self, snapshot: VoiceSnapshot | None = None) -> None:
        self._snapshots: dict[str, VoiceSnapshot] = {}
        if snapshot is not None:
            self._snapshots[snapshot.metadata.id] = snapshot
        self._cache: dict[tuple[str, str], bytes] = {}
        self.snapshot_calls: list[str] = []

    def get_snapshot(self, voice_id: str) -> VoiceSnapshot | None:
        self.snapshot_calls.append(voice_id)
        return self._snapshots.get(voice_id)

    def get_cached_latent(self, voice_id: str, role: str) -> bytes | None:
        return self._cache.get((voice_id, role))

    def cache_latent(
        self,
        voice_id: str,
        role: str,
        latents: bytes,
    ) -> bool:
        if voice_id not in self._snapshots:
            return False
        self._cache[(voice_id, role)] = latents
        return True

    def delete(self, voice_id: str) -> bool:
        if self._snapshots.pop(voice_id, None) is None:
            return False
        for key in tuple(self._cache):
            if key[0] == voice_id:
                del self._cache[key]
        return True

    def restore(self, snapshot: VoiceSnapshot) -> None:
        self._snapshots[snapshot.metadata.id] = snapshot


def _snapshot(
    *,
    audio: bytes = b"reference-wav",
    prompt_text: str | None = "exact transcript",
) -> VoiceSnapshot:
    return VoiceSnapshot(
        metadata=VoiceMetadata(
            id=VOICE_ID,
            name="assistant",
            prompt_text=prompt_text,
            duration_seconds=5.0,
            created_at="2026-07-27T00:00:00Z",
        ),
        reference_wav=audio,
    )


async def _segments(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


async def _collect(
    service: SpeechService,
    options: SynthesisOptions,
    *segments: str,
) -> list[bytes]:
    return [
        chunk
        async for chunk in service.synthesize(
            options,
            _segments(*segments),
        )
    ]


@pytest.mark.parametrize(
    (
        "options",
        "expected_target",
        "expected_prompt_latents",
        "expected_prompt_text",
        "expected_reference_latents",
        "reference_encodes",
        "prompt_encodes",
    ),
    [
        (
            SynthesisOptions(voice=None, mode=None, style=None),
            "hello",
            None,
            "",
            None,
            0,
            0,
        ),
        (
            SynthesisOptions(
                voice=None,
                mode=None,
                style="  (calm （bright）)  ",
            ),
            "(calm bright)hello",
            None,
            "",
            None,
            0,
            0,
        ),
        (
            SynthesisOptions(
                voice=DesignVoice(description=" （warm） "),
                mode=None,
                style=" (slow) ",
            ),
            "(warm; slow)hello",
            None,
            "",
            None,
            0,
            0,
        ),
        (
            SynthesisOptions(
                voice=ProfileVoice(id=VOICE_ID),
                mode="controllable",
                style=" (friendly) ",
            ),
            "(friendly)hello",
            None,
            "",
            b"reference-latents-reference-wav",
            1,
            0,
        ),
        (
            SynthesisOptions(
                voice=ProfileVoice(id=VOICE_ID),
                mode="faithful",
                style=None,
            ),
            "hello",
            b"prompt-latents-reference-wav",
            "exact transcript",
            b"reference-latents-reference-wav",
            1,
            1,
        ),
    ],
)
def test_synthesis_conditioning_matrix(
    options: SynthesisOptions,
    expected_target: str,
    expected_prompt_latents: bytes | None,
    expected_prompt_text: str,
    expected_reference_latents: bytes | None,
    reference_encodes: int,
    prompt_encodes: int,
) -> None:
    voices = FakeVoiceStore(_snapshot())
    engine = FakeEngine()
    service = SpeechService(engine, voices)

    output = asyncio.run(_collect(service, options, "hello"))

    assert len(output) == 1
    assert output[0] == b"\0" * (7680 * 2)
    assert engine.generate_calls == [
        {
            "target_text": expected_target,
            "prompt_latents": expected_prompt_latents,
            "prompt_text": expected_prompt_text,
            "ref_audio_latents": expected_reference_latents,
        }
    ]
    assert len(engine.reference_audio) == reference_encodes
    assert len(engine.prompt_audio) == prompt_encodes
    assert engine.streams[0].closed


@pytest.mark.parametrize(
    ("voices", "options", "error_type"),
    [
        (
            FakeVoiceStore(),
            SynthesisOptions(
                voice=ProfileVoice(id=VOICE_ID),
                mode="controllable",
                style=None,
            ),
            InvalidVoice,
        ),
        (
            FakeVoiceStore(_snapshot(prompt_text=None)),
            SynthesisOptions(
                voice=ProfileVoice(id=VOICE_ID),
                mode="faithful",
                style=None,
            ),
            InvalidSynthesisOptions,
        ),
    ],
)
def test_profile_must_exist_and_faithful_requires_exact_transcript(
    voices: FakeVoiceStore,
    options: SynthesisOptions,
    error_type: type[Exception],
) -> None:
    engine = FakeEngine()
    service = SpeechService(engine, voices)

    with pytest.raises(error_type):
        asyncio.run(_collect(service, options, "hello"))

    assert engine.generate_calls == []


def test_segments_are_serial_and_use_only_last_complete_continuation() -> None:
    engine = FakeEngine()
    service = SpeechService(engine, FakeVoiceStore())
    options = SynthesisOptions(
        voice=None,
        mode=None,
        style=" (calm) ",
    )

    output = asyncio.run(_collect(service, options, "one", "two", "three"))

    assert len(output) == 3
    assert engine.generate_calls == [
        {
            "target_text": "(calm)one",
            "prompt_latents": None,
            "prompt_text": "",
            "ref_audio_latents": None,
        },
        {
            "target_text": "two",
            "prompt_latents": b"generated-0",
            "prompt_text": "(calm)one",
            "ref_audio_latents": None,
        },
        {
            "target_text": "three",
            "prompt_latents": b"generated-1",
            "prompt_text": "two",
            "ref_audio_latents": None,
        },
    ]
    assert all(stream.closed for stream in engine.streams)


@pytest.mark.parametrize(
    "plan",
    [
        StreamPlan([WAVEFORM, RuntimeError("generation failed")]),
        StreamPlan(
            [
                WAVEFORM,
                GenerationCompletion(generated_latents=b"uncommitted"),
            ],
            close_error=RuntimeError("close failed"),
        ),
    ],
)
def test_error_or_close_failure_does_not_start_the_next_segment(
    plan: StreamPlan,
) -> None:
    engine = FakeEngine(plans=[plan])
    service = SpeechService(engine, FakeVoiceStore())
    options = SynthesisOptions(voice=None, mode=None, style=None)

    with pytest.raises(RuntimeError):
        asyncio.run(_collect(service, options, "one", "two"))

    assert len(engine.generate_calls) == 1
    assert engine.streams[0].closed


def test_consumer_close_closes_active_generation_without_starting_next() -> None:
    engine = FakeEngine()
    service = SpeechService(engine, FakeVoiceStore())
    options = SynthesisOptions(voice=None, mode=None, style=None)

    async def close_after_one_chunk() -> None:
        stream = service.synthesize(options, _segments("one", "two"))
        assert await stream.__anext__() == b"\0" * (7680 * 2)
        await stream.aclose()

    asyncio.run(close_after_one_chunk())

    assert len(engine.generate_calls) == 1
    assert engine.streams[0].closed


def test_snapshot_survives_delete_and_late_latents_are_not_cached() -> None:
    old_snapshot = _snapshot(audio=b"old-wav")
    voices = FakeVoiceStore(old_snapshot)

    def delete_during_encode(audio: bytes) -> None:
        if audio == b"old-wav":
            assert voices.delete(VOICE_ID)

    engine = FakeEngine(on_encode_reference=delete_during_encode)
    service = SpeechService(engine, voices)
    options = SynthesisOptions(
        voice=ProfileVoice(id=VOICE_ID),
        mode="controllable",
        style=None,
    )

    first_output = asyncio.run(_collect(service, options, "first"))

    assert first_output
    assert engine.reference_audio == [b"old-wav"]
    assert voices.get_cached_latent(VOICE_ID, "reference") is None

    voices.restore(_snapshot(audio=b"new-wav"))
    asyncio.run(_collect(service, options, "second"))
    asyncio.run(_collect(service, options, "third"))

    assert engine.reference_audio == [b"old-wav", b"new-wav"]
    assert (
        voices.get_cached_latent(VOICE_ID, "reference")
        == b"reference-latents-new-wav"
    )
