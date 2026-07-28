from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator
from contextlib import ExitStack
from typing import Literal

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocket

import botified_tts.streaming as streaming_module
from botified_tts.app import Readiness, create_app
from botified_tts.engine import EngineError
from botified_tts.schemas import SynthesisOptions
from botified_tts.speech import SynthesisSummary


AUTH = {"Authorization": "Bearer test-secret"}
PCM = b"\x01\x00\xff\xff"


class _UnusedVoices:
    def list(self) -> tuple[object, ...]:
        return ()


def _client(*, speech: object) -> TestClient:
    app = create_app(
        api_key="test-secret",
        readiness=Readiness(ready=True),
        voices=_UnusedVoices(),  # type: ignore[arg-type]
        speech=speech,  # type: ignore[arg-type]
    )
    return TestClient(app, raise_server_exceptions=False)


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
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0
        self.segments: list[str] = []

    async def synthesize(
        self,
        _options: SynthesisOptions,
        segments: AsyncIterator[str],
        *,
        summary: SynthesisSummary | None = None,
    ) -> AsyncIterator[bytes]:
        self.calls += 1
        async for segment in segments:
            self.segments.append(segment)
            if summary is not None:
                summary.record_segment()
            if self.error is not None:
                raise self.error
            pcm = len(self.segments).to_bytes(2, "little")
            if summary is not None:
                summary.record_pcm(pcm)
            yield pcm


_SUMMARY_FIELDS = {
    "id",
    "voice_type",
    "mode",
    "accepted_chars",
    "segments",
    "ttfb",
    "audio_duration",
    "rtf",
    "result",
}


def _summaries(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    values: list[dict[str, object]] = []
    for record in caplog.records:
        if record.name != "uvicorn.error.botified_tts":
            continue
        value = json.loads(record.getMessage())
        if set(value) == _SUMMARY_FIELDS:
            values.append(value)
    return values


def test_stream_start_auth_and_http_share_one_admission() -> None:
    speech = StreamingSpeech()
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

        assert speech.calls == 0
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
        *,
        summary: SynthesisSummary | None = None,
    ) -> AsyncIterator[bytes]:
        self.calls += 1
        async for segment in segments:
            self.segments.append(segment)
            if summary is not None:
                summary.record_segment()
            if len(self.segments) == 1:
                self.first_seen.set()
                while not self.release.is_set():
                    await asyncio.sleep(0.001)
            pcm = len(self.segments).to_bytes(2, "little")
            if summary is not None:
                summary.record_pcm(pcm)
            yield pcm


def test_stream_receives_while_generation_blocks_and_finish_drains(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="uvicorn.error.botified_tts")
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
    summaries = _summaries(caplog)
    assert len(summaries) == 1
    summary = summaries[0]
    assert str(summary["id"]).startswith("session_")
    assert summary["voice_type"] == "default"
    assert summary["mode"] is None
    assert summary["accepted_chars"] == 8
    assert summary["segments"] == 2
    assert isinstance(summary["ttfb"], float)
    assert summary["audio_duration"] == pytest.approx(4 / 96_000)
    assert summary["rtf"] == 0.0
    assert summary["result"] == "ok"


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
    monkeypatch.setattr(streaming_module, "_SEGMENT_DEADLINE_SECONDS", 0.25)
    first_append_processed = threading.Event()
    segmenter_class = streaming_module.Segmenter

    class ObservedSegmenter(segmenter_class):
        def append(self, text: str) -> list[str]:
            segments = super().append(text)
            first_append_processed.set()
            return segments

    monkeypatch.setattr(streaming_module, "Segmenter", ObservedSegmenter)
    speech = StreamingSpeech()
    first_seen = threading.Event()
    original = speech.synthesize

    async def observe(
        options: SynthesisOptions,
        segments: AsyncIterator[str],
        *,
        summary: SynthesisSummary | None = None,
    ) -> AsyncIterator[bytes]:
        async for chunk in original(options, segments, summary=summary):
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
        *,
        summary: SynthesisSummary | None = None,
    ) -> AsyncIterator[bytes]:
        self.calls += 1
        try:
            async for segment in segments:
                self.segments.append(segment)
                if summary is not None:
                    summary.record_segment()
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
        *,
        summary: SynthesisSummary | None = None,
    ) -> AsyncIterator[bytes]:
        self.calls += 1
        try:
            async for segment in segments:
                self.segments.append(segment)
                if summary is not None:
                    summary.record_segment()
                    summary.record_pcm(PCM)
                yield PCM
                await asyncio.Event().wait()
        finally:
            self.closed.set()


class DrainingCancellableSpeech:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.closed = threading.Event()
        self.segments: list[str] = []

    async def synthesize(
        self,
        _options: SynthesisOptions,
        segments: AsyncIterator[str],
        *,
        summary: SynthesisSummary | None = None,
    ) -> AsyncIterator[bytes]:
        try:
            async for segment in segments:
                self.segments.append(segment)
                self.started.set()
                await asyncio.sleep(0.1)
                yield PCM
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


def test_stream_cancel_after_finish_stops_draining_speech() -> None:
    speech = DrainingCancellableSpeech()
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            websocket.send_json({"type": "append", "text": "等待结束"})
            websocket.send_json({"type": "finish"})
            assert speech.started.wait(timeout=1)

            websocket.send_json({"type": "cancel"})
            assert websocket.receive_json() == {
                "type": "done",
                "cancelled": True,
            }

    assert speech.closed.wait(timeout=1)
    assert speech.segments == ["等待结束"]


def test_stream_send_timeout_closes_speech(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(streaming_module, "_SEND_TIMEOUT_SECONDS", 0.02)
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
    monkeypatch.setattr(streaming_module, "_IDLE_TIMEOUT_SECONDS", 0.02)
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


def test_stream_engine_error_is_one_terminal_error_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="uvicorn.error.botified_tts")
    speech = StreamingSpeech(
        EngineError("engine_error", "secret engine detail")
    )
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
    summaries = _summaries(caplog)
    assert len(summaries) == 1
    assert summaries[0]["result"] == "engine_error"
    assert summaries[0]["ttfb"] is None
    assert summaries[0]["audio_duration"] is None
    assert summaries[0]["rtf"] is None


def test_stream_ready_send_does_not_extend_active_idle_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(streaming_module, "_IDLE_TIMEOUT_SECONDS", 0.08)
    original_send_json = WebSocket.send_json

    async def delay_ready(
        websocket: WebSocket,
        value: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        if isinstance(value, dict) and value.get("type") == "ready":
            await asyncio.sleep(0.12)
        await original_send_json(websocket, value, *args, **kwargs)

    monkeypatch.setattr(WebSocket, "send_json", delay_ready)
    with _client(speech=SinkSpeech()) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            websocket.send_json({"type": "start"})
            assert websocket.receive_json()["type"] == "ready"
            started = time.monotonic()
            assert websocket.receive_json() == {
                "type": "done",
                "cancelled": True,
            }

    assert time.monotonic() - started < 0.04


def test_coordinate_prefers_precompleted_generation_failure() -> None:
    async def exercise() -> None:
        session = object.__new__(streaming_module._StreamingSession)

        async def receive() -> Literal["finish", "cancel"]:
            return "cancel"

        async def generate() -> None:
            raise EngineError("engine_error", "generation failed")

        receive_task = asyncio.create_task(receive())
        generate_task = asyncio.create_task(generate())
        await asyncio.sleep(0)
        with pytest.raises(EngineError):
            await session._coordinate(
                receive_task,
                generate_task,
                asyncio.Event(),
                asyncio.Event(),
            )

    asyncio.run(exercise())


def test_coordinate_uses_precompleted_receive_after_normal_generation() -> None:
    async def exercise() -> None:
        session = object.__new__(streaming_module._StreamingSession)

        async def receive() -> Literal["finish", "cancel"]:
            return "cancel"

        async def generate() -> None:
            return None

        receive_task = asyncio.create_task(receive())
        generate_task = asyncio.create_task(generate())
        await asyncio.sleep(0)
        assert await session._coordinate(
            receive_task,
            generate_task,
            asyncio.Event(),
            asyncio.Event(),
        )

    asyncio.run(exercise())


class BlockingCloseSpeech:
    def __init__(self) -> None:
        self.close_started = threading.Event()

    def synthesize(
        self,
        _options: SynthesisOptions,
        segments: AsyncIterator[str],
        *,
        summary: SynthesisSummary | None = None,
    ) -> BlockingCloseStream:
        return BlockingCloseStream(segments, self.close_started)


class BlockingCloseStream:
    def __init__(
        self,
        segments: AsyncIterator[str],
        close_started: threading.Event,
    ) -> None:
        self._segments = segments
        self._close_started = close_started
        self._yielded = False

    def __aiter__(self) -> BlockingCloseStream:
        return self

    async def __anext__(self) -> bytes:
        if self._yielded:
            raise StopAsyncIteration
        await anext(self._segments)
        self._yielded = True
        return PCM

    async def aclose(self) -> None:
        self._close_started.set()
        await asyncio.Event().wait()


def test_stream_blocking_close_is_bounded_and_releases_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(streaming_module, "_CLEANUP_TIMEOUT_SECONDS", 0.02)
    speech = BlockingCloseSpeech()
    with _client(speech=speech) as client:
        with client.websocket_connect(
            "/v1/speech/stream",
            headers=AUTH,
        ) as websocket:
            _start_stream(websocket)
            websocket.send_json({"type": "append", "text": "第一句。"})
            websocket.send_json({"type": "finish"})
            assert websocket.receive_bytes() == PCM
            event = websocket.receive_json()
            assert event["type"] == "error"
            assert event["error"]["code"] == "engine_error"

        assert speech.close_started.wait(timeout=1)
        released = client.post(
            "/v1/speech",
            headers=AUTH,
            json={"text": "released"},
        )
        assert released.status_code == 200
