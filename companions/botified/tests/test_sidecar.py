from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from botified_tts_companion.cli import (
    AplaySink,
    PreviewSidecar,
    build_start_message,
    load_tts_environment,
    parse_args,
)


class FakeSink:
    def __init__(self) -> None:
        self.audio: list[bytes] = []
        self.finished = False
        self.cancelled = False

    async def write(self, pcm: bytes) -> None:
        self.audio.append(pcm)

    async def finish(self) -> None:
        self.finished = True

    async def cancel(self) -> None:
        self.cancelled = True


class BlockingCancelSink(FakeSink):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_started = asyncio.Event()
        self.cancel_release = asyncio.Event()

    async def cancel(self) -> None:
        self.cancelled = True
        self.cancel_started.set()
        await self.cancel_release.wait()


async def frames(*values: dict[str, object]) -> AsyncIterator[dict[str, object]]:
    for value in values:
        yield value


def observe_result() -> dict[str, object]:
    return {
        "op": "observe_result",
        "id": "tts-preview",
        "ok": True,
        "observing": True,
        "delivery": "stream_text",
        "min_batch_chars": 1,
    }


def assistant_text(
    observation_id: str,
    provider_request_id: str,
    text: str,
    *,
    chunk_index: int = 0,
    is_last_chunk: bool = True,
) -> dict[str, object]:
    return {
        "op": "observe",
        "id": observation_id,
        "delivery": "stream_text",
        "source": "assistant",
        "event": "text",
        "text": text,
        "chunk_index": chunk_index,
        "is_last_chunk": is_last_chunk,
        "provider_request_id": provider_request_id,
    }


def assistant_terminal(
    provider_request_id: str,
    event: str,
) -> dict[str, object]:
    return {
        "op": "observe",
        "id": f"terminal-{provider_request_id}",
        "delivery": "stream_text",
        "source": "assistant",
        "event": event,
        "provider_request_id": provider_request_id,
    }


def user_text() -> dict[str, object]:
    return {
        "op": "observe",
        "id": "user-1",
        "delivery": "stream_text",
        "source": "user",
        "event": "text",
        "text": "请停一下",
        "chunk_index": 0,
        "is_last_chunk": True,
    }


async def send_ready(websocket: ServerConnection) -> None:
    assert json.loads(await websocket.recv()) == {"type": "start"}
    await websocket.send(
        json.dumps(
            {
                "type": "ready",
                "audio": {
                    "encoding": "pcm_s16le",
                    "sample_rate": 48_000,
                    "channels": 1,
                },
            }
        )
    )


async def run_fake_server(
    scenario: Any,
) -> list[str]:
    sessions: list[list[dict[str, object]]] = []
    authorizations: list[str] = []

    async def handler(websocket: ServerConnection) -> None:
        authorizations.append(websocket.request.headers["Authorization"])
        received: list[dict[str, object]] = []
        sessions.append(received)
        first = json.loads(await websocket.recv())
        received.append(first)
        await websocket.send(
            json.dumps(
                {
                    "type": "ready",
                    "audio": {
                        "encoding": "pcm_s16le",
                        "sample_rate": 48_000,
                        "channels": 1,
                    },
                }
            )
        )
        async for raw in websocket:
            message = json.loads(raw)
            received.append(message)
            if message["type"] == "append":
                await websocket.send(b"\x01\x02")
            elif message["type"] == "finish":
                await websocket.send(json.dumps({"type": "done", "cancelled": False}))
                return
            elif message["type"] == "cancel":
                await websocket.send(json.dumps({"type": "done", "cancelled": True}))
                return

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await scenario(f"ws://127.0.0.1:{port}/v1/speech/stream", sessions)
    return authorizations


def test_reassembles_preview_and_switches_provider_with_full_duplex_audio() -> None:
    async def scenario(
        url: str,
        sessions: list[list[dict[str, object]]],
    ) -> None:
        sinks: list[FakeSink] = []
        emitted: list[dict[str, object]] = []

        async def open_sink() -> FakeSink:
            sink = FakeSink()
            sinks.append(sink)
            return sink

        start_message = build_start_message(
            voice_id="voice_" + "1" * 32,
            design=None,
            mode="controllable",
            style="calm and conversational",
        )
        sidecar = PreviewSidecar(
            tts_url=url,
            api_key="secret",
            start_message=start_message,
            sink_factory=open_sink,
            emit=emitted.append,
        )

        async def normal_finish_frames() -> AsyncIterator[dict[str, object]]:
            yield observe_result()
            yield assistant_text(
                "obs-1",
                "provider-1",
                "你",
                is_last_chunk=False,
            )
            yield assistant_text(
                "obs-1",
                "provider-1",
                "好。",
                chunk_index=1,
            )
            while len(sinks) != 1:
                await asyncio.sleep(0)
            yield assistant_text("obs-2", "provider-2", "新的回答。")
            while len(sinks) != 2:
                await asyncio.sleep(0)
            yield assistant_terminal("provider-1", "error")
            yield assistant_terminal("provider-2", "done")
            while len(sinks) != 2 or not sinks[1].finished:
                await asyncio.sleep(0)

        await sidecar.run(normal_finish_frames())

        assert len(sessions) == 2
        assert sessions[0] == [
            {
                "type": "start",
                "voice": {
                    "type": "profile",
                    "id": "voice_" + "1" * 32,
                },
                "mode": "controllable",
                "style": "calm and conversational",
            },
            {"type": "append", "text": "你好。"},
            {"type": "cancel"},
        ]
        assert sessions[1] == [
            {
                "type": "start",
                "voice": {
                    "type": "profile",
                    "id": "voice_" + "1" * 32,
                },
                "mode": "controllable",
                "style": "calm and conversational",
            },
            {"type": "append", "text": "新的回答。"},
            {"type": "finish"},
        ]
        assert sinks[0].cancelled
        assert sinks[1].finished
        assert sinks[1].audio == [b"\x01\x02"]
        assert emitted == [
            {
                "op": "observe_request",
                "id": "tts-preview",
                "delivery": "stream_text",
                "min_batch_chars": 1,
            }
        ]

    async def exercise() -> None:
        authorizations = await run_fake_server(scenario)
        assert authorizations == ["Bearer secret", "Bearer secret"]

    asyncio.run(exercise())


def test_user_interrupt_and_stdin_eof_cancel_active_speech() -> None:
    async def scenario(
        url: str,
        sessions: list[list[dict[str, object]]],
    ) -> None:
        sinks: list[FakeSink] = []

        async def open_sink() -> FakeSink:
            sink = FakeSink()
            sinks.append(sink)
            return sink

        sidecar = PreviewSidecar(
            tts_url=url,
            api_key="secret",
            sink_factory=open_sink,
            emit=lambda _: None,
        )

        async def interrupting_frames() -> AsyncIterator[dict[str, object]]:
            yield observe_result()
            yield assistant_text("obs-1", "provider-1", "第一条回答。")
            while len(sinks) != 1:
                await asyncio.sleep(0)
            yield user_text()
            yield assistant_text("obs-2", "provider-2", "第二条回答。")
            while len(sinks) != 2:
                await asyncio.sleep(0)

        await sidecar.run(interrupting_frames())

        assert [session[-1] for session in sessions] == [
            {"type": "cancel"},
            {"type": "cancel"},
        ]
        assert all(sink.cancelled for sink in sinks)

    asyncio.run(run_fake_server(scenario))


def test_user_interrupt_cancels_slow_handshake_before_player_opens() -> None:
    async def exercise() -> None:
        start_received = asyncio.Event()
        connection_closed = asyncio.Event()
        sink_opened = False

        async def handler(websocket: ServerConnection) -> None:
            assert json.loads(await websocket.recv()) == {"type": "start"}
            start_received.set()
            await websocket.wait_closed()
            connection_closed.set()

        async def interrupt_during_handshake() -> AsyncIterator[dict[str, object]]:
            yield observe_result()
            yield assistant_text("obs-1", "provider-1", "请开始播放。")
            await start_received.wait()
            yield user_text()

        async def open_sink() -> FakeSink:
            nonlocal sink_opened
            sink_opened = True
            return FakeSink()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            sidecar = PreviewSidecar(
                tts_url=f"ws://127.0.0.1:{port}/v1/speech/stream",
                api_key="secret",
                sink_factory=open_sink,
                emit=lambda _: None,
            )
            await asyncio.wait_for(
                sidecar.run(interrupt_during_handshake()),
                timeout=1,
            )
            await asyncio.wait_for(connection_closed.wait(), timeout=1)

        assert not sink_opened

    asyncio.run(exercise())


def test_user_interrupt_wins_when_append_and_input_complete_together() -> None:
    class BlockingAppendWebsocket:
        def __init__(self) -> None:
            self.append_started = asyncio.Event()
            self.append_release = asyncio.Event()
            self.messages: list[dict[str, object]] = []
            self._ready = True

        async def send(self, raw: str) -> None:
            message = json.loads(raw)
            self.messages.append(message)
            if message == {"type": "append", "text": "旧文本一"}:
                self.append_started.set()
                await self.append_release.wait()

        async def recv(self) -> str:
            if self._ready:
                self._ready = False
                return json.dumps(
                    {
                        "type": "ready",
                        "audio": {
                            "encoding": "pcm_s16le",
                            "sample_rate": 48_000,
                            "channels": 1,
                        },
                    }
                )
            await asyncio.Future()
            raise AssertionError("unreachable")

        async def close(self) -> None:
            pass

    async def exercise() -> None:
        websocket = BlockingAppendWebsocket()
        sink = FakeSink()

        async def connector(*_: object, **__: object) -> BlockingAppendWebsocket:
            return websocket

        async def simultaneous_interrupt() -> AsyncIterator[dict[str, object]]:
            yield observe_result()
            yield assistant_text("obs-1", "provider-1", "旧文本一")
            await websocket.append_started.wait()
            yield assistant_text("obs-2", "provider-1", "旧文本二")
            for _ in range(5):
                await asyncio.sleep(0)
            websocket.append_release.set()
            yield user_text()
            while not sink.cancelled:
                await asyncio.sleep(0)

        sidecar = PreviewSidecar(
            tts_url="ws://tts.example/v1/speech/stream",
            api_key="secret",
            sink_factory=lambda: asyncio.sleep(0, result=sink),
            connector=connector,
            emit=lambda _: None,
        )
        await asyncio.wait_for(sidecar.run(simultaneous_interrupt()), timeout=1)

        assert {"type": "append", "text": "旧文本二"} not in websocket.messages
        assert websocket.messages[-1] == {"type": "cancel"}
        assert sink.cancelled

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "interrupt_with_user",
    [True, False],
    ids=["user-interrupt", "provider-replacement"],
)
def test_interrupt_resets_incomplete_multi_chunk_observation(
    interrupt_with_user: bool,
) -> None:
    async def scenario(
        url: str,
        sessions: list[list[dict[str, object]]],
    ) -> None:
        sink = FakeSink()

        async def interrupted_frames() -> AsyncIterator[dict[str, object]]:
            yield observe_result()
            yield assistant_text(
                "obs-old",
                "provider-old",
                "未完成的旧文本",
                is_last_chunk=False,
            )
            for _ in range(3):
                await asyncio.sleep(0)
            if interrupt_with_user:
                yield user_text()
                for _ in range(3):
                    await asyncio.sleep(0)
            yield assistant_text("obs-new", "provider-new", "新的回答。")
            while not sessions or len(sessions[0]) != 2:
                await asyncio.sleep(0)
            yield assistant_terminal("provider-new", "done")
            while not sink.finished:
                await asyncio.sleep(0)

        sidecar = PreviewSidecar(
            tts_url=url,
            api_key="secret",
            sink_factory=lambda: asyncio.sleep(0, result=sink),
            emit=lambda _: None,
        )
        await sidecar.run(interrupted_frames())

        assert sessions == [
            [
                {"type": "start"},
                {"type": "append", "text": "新的回答。"},
                {"type": "finish"},
            ]
        ]
        assert sink.finished

    asyncio.run(run_fake_server(scenario))


def test_user_interrupt_cancels_session_while_finish_is_draining() -> None:
    async def exercise() -> None:
        received: list[dict[str, object]] = []
        finish_seen = asyncio.Event()

        async def handler(websocket: ServerConnection) -> None:
            await send_ready(websocket)
            async for raw in websocket:
                message = json.loads(raw)
                received.append(message)
                if message["type"] == "append":
                    await websocket.send(b"\x01\x02")
                elif message["type"] == "finish":
                    finish_seen.set()
                elif message["type"] == "cancel":
                    await websocket.send(
                        json.dumps({"type": "done", "cancelled": True})
                    )
                    return

        async def interrupting_frames() -> AsyncIterator[dict[str, object]]:
            yield observe_result()
            yield assistant_text("obs-1", "provider-1", "正在播放。")
            yield assistant_terminal("provider-1", "done")
            await finish_seen.wait()
            yield user_text()

        sink = FakeSink()
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            sidecar = PreviewSidecar(
                tts_url=f"ws://127.0.0.1:{port}/v1/speech/stream",
                api_key="secret",
                sink_factory=lambda: asyncio.sleep(0, result=sink),
                emit=lambda _: None,
            )
            await asyncio.wait_for(sidecar.run(interrupting_frames()), timeout=1)

        assert [item["type"] for item in received] == [
            "append",
            "finish",
            "cancel",
        ]
        assert sink.cancelled
        assert not sink.finished

    asyncio.run(exercise())


def test_new_provider_cancels_old_session_before_opening_and_finishes() -> None:
    async def exercise() -> None:
        sessions: list[list[dict[str, object]]] = []
        old_sink = BlockingCancelSink()
        new_sink = FakeSink()
        sinks: list[FakeSink] = [old_sink, new_sink]

        async def handler(websocket: ServerConnection) -> None:
            index = len(sessions)
            received: list[dict[str, object]] = []
            sessions.append(received)
            await send_ready(websocket)
            async for raw in websocket:
                message = json.loads(raw)
                received.append(message)
                if message["type"] == "append":
                    await websocket.send(b"\x01\x02")
                elif message["type"] == "finish" and index == 0:
                    continue
                elif message["type"] == "cancel":
                    await websocket.send(
                        json.dumps({"type": "done", "cancelled": True})
                    )
                    return
                elif message["type"] == "finish":
                    await websocket.send(
                        json.dumps({"type": "done", "cancelled": False})
                    )
                    return

        async def open_sink() -> FakeSink:
            return sinks.pop(0)

        async def provider_frames() -> AsyncIterator[dict[str, object]]:
            yield observe_result()
            yield assistant_text("obs-1", "provider-1", "旧回答。")
            while not sessions or len(sessions[0]) != 1:
                await asyncio.sleep(0)
            yield assistant_terminal("provider-1", "done")
            yield assistant_text("obs-2", "provider-2", "新回答。")
            await old_sink.cancel_started.wait()
            assert len(sessions) == 1
            old_sink.cancel_release.set()
            while len(sessions) != 2:
                await asyncio.sleep(0)
            yield assistant_terminal("provider-2", "done")
            while not new_sink.finished:
                await asyncio.sleep(0)

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            sidecar = PreviewSidecar(
                tts_url=f"ws://127.0.0.1:{port}/v1/speech/stream",
                api_key="secret",
                sink_factory=open_sink,
                emit=lambda _: None,
            )
            await asyncio.wait_for(sidecar.run(provider_frames()), timeout=1)

        assert [item["type"] for item in sessions[0]] == [
            "append",
            "cancel",
        ]
        assert [item["type"] for item in sessions[1]] == [
            "append",
            "finish",
        ]
        assert old_sink.cancelled

    asyncio.run(exercise())


def test_stdin_eof_cancels_draining_session_without_task_leaks() -> None:
    async def exercise() -> None:
        received: list[dict[str, object]] = []

        async def handler(websocket: ServerConnection) -> None:
            await send_ready(websocket)
            async for raw in websocket:
                message = json.loads(raw)
                received.append(message)
                if message["type"] == "append":
                    await websocket.send(b"\x01\x02")
                elif message["type"] == "finish":
                    continue
                elif message["type"] == "cancel":
                    await websocket.send(
                        json.dumps({"type": "done", "cancelled": True})
                    )
                    return

        sink = FakeSink()
        async with serve(handler, "127.0.0.1", 0) as server:
            baseline = set(asyncio.all_tasks())
            port = server.sockets[0].getsockname()[1]
            sidecar = PreviewSidecar(
                tts_url=f"ws://127.0.0.1:{port}/v1/speech/stream",
                api_key="secret",
                sink_factory=lambda: asyncio.sleep(0, result=sink),
                emit=lambda _: None,
            )

            async def eof_while_draining() -> AsyncIterator[dict[str, object]]:
                yield observe_result()
                yield assistant_text("obs-1", "provider-1", "播放尾部。")
                while not received:
                    await asyncio.sleep(0)
                yield assistant_terminal("provider-1", "done")
                while received[-1]["type"] != "finish":
                    await asyncio.sleep(0)

            await asyncio.wait_for(
                sidecar.run(eof_while_draining()),
                timeout=1,
            )
            await asyncio.sleep(0)
            leaked = [
                task for task in asyncio.all_tasks() - baseline if not task.done()
            ]

        assert [item["type"] for item in received] == [
            "append",
            "finish",
            "cancel",
        ]
        assert sink.cancelled
        assert leaked == []

    asyncio.run(exercise())


def test_aplay_finish_can_be_cancelled_while_waiting_for_process() -> None:
    class FakeStdin:
        def __init__(self) -> None:
            self.closed = False

        def write(self, _: bytes) -> None:
            pass

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.returncode: int | None = None
            self.exited = asyncio.Event()
            self.terminated = False
            self.killed = False

        async def wait(self) -> int:
            await self.exited.wait()
            assert self.returncode is not None
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15
            self.exited.set()

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9
            self.exited.set()

    async def exercise() -> None:
        process = FakeProcess()
        sink = AplaySink(process)  # type: ignore[arg-type]
        finish_task = asyncio.create_task(sink.finish())
        await asyncio.sleep(0)

        assert process.stdin.closed
        assert not finish_task.done()
        await sink.cancel()
        await asyncio.wait_for(finish_task, timeout=1)

        assert process.terminated
        assert not process.killed
        assert sink.exit_task.done()

    asyncio.run(exercise())


def test_aplay_early_exit_fails_active_tts_session() -> None:
    async def handler(websocket: ServerConnection) -> None:
        await send_ready(websocket)
        async for raw in websocket:
            if json.loads(raw)["type"] == "cancel":
                return

    async def delayed_frames() -> AsyncIterator[dict[str, object]]:
        yield observe_result()
        yield assistant_text("obs-1", "provider-1", "等待声音。")
        await asyncio.sleep(5)

    async def exercise() -> None:
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            async def open_failed_sink() -> AplaySink:
                return await AplaySink.open(Path("/bin/false"))

            sidecar = PreviewSidecar(
                tts_url=f"ws://127.0.0.1:{port}/v1/speech/stream",
                api_key="secret",
                sink_factory=open_failed_sink,
                emit=lambda _: None,
            )
            with pytest.raises(RuntimeError, match="aplay exited early"):
                await asyncio.wait_for(
                    sidecar.run(delayed_frames()),
                    timeout=1,
                )

    asyncio.run(exercise())


def test_observer_rejection_is_fatal() -> None:
    sidecar = PreviewSidecar(
        tts_url="ws://127.0.0.1:1/v1/speech/stream",
        api_key="secret",
        sink_factory=None,
        emit=lambda _: None,
    )
    rejected = {
        "op": "observe_result",
        "id": "tts-preview",
        "ok": False,
        "exception": {
            "code": "preview_disabled",
            "message": "preview disabled",
            "retryable": False,
        },
    }
    with pytest.raises(RuntimeError, match="preview_disabled"):
        asyncio.run(sidecar.run(frames(rejected)))


def test_builds_default_and_design_start_messages() -> None:
    assert (
        build_start_message(
            voice_id=None,
            design=None,
            mode=None,
            style=None,
        )
        == '{"type":"start"}'
    )
    assert json.loads(
        build_start_message(
            voice_id=None,
            design="A warm, natural voice",
            mode=None,
            style="gentle",
        )
    ) == {
        "type": "start",
        "voice": {
            "type": "design",
            "description": "A warm, natural voice",
        },
        "style": "gentle",
    }


def test_cli_accepts_start_options_and_rejects_conflicts() -> None:
    args = parse_args(
        [
            "--voice-id",
            "voice_" + "1" * 32,
            "--mode",
            "controllable",
            "--style",
            "calm",
        ]
    )
    assert args.mode == "controllable"

    defaults = parse_args([])
    assert defaults.voice_id is None
    assert defaults.design is None

    with pytest.raises(SystemExit):
        parse_args(
            [
                "--voice-id",
                "voice_" + "1" * 32,
                "--design",
                "warm",
            ]
        )
    with pytest.raises(SystemExit):
        parse_args(["--mode", "unsupported"])
    with pytest.raises(SystemExit):
        parse_args(["--env-file", "botified-tts.env"])
    with pytest.raises(SystemExit):
        parse_args(["--tts-url", "ws://tts.example/v1/speech/stream"])


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://tts.example", "ws://tts.example/v1/speech/stream"),
        ("https://tts.example:8443/", "wss://tts.example:8443/v1/speech/stream"),
    ],
)
def test_environment_derives_websocket_endpoint_from_service_base(
    base_url: str,
    expected: str,
) -> None:
    assert load_tts_environment(
        {
            "BOTIFIED_TTS_URL": base_url,
            "BOTIFIED_TTS_API_KEY": "AbC.0_~-z",
        }
    ) == (
        expected,
        "AbC.0_~-z",
    )


@pytest.mark.parametrize(
    "base_url",
    [
        "ws://tts.example",
        "http://tts.example/v1/speech",
        "http://user@tts.example",
        "http://tts.example?voice=test",
        "http://tts.example#fragment",
        "http://tts.example:0",
        "http://tts.example:",
        "not-a-url",
        "http://tts example",
    ],
)
def test_environment_rejects_invalid_service_base_without_leaking_key(
    base_url: str,
) -> None:
    api_key = "secret-not-in-error"
    with pytest.raises(RuntimeError, match="BOTIFIED_TTS_URL") as caught:
        load_tts_environment(
            {
                "BOTIFIED_TTS_URL": base_url,
                "BOTIFIED_TTS_API_KEY": api_key,
            }
        )
    assert api_key not in str(caught.value)


@pytest.mark.parametrize(
    ("environment", "missing_name"),
    [
        ({"BOTIFIED_TTS_API_KEY": "secret"}, "BOTIFIED_TTS_URL"),
        ({"BOTIFIED_TTS_URL": "http://tts.example"}, "BOTIFIED_TTS_API_KEY"),
        (
            {
                "BOTIFIED_TTS_URL": "http://tts.example",
                "BOTIFIED_TTS_API_KEY": "line1\nline2",
            },
            "BOTIFIED_TTS_API_KEY",
        ),
        (
            {
                "BOTIFIED_TTS_URL": "http://tts.example",
                "BOTIFIED_TTS_API_KEY": "not+the+shared+format",
            },
            "BOTIFIED_TTS_API_KEY",
        ),
    ],
)
def test_environment_failure_names_variable_without_printing_key(
    environment: dict[str, str],
    missing_name: str,
) -> None:
    with pytest.raises(RuntimeError, match=missing_name) as caught:
        load_tts_environment(environment)
    assert environment.get("BOTIFIED_TTS_API_KEY", "secret") not in str(caught.value)


def test_start_options_match_tts_api_combinations() -> None:
    profile = "voice_" + "1" * 32

    with pytest.raises(ValueError, match="mode requires"):
        build_start_message(
            voice_id=None,
            design=None,
            mode="controllable",
            style=None,
        )
    with pytest.raises(ValueError, match="mode requires"):
        build_start_message(
            voice_id=None,
            design="warm",
            mode="controllable",
            style=None,
        )
    with pytest.raises(ValueError, match="faithful.*style"):
        build_start_message(
            voice_id=profile,
            design=None,
            mode="faithful",
            style="calm",
        )


def test_installed_console_command_can_start() -> None:
    executable = shutil.which("botified-tts-companion")
    assert executable is not None
    result = subprocess.run(
        [executable, "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--voice-id" in result.stdout
    assert "--env-file" not in result.stdout
    assert "--tts-url" not in result.stdout


def test_handshake_error_preserves_code_and_message_without_opening_player() -> None:
    async def exercise() -> None:
        sink_opened = False

        async def handler(websocket: ServerConnection) -> None:
            assert json.loads(await websocket.recv()) == {"type": "start"}
            await websocket.send(
                json.dumps(
                    {
                        "type": "error",
                        "error": {
                            "code": "invalid_voice",
                            "message": "Voice profile not found",
                        },
                    }
                )
            )

        async def open_sink() -> FakeSink:
            nonlocal sink_opened
            sink_opened = True
            return FakeSink()

        async def wait_for_handshake_error() -> AsyncIterator[dict[str, object]]:
            yield observe_result()
            yield assistant_text("obs-1", "provider-1", "hello")
            await asyncio.sleep(5)

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            sidecar = PreviewSidecar(
                tts_url=f"ws://127.0.0.1:{port}/v1/speech/stream",
                api_key="secret-not-in-error",
                sink_factory=open_sink,
                emit=lambda _: None,
            )
            with pytest.raises(
                RuntimeError,
                match="invalid_voice.*Voice profile not found",
            ) as caught:
                await sidecar.run(wait_for_handshake_error())

        assert not sink_opened
        assert "secret-not-in-error" not in str(caught.value)

    asyncio.run(exercise())


def test_streaming_error_preserves_code_and_message() -> None:
    async def exercise() -> None:
        error_sent = asyncio.Event()

        async def handler(websocket: ServerConnection) -> None:
            await send_ready(websocket)
            async for raw in websocket:
                if json.loads(raw)["type"] == "append":
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "error",
                                "error": {
                                    "code": "engine_error",
                                    "message": "Synthesis failed",
                                },
                            }
                        )
                    )
                    error_sent.set()

        async def delayed_frames() -> AsyncIterator[dict[str, object]]:
            yield observe_result()
            yield assistant_text("obs-1", "provider-1", "hello")
            await error_sent.wait()
            await asyncio.sleep(5)

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            sidecar = PreviewSidecar(
                tts_url=f"ws://127.0.0.1:{port}/v1/speech/stream",
                api_key="secret",
                sink_factory=lambda: asyncio.sleep(0, result=FakeSink()),
                emit=lambda _: None,
            )
            with pytest.raises(
                RuntimeError,
                match="engine_error.*Synthesis failed",
            ):
                await asyncio.wait_for(
                    sidecar.run(delayed_frames()),
                    timeout=1,
                )

    asyncio.run(exercise())


def test_aplay_failures_are_explicit(tmp_path: Path) -> None:

    with pytest.raises(RuntimeError, match="aplay executable"):
        asyncio.run(AplaySink.open(tmp_path / "missing-aplay"))

    async def early_exit() -> None:
        sink = await AplaySink.open(Path("/bin/false"))
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="aplay exited"):
            await sink.finish()

    asyncio.run(early_exit())
