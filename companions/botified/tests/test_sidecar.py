from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from sidecar import AplaySink, PreviewSidecar, read_api_key


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

        sidecar = PreviewSidecar(
            tts_url=url,
            api_key="secret",
            sink_factory=open_sink,
            emit=emitted.append,
        )

        async def normal_finish_frames() -> AsyncIterator[dict[str, object]]:
            for frame in (
                observe_result(),
                assistant_text(
                    "obs-1",
                    "provider-1",
                    "你",
                    is_last_chunk=False,
                ),
                assistant_text(
                    "obs-1",
                    "provider-1",
                    "好。",
                    chunk_index=1,
                ),
                assistant_text("obs-2", "provider-2", "新的回答。"),
                assistant_terminal("provider-1", "error"),
                assistant_terminal("provider-2", "done"),
            ):
                yield frame
            while len(sinks) != 2 or not sinks[1].finished:
                await asyncio.sleep(0)

        await sidecar.run(normal_finish_frames())

        assert len(sessions) == 2
        assert sessions[0] == [
            {"type": "start"},
            {"type": "append", "text": "你好。"},
            {"type": "cancel"},
        ]
        assert sessions[1] == [
            {"type": "start"},
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
        await sidecar.run(
            frames(
                observe_result(),
                assistant_text("obs-1", "provider-1", "第一条回答。"),
                user_text(),
                assistant_text("obs-2", "provider-2", "第二条回答。"),
            )
        )

        assert [session[-1] for session in sessions] == [
            {"type": "cancel"},
            {"type": "cancel"},
        ]
        assert all(sink.cancelled for sink in sinks)

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


def test_new_provider_waits_for_old_drain_before_opening_and_finishes() -> None:
    async def exercise() -> None:
        sessions: list[list[dict[str, object]]] = []
        old_sink = BlockingCancelSink()
        sinks: list[FakeSink] = [old_sink, FakeSink()]

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
            yield assistant_terminal("provider-1", "done")
            yield assistant_text("obs-2", "provider-2", "新回答。")
            await old_sink.cancel_started.wait()
            assert len(sessions) == 1
            old_sink.cancel_release.set()
            while len(sessions) != 2:
                await asyncio.sleep(0)
            yield assistant_terminal("provider-2", "done")

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
            "finish",
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
            await asyncio.wait_for(
                sidecar.run(
                    frames(
                        observe_result(),
                        assistant_text("obs-1", "provider-1", "播放尾部。"),
                        assistant_terminal("provider-1", "done"),
                    )
                ),
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


def test_api_key_file_and_aplay_failures_are_explicit(tmp_path: Path) -> None:
    key_file = tmp_path / "tts.key"
    key_file.write_text("top-secret\n", encoding="utf-8")
    assert read_api_key(key_file) == "top-secret"

    with pytest.raises(RuntimeError, match="aplay executable"):
        asyncio.run(AplaySink.open(tmp_path / "missing-aplay"))

    async def early_exit() -> None:
        sink = await AplaySink.open(Path("/bin/false"))
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="aplay exited"):
            await sink.finish()

    asyncio.run(early_exit())
