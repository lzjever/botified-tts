from __future__ import annotations

import asyncio
import io
import threading
import time
import wave
from collections.abc import AsyncIterator
from contextlib import ExitStack

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

import botified_tts.app as app_module
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
    assert unavailable.json() == {
        "status": "not_ready",
        "cuda": True,
        "model": MODEL,
        "sample_rate": 48_000,
    }
    assert protected.status_code == 500
    assert protected.json() == _error(
        "engine_error",
        "Service is not ready",
        "server_error",
    )
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


@pytest.mark.parametrize(
    "headers",
    (
        {"Authorization": "Basic test-secret"},
        [
            ("Authorization", "Bearer test-secret"),
            ("Authorization", "Bearer test-secret"),
        ],
    ),
    ids=("wrong-scheme", "duplicate"),
)
def test_invalid_authorization_is_rejected_before_business_logic(
    headers: dict[str, str] | list[tuple[str, str]],
) -> None:
    speech = FakeSpeech(RuntimeError("business must not run"))
    with _client(speech=speech) as client:
        response = client.post(
            "/v1/speech",
            headers=headers,
            json={"text": "你好。"},
        )

    assert response.status_code == 401
    assert response.json() == _error(
        "invalid_api_key",
        "Invalid authentication credentials",
        "authentication_error",
    )
    assert speech.calls == []


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


def test_unexpected_exception_is_fixed_engine_error_without_details() -> None:
    secret = "database-password-must-not-leak"
    with _client(speech=FakeSpeech(RuntimeError(secret))) as client:
        response = client.post(
            "/v1/speech",
            headers=AUTH,
            json={"text": "hello"},
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "engine_error"
    assert response.json()["error"]["type"] == "server_error"
    assert secret not in response.text


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


def test_voice_multipart_rejects_empty_upload_and_framework_limits() -> None:
    voices = FakeVoices()
    with _client(voices=voices) as client:
        empty = client.post(
            "/v1/voices",
            headers=AUTH,
            data={"name": "assistant"},
            files={"file": ("reference.wav", b"", "audio/wav")},
        )
        too_many_files = client.post(
            "/v1/voices",
            headers=AUTH,
            data={"name": "assistant"},
            files=[
                ("file", ("one.wav", b"one", "audio/wav")),
                ("file", ("two.wav", b"two", "audio/wav")),
            ],
        )
        too_many_fields = client.post(
            "/v1/voices",
            headers=AUTH,
            files=[
                ("name", (None, "assistant")),
                ("prompt_text", (None, "transcript")),
                ("name", (None, "duplicate")),
                ("file", ("reference.wav", b"wave", "audio/wav")),
            ],
        )

    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "invalid_request"
    for response in (too_many_files, too_many_fields):
        assert response.status_code == 400
        assert response.json() == _error(
            "invalid_request",
            "Invalid request",
            "invalid_request_error",
        )
    assert voices.create_args == ("assistant", b"", "reference.wav", None)


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("GET", "/not-found"),
        ("GET", "/v1/speech"),
    ),
)
def test_unknown_routes_and_methods_are_generic_invalid_requests(
    method: str,
    path: str,
) -> None:
    with _client() as client:
        response = client.request(method, path, headers=AUTH)

    assert response.status_code == 400
    assert response.json() == _error(
        "invalid_request",
        "Invalid request",
        "invalid_request_error",
    )


def _start_stream(websocket: WebSocket) -> None:
    websocket.send_json({"type": "start"})
    assert websocket.receive_json() == {
        "type": "ready",
        "audio": {
            "encoding": "pcm_s16le",
            "sample_rate": 48_000,
            "channels": 1,
        },
    }


class StreamingSpeech:
    def __init__(self) -> None:
        self.calls = 0
        self.segments: list[str] = []

    async def synthesize(
        self,
        _options: SynthesisOptions,
        segments: AsyncIterator[str],
    ) -> AsyncIterator[bytes]:
        self.calls += 1
        async for segment in segments:
            self.segments.append(segment)
            yield len(self.segments).to_bytes(2, "little")


def test_stream_start_auth_and_http_share_one_admission() -> None:
    speech = FakeSpeech()
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers={"Authorization": "Basic test-secret"},
        ) as websocket:
            assert websocket.receive_json()["error"]["code"] == "invalid_api_key"

        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            websocket.send_json({"type": "append", "text": "not started"})
            assert websocket.receive_json()["error"]["code"] == "invalid_request"

        with ExitStack() as stack:
            sessions = [
                stack.enter_context(
                    client.websocket_connect(
                        "/v1/speech/stream",
                        headers=AUTH,
                    )
                )
                for _ in range(16)
            ]
            for websocket in sessions:
                _start_stream(websocket)

            with client.websocket_connect(
                "/v1/speech/stream",
                headers=AUTH,
            ) as websocket:
                websocket.send_json({"type": "start"})
                assert websocket.receive_json()["error"]["code"] == (
                    "service_busy"
                )

            busy = client.post(
                "/v1/speech",
                headers=AUTH,
                json={"text": "shared limit"},
            )
            assert busy.status_code == 503
            assert busy.headers["retry-after"] == "1"
            assert busy.json()["error"]["code"] == "service_busy"

            for websocket in sessions:
                websocket.send_json({"type": "cancel"})
            for websocket in sessions:
                assert websocket.receive_json() == {
                    "type": "done",
                    "cancelled": True,
                }

        released = client.post(
            "/v1/speech",
            headers=AUTH,
            json={"text": "released"},
        )
        assert released.status_code == 200

    assert len(speech.calls) == 1


class BlockingFirstSegmentSpeech:
    def __init__(self) -> None:
        self.calls = 0
        self.segments: list[str] = []
        self.first_seen = threading.Event()
        self.release = threading.Event()

    async def synthesize(
        self,
        _options: SynthesisOptions,
        segments: AsyncIterator[str],
    ) -> AsyncIterator[bytes]:
        self.calls += 1
        async for segment in segments:
            self.segments.append(segment)
            if len(self.segments) == 1:
                self.first_seen.set()
                while not self.release.is_set():
                    await asyncio.sleep(0.001)
            yield len(self.segments).to_bytes(2, "little")


def test_stream_receives_while_generation_blocks_and_finish_drains() -> None:
    speech = BlockingFirstSegmentSpeech()
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            websocket.send_json({"type": "append", "text": "第一句。"})
            assert speech.first_seen.wait(timeout=1)

            websocket.send_json({"type": "append", "text": "第二句。"})
            websocket.send_json({"type": "finish"})
            speech.release.set()

            assert websocket.receive_bytes() == b"\x01\x00"
            assert websocket.receive_bytes() == b"\x02\x00"
            assert websocket.receive_json() == {
                "type": "done",
                "cancelled": False,
            }

    assert speech.calls == 1
    assert speech.segments == ["第一句。", "第二句。"]


def test_stream_flush_keeps_one_speech_session() -> None:
    speech = StreamingSpeech()
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            websocket.send_json({"type": "append", "text": "短文本"})
            websocket.send_json({"type": "flush"})
            assert websocket.receive_bytes() == b"\x01\x00"

            websocket.send_json({"type": "append", "text": "尾部"})
            websocket.send_json({"type": "finish"})
            assert websocket.receive_bytes() == b"\x02\x00"
            assert websocket.receive_json() == {
                "type": "done",
                "cancelled": False,
            }

    assert speech.calls == 1
    assert speech.segments == ["短文本", "尾部"]


def test_stream_deadline_is_not_reset_by_later_append(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "_SEGMENT_DEADLINE_SECONDS", 0.25)
    first_append_processed = threading.Event()
    segmenter_class = app_module.Segmenter

    class ObservedSegmenter(segmenter_class):
        def append(self, text: str) -> list[str]:
            segments = super().append(text)
            first_append_processed.set()
            return segments

    monkeypatch.setattr(app_module, "Segmenter", ObservedSegmenter)
    speech = StreamingSpeech()
    first_seen = threading.Event()
    original = speech.synthesize

    async def observe(
        options: SynthesisOptions,
        segments: AsyncIterator[str],
    ) -> AsyncIterator[bytes]:
        async for chunk in original(options, segments):
            first_seen.set()
            yield chunk

    speech.synthesize = observe  # type: ignore[method-assign]
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            websocket.send_json({"type": "append", "text": "abcdefghijkl"})
            assert first_append_processed.wait(timeout=1)
            time.sleep(0.15)
            websocket.send_json({"type": "append", "text": "m"})

            assert first_seen.wait(timeout=0.18)
            assert websocket.receive_bytes() == b"\x01\x00"
            websocket.send_json({"type": "finish"})
            assert websocket.receive_json() == {
                "type": "done",
                "cancelled": False,
            }

    assert speech.segments == ["abcdefghijklm"]


class SinkSpeech:
    def __init__(self) -> None:
        self.calls = 0
        self.segments: list[str] = []
        self.closed = threading.Event()

    async def synthesize(
        self,
        _options: SynthesisOptions,
        segments: AsyncIterator[str],
    ) -> AsyncIterator[bytes]:
        self.calls += 1
        try:
            async for segment in segments:
                self.segments.append(segment)
                await asyncio.sleep(0)
        finally:
            self.closed.set()
        if False:
            yield b""


def test_stream_cumulative_utf8_budget_rejects_append_atomically() -> None:
    speech = SinkSpeech()
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            for _ in range(4):
                websocket.send_json(
                    {"type": "append", "text": "a" * (16 * 1024)}
                )
            websocket.send_json({"type": "append", "text": "b"})

            event = websocket.receive_json()
            assert event["type"] == "error"
            assert event["error"]["code"] == "input_too_large"

    assert speech.calls == 1
    assert speech.closed.wait(timeout=1)
    assert "b" not in "".join(speech.segments)


class CancellableSpeech:
    def __init__(self) -> None:
        self.calls = 0
        self.segments: list[str] = []
        self.closed = threading.Event()

    async def synthesize(
        self,
        _options: SynthesisOptions,
        segments: AsyncIterator[str],
    ) -> AsyncIterator[bytes]:
        self.calls += 1
        try:
            async for segment in segments:
                self.segments.append(segment)
                yield PCM
                await asyncio.Event().wait()
        finally:
            self.closed.set()


def test_stream_cancel_closes_active_speech_and_discards_queue() -> None:
    speech = CancellableSpeech()
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            websocket.send_json({"type": "append", "text": "第一句。"})
            assert websocket.receive_bytes() == PCM
            websocket.send_json({"type": "append", "text": "第二句。"})
            websocket.send_json({"type": "cancel"})

            assert websocket.receive_json() == {
                "type": "done",
                "cancelled": True,
            }

    assert speech.closed.wait(timeout=1)
    assert speech.segments == ["第一句。"]


def test_stream_send_timeout_closes_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "_SEND_TIMEOUT_SECONDS", 0.02)
    speech = CancellableSpeech()

    async def block_send(_: WebSocket, __: bytes) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(WebSocket, "send_bytes", block_send)
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            websocket.send_json({"type": "append", "text": "第一句。"})

            event = websocket.receive_json()
            assert event["type"] == "error"
            assert event["error"]["code"] == "client_too_slow"

    assert speech.closed.wait(timeout=1)


def test_stream_idle_cancels_without_waiting_during_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "_IDLE_TIMEOUT_SECONDS", 0.02)
    speech = SinkSpeech()
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            assert websocket.receive_json() == {
                "type": "done",
                "cancelled": True,
            }

        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            assert websocket.receive_json() == {
                "type": "done",
                "cancelled": True,
            }

    assert speech.closed.wait(timeout=1)

    draining = BlockingFirstSegmentSpeech()
    with _client(speech=draining) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            websocket.send_json({"type": "append", "text": "第一句。"})
            assert draining.first_seen.wait(timeout=1)
            websocket.send_json({"type": "finish"})
            time.sleep(0.05)
            draining.release.set()

            assert websocket.receive_bytes() == b"\x01\x00"
            assert websocket.receive_json() == {
                "type": "done",
                "cancelled": False,
            }


def test_stream_engine_error_is_one_terminal_error_event() -> None:
    speech = FakeSpeech(EngineError("engine_error", "secret engine detail"))
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            websocket.send_json({"type": "append", "text": "第一句。"})
            websocket.send_json({"type": "finish"})

            event = websocket.receive_json()
            assert event == {
                "type": "error",
                "error": {
                    "code": "engine_error",
                    "message": "Speech synthesis failed",
                },
            }
            assert "secret engine detail" not in str(event)
