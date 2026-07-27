from __future__ import annotations

import asyncio
import io
import wave
from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.testclient import TestClient

from botified_tts.app import Readiness, create_app
from botified_tts.engine import EngineError
from botified_tts.schemas import SynthesisOptions
from botified_tts.voices import InvalidVoice, VoiceMetadata


AUTH = {"Authorization": "Bearer test-secret"}
MODEL = "openbmb/VoxCPM2"
PCM = b"\x01\x00\xff\xff"


class FakeSpeech:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[SynthesisOptions, list[str]]] = []

    async def synthesize(
        self,
        options: SynthesisOptions,
        segments: AsyncIterator[str],
    ) -> AsyncIterator[bytes]:
        received = [segment async for segment in segments]
        self.calls.append((options, received))
        if self.error is not None:
            raise self.error
        yield PCM[:2]
        yield PCM[2:]


class FakeVoices:
    def __init__(self) -> None:
        self.items: dict[str, VoiceMetadata] = {}
        self.create_args: tuple[str, bytes, str, str | None] | None = None

    def create(
        self,
        *,
        name: str,
        source: bytes,
        filename: str,
        prompt_text: str | None = None,
    ) -> VoiceMetadata:
        self.create_args = (name, source, filename, prompt_text)
        if not source:
            raise InvalidVoice("voice reference must not be empty")
        metadata = VoiceMetadata(
            id="voice_" + "1" * 32,
            name=name,
            prompt_text=prompt_text,
            duration_seconds=4.0,
            created_at="2026-07-27T00:00:00Z",
        )
        self.items[metadata.id] = metadata
        return metadata

    def list(self) -> tuple[VoiceMetadata, ...]:
        return tuple(self.items.values())

    def delete(self, voice_id: str) -> bool:
        return self.items.pop(voice_id, None) is not None


def _client(
    *,
    readiness: Readiness | None = None,
    voices: FakeVoices | None = None,
    speech: FakeSpeech | None = None,
) -> TestClient:
    app = create_app(
        api_key="test-secret",
        model=MODEL,
        readiness=readiness or Readiness(ready=True),
        voices=voices or FakeVoices(),
        speech=speech or FakeSpeech(),
    )
    return TestClient(app, raise_server_exceptions=False)


def _error(code: str, message: str, error_type: str) -> dict[str, object]:
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": None,
            "code": code,
        }
    }


def test_health_is_public_and_ready_gate_precedes_protected_work() -> None:
    readiness = Readiness(ready=False)
    with _client(readiness=readiness) as client:
        unavailable = client.get("/health")
        protected = client.post(
            "/v1/speech",
            headers=AUTH,
            json={"text": "你好。"},
        )
        readiness.ready = True
        ready = client.get("/health")
        unauthenticated = client.get("/v1/voices")

    assert unavailable.status_code == 503
    assert unavailable.json() == _error(
        "service_not_ready",
        "Service is not ready",
        "server_error",
    )
    assert protected.status_code == 503
    assert protected.json()["error"]["code"] == "service_not_ready"
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "cuda": True,
        "model": MODEL,
        "sample_rate": 48_000,
    }
    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == _error(
        "invalid_api_key",
        "Invalid authentication credentials",
        "authentication_error",
    )


def test_speech_uses_the_segmenter_and_returns_one_canonical_wav() -> None:
    speech = FakeSpeech()
    with _client(speech=speech) as client:
        response = client.post(
            "/v1/speech",
            headers=AUTH,
            json={
                "text": "第一句。第二句。",
                "style": "自然",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(response.content), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            48_000,
        )
        assert wav.readframes(wav.getnframes()) == PCM
    assert len(speech.calls) == 1
    options, segments = speech.calls[0]
    assert options.style == "自然"
    assert segments == ["第一句。", "第二句。"]


@pytest.mark.parametrize(
    ("body", "speech_error", "status", "code"),
    (
        (b"{", None, 400, "invalid_request"),
        (
            b'{"text":"x","unknown":true}',
            None,
            400,
            "invalid_request",
        ),
        (
            ('{"text":"' + "你" * 2731 + '"}').encode(),
            None,
            413,
            "input_too_large",
        ),
        (
            b'{"text":"hello","voice":{"type":"profile","id":"missing"}}',
            InvalidVoice("voice profile does not exist"),
            404,
            "invalid_voice",
        ),
        (
            b'{"text":"hello"}',
            EngineError("engine_error", "generation failed"),
            500,
            "engine_error",
        ),
    ),
)
def test_speech_maps_public_input_and_engine_errors(
    body: bytes,
    speech_error: Exception | None,
    status: int,
    code: str,
) -> None:
    with _client(speech=FakeSpeech(speech_error)) as client:
        response = client.post(
            "/v1/speech",
            headers={**AUTH, "Content-Type": "application/json"},
            content=body,
        )

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert set(response.json()["error"]) == {
        "message",
        "type",
        "param",
        "code",
    }


class BlockingSpeech:
    def __init__(self) -> None:
        self.entered = 0
        self.full = asyncio.Event()
        self.release = asyncio.Event()

    async def synthesize(
        self,
        _options: SynthesisOptions,
        segments: AsyncIterator[str],
    ) -> AsyncIterator[bytes]:
        async for _ in segments:
            pass
        self.entered += 1
        if self.entered == 16:
            self.full.set()
        await self.release.wait()
        yield PCM


def test_admission_rejects_the_seventeenth_request_and_releases_slots() -> None:
    async def exercise() -> None:
        speech = BlockingSpeech()
        app = create_app(
            api_key="test-secret",
            model=MODEL,
            readiness=Readiness(ready=True),
            voices=FakeVoices(),
            speech=speech,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            active = [
                asyncio.create_task(
                    client.post(
                        "/v1/speech",
                        headers=AUTH,
                        json={"text": f"request {index}"},
                    )
                )
                for index in range(16)
            ]
            await asyncio.wait_for(speech.full.wait(), timeout=2)

            busy = await client.post(
                "/v1/speech",
                headers=AUTH,
                json={"text": "seventeenth"},
            )
            assert busy.status_code == 503
            assert busy.headers["retry-after"] == "1"
            assert busy.json()["error"]["code"] == "service_busy"

            speech.release.set()
            completed = await asyncio.gather(*active)
            assert all(response.status_code == 200 for response in completed)

            released = await client.post(
                "/v1/speech",
                headers=AUTH,
                json={"text": "after release"},
            )
            assert released.status_code == 200

    asyncio.run(exercise())


def test_voice_create_list_delete_lifecycle_uses_framework_multipart() -> None:
    voices = FakeVoices()
    with _client(voices=voices) as client:
        created = client.post(
            "/v1/voices",
            headers=AUTH,
            data={"name": "assistant", "prompt_text": "你好"},
            files={"file": ("reference.wav", b"wave", "audio/wav")},
        )
        listed = client.get("/v1/voices", headers=AUTH)
        deleted = client.delete(
            f"/v1/voices/{created.json()['id']}",
            headers=AUTH,
        )
        missing = client.delete(
            f"/v1/voices/{created.json()['id']}",
            headers=AUTH,
        )

    expected = {
        "id": "voice_" + "1" * 32,
        "name": "assistant",
        "prompt_text": "你好",
        "duration_seconds": 4.0,
        "created_at": "2026-07-27T00:00:00Z",
    }
    assert created.status_code == 201
    assert created.json() == expected
    assert voices.create_args == (
        "assistant",
        b"wave",
        "reference.wav",
        "你好",
    )
    assert listed.status_code == 200
    assert listed.json() == {"object": "list", "data": [expected]}
    assert deleted.status_code == 204
    assert not deleted.content
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "invalid_voice"


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    (
        ("GET", "/not-found", 404, "not_found"),
        ("GET", "/v1/speech", 405, "http_error"),
    ),
)
def test_unknown_routes_and_methods_use_the_error_envelope(
    method: str,
    path: str,
    status: int,
    code: str,
) -> None:
    with _client() as client:
        response = client.request(method, path, headers=AUTH)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert set(response.json()["error"]) == {
        "message",
        "type",
        "param",
        "code",
    }
