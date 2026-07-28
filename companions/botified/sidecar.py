from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

FRAME_OPEN = "<botified>"
FRAME_CLOSE = "</botified>"
OBSERVE_REQUEST_ID = "tts-preview"
APLAYER = Path("/usr/bin/aplay")
API_KEY_PATTERN = re.compile(r"[A-Za-z0-9._~-]+")
DEFAULT_START_MESSAGE = '{"type":"start"}'
_EOF = object()


class AudioSink(Protocol):
    async def write(self, pcm: bytes) -> None: ...

    async def finish(self) -> None: ...

    async def cancel(self) -> None: ...


SinkFactory = Callable[[], Awaitable[AudioSink]]
Connector = Callable[..., Awaitable[Any]]


class AplaySink:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self.exit_task = asyncio.create_task(process.wait())
        self._input_closed = False
        self._cancelled = False

    @classmethod
    async def open(cls, executable: Path = APLAYER) -> AplaySink:
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(f"aplay executable is unavailable: {executable}")
        process = await asyncio.create_subprocess_exec(
            str(executable),
            "-q",
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            "48000",
            "-c",
            "1",
            stdin=asyncio.subprocess.PIPE,
        )
        return cls(process)

    async def write(self, pcm: bytes) -> None:
        if self._input_closed:
            raise RuntimeError("aplay sink is closed")
        if self._process.returncode is not None:
            raise RuntimeError(
                f"aplay exited early with code {self._process.returncode}"
            )
        stdin = self._process.stdin
        if stdin is None:
            raise RuntimeError("aplay stdin is unavailable")
        try:
            stdin.write(pcm)
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            await self._wait()
            raise RuntimeError(
                f"aplay exited early with code {self._process.returncode}"
            ) from error

    async def finish(self) -> None:
        self._close_input()
        await self._wait()
        if self._process.returncode != 0 and not self._cancelled:
            raise RuntimeError(f"aplay exited with code {self._process.returncode}")

    async def cancel(self) -> None:
        first_cancel = not self._cancelled
        self._cancelled = True
        self._close_input()
        if first_cancel and self._process.returncode is None:
            self._process.terminate()
        try:
            async with asyncio.timeout(2):
                await asyncio.shield(self.exit_task)
        except TimeoutError:
            self._process.kill()
            await asyncio.shield(self.exit_task)

    async def _wait(self) -> None:
        await asyncio.shield(self.exit_task)

    def _close_input(self) -> None:
        if self._input_closed:
            return
        self._input_closed = True
        if self._process.stdin is not None:
            self._process.stdin.close()


class _PreviewAssembler:
    def __init__(self) -> None:
        self._id: str | None = None
        self._provider_request_id: str | None = None
        self._next_index = 0
        self._parts: list[str] = []

    def feed(self, frame: dict[str, object]) -> tuple[str, str] | None:
        observation_id = frame.get("id")
        provider_request_id = frame.get("provider_request_id")
        text = frame.get("text")
        chunk_index = frame.get("chunk_index")
        is_last = frame.get("is_last_chunk")
        if (
            not isinstance(observation_id, str)
            or not isinstance(provider_request_id, str)
            or not isinstance(text, str)
            or not isinstance(chunk_index, int)
            or not isinstance(is_last, bool)
        ):
            raise TypeError("invalid assistant observe text frame")

        if chunk_index == 0:
            if self._id is not None:
                raise RuntimeError("interleaved observe text chunks")
            self._id = observation_id
            self._provider_request_id = provider_request_id
            self._next_index = 0
            self._parts = []
        if (
            observation_id != self._id
            or provider_request_id != self._provider_request_id
            or chunk_index != self._next_index
        ):
            raise RuntimeError("out-of-order observe text chunks")

        self._parts.append(text)
        self._next_index += 1
        if not is_last:
            return None

        completed = (provider_request_id, "".join(self._parts))
        self.reset()
        return completed

    def reset(self) -> None:
        self._id = None
        self._provider_request_id = None
        self._next_index = 0
        self._parts = []


class _TtsSession:
    def __init__(
        self,
        websocket: Any,
        sink: AudioSink,
    ) -> None:
        self.websocket = websocket
        self.sink = sink
        self.closed = False
        self.receiver = asyncio.create_task(self._receive())
        self.watcher = asyncio.create_task(self._watch())
        self._abort_lock = asyncio.Lock()

    @classmethod
    async def open(
        cls,
        tts_url: str,
        api_key: str,
        start_message: str,
        sink_factory: SinkFactory,
        connector: Connector,
    ) -> _TtsSession:
        websocket = await connector(
            tts_url,
            additional_headers={"Authorization": f"Bearer {api_key}"},
            open_timeout=10,
            close_timeout=2,
        )
        try:
            await websocket.send(start_message)
            raw_ready = await websocket.recv()
            ready = _json_event(raw_ready)
            _raise_tts_error(ready)
            if ready != {
                "type": "ready",
                "audio": {
                    "encoding": "pcm_s16le",
                    "sample_rate": 48_000,
                    "channels": 1,
                },
            }:
                raise RuntimeError("TTS WebSocket returned incompatible audio")
            sink = await sink_factory()
        except BaseException:
            await websocket.close()
            raise
        return cls(websocket, sink)

    async def append(self, text: str) -> None:
        self.raise_if_failed()
        await self.websocket.send(
            json.dumps(
                {"type": "append", "text": text},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

    async def finish(self) -> None:
        if self.closed:
            return
        try:
            self.raise_if_failed()
            await self.websocket.send('{"type":"finish"}')
            event = await self.receiver
            if event != {"type": "done", "cancelled": False}:
                raise RuntimeError("TTS WebSocket did not finish normally")
            await self.sink.finish()
        except BaseException:
            await self._abort()
            raise
        self.closed = True
        self.watcher.cancel()
        await asyncio.gather(self.watcher, return_exceptions=True)
        await self.websocket.close()

    async def cancel(self) -> None:
        if self.closed:
            return
        try:
            with contextlib.suppress(Exception):
                await self.websocket.send('{"type":"cancel"}')
        finally:
            await self._abort()

    def raise_if_failed(self) -> None:
        if not self.watcher.done():
            return
        try:
            event = self.watcher.result()
        except asyncio.CancelledError:
            raise
        except RuntimeError:
            raise
        except Exception as error:
            raise RuntimeError("TTS WebSocket receiver failed") from error
        raise RuntimeError(f"TTS WebSocket ended unexpectedly: {event}")

    async def _receive(self) -> dict[str, object]:
        while True:
            raw = await self.websocket.recv()
            if isinstance(raw, bytes):
                await self.sink.write(raw)
                continue
            event = _json_event(raw)
            _raise_tts_error(event)
            if event.get("type") == "done":
                return event
            raise RuntimeError("TTS WebSocket returned an unexpected event")

    async def _watch(self) -> dict[str, object]:
        exit_task = getattr(self.sink, "exit_task", None)
        if not isinstance(exit_task, asyncio.Task):
            return await self.receiver
        done, _ = await asyncio.wait(
            (self.receiver, exit_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        if exit_task in done:
            raise RuntimeError(f"aplay exited early with code {exit_task.result()}")
        return self.receiver.result()

    async def _abort(self) -> None:
        async with self._abort_lock:
            if self.closed:
                return
            self.closed = True
            try:
                await self.sink.cancel()
            finally:
                self.watcher.cancel()
                self.receiver.cancel()
                await asyncio.gather(
                    self.watcher,
                    self.receiver,
                    return_exceptions=True,
                )
                await self.websocket.close()


class _ActiveSession:
    def __init__(
        self,
        provider_request_id: str,
        session: _TtsSession,
    ) -> None:
        self.provider_request_id = provider_request_id
        self.session = session
        self.drain_task: asyncio.Task[None] | None = None


class _TtsManager:
    def __init__(
        self,
        tts_url: str,
        api_key: str,
        start_message: str,
        sink_factory: SinkFactory,
        connector: Connector,
    ) -> None:
        self._tts_url = tts_url
        self._api_key = api_key
        self._start_message = start_message
        self._sink_factory = sink_factory
        self._connector = connector
        self._active: _ActiveSession | None = None

    @property
    def monitor(self) -> asyncio.Task[object] | None:
        active = self._active
        if active is None:
            return None
        if active.drain_task is not None:
            return active.drain_task
        return active.session.watcher

    async def append(self, provider_request_id: str, text: str) -> None:
        active = self._active
        if active is None or active.provider_request_id != provider_request_id:
            await self.cancel()
            session = await _TtsSession.open(
                self._tts_url,
                self._api_key,
                self._start_message,
                self._sink_factory,
                self._connector,
            )
            active = _ActiveSession(provider_request_id, session)
            self._active = active
        elif active.drain_task is not None:
            raise RuntimeError("cannot append after assistant done")
        if text:
            await active.session.append(text)

    async def finish(self, provider_request_id: str) -> None:
        active = self._active
        if (
            active is None
            or active.provider_request_id != provider_request_id
            or active.drain_task is not None
        ):
            return
        active.drain_task = asyncio.create_task(active.session.finish())

    async def cancel(self, provider_request_id: str | None = None) -> None:
        active = self._active
        if (
            active is not None
            and provider_request_id is not None
            and active.provider_request_id != provider_request_id
        ):
            return
        if active is None:
            return
        drain_task = active.drain_task
        try:
            if drain_task is None:
                await active.session.cancel()
            elif drain_task.done():
                drain_task.result()
            else:
                try:
                    await active.session.cancel()
                finally:
                    await asyncio.gather(
                        drain_task,
                        return_exceptions=True,
                    )
        finally:
            if self._active is active:
                self._active = None

    def reap_monitor(self) -> None:
        active = self._active
        if active is None:
            return
        drain_task = active.drain_task
        if drain_task is None:
            active.session.raise_if_failed()
            return
        drain_task.result()
        if self._active is active:
            self._active = None


class PreviewSidecar:
    def __init__(
        self,
        *,
        tts_url: str,
        api_key: str,
        start_message: str = DEFAULT_START_MESSAGE,
        sink_factory: SinkFactory | None = None,
        connector: Connector = connect,
        emit: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._manager = _TtsManager(
            tts_url,
            api_key,
            start_message,
            sink_factory or AplaySink.open,
            connector,
        )
        self._assembler = _PreviewAssembler()
        self._emit = emit or emit_frame

    async def run(
        self,
        frames: AsyncIterator[dict[str, object]],
    ) -> None:
        self._emit(
            {
                "op": "observe_request",
                "id": OBSERVE_REQUEST_ID,
                "delivery": "stream_text",
                "min_batch_chars": 1,
            }
        )
        queue: asyncio.Queue[dict[str, object] | BaseException | object] = (
            asyncio.Queue()
        )
        reader = asyncio.create_task(_drain_frames(frames, queue))
        configured = False
        active_error: BaseException | None = None
        try:
            while True:
                frame_task = asyncio.create_task(queue.get())
                monitor = self._manager.monitor
                watched: set[asyncio.Task[Any]] = {frame_task}
                if monitor is not None:
                    watched.add(monitor)
                done, _ = await asyncio.wait(
                    watched,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if monitor is not None and monitor in done:
                    self._manager.reap_monitor()
                    if frame_task not in done:
                        frame_task.cancel()
                        await asyncio.gather(
                            frame_task,
                            return_exceptions=True,
                        )
                        continue
                item = frame_task.result()
                if item is _EOF:
                    if not configured:
                        raise RuntimeError("stdin closed before observer configuration")
                    return
                if isinstance(item, BaseException):
                    raise item
                frame = item
                if not isinstance(frame, dict):
                    raise TypeError("invalid Botified stdin frame")
                if (
                    frame.get("op") == "observe_result"
                    and frame.get("id") == OBSERVE_REQUEST_ID
                ):
                    if frame.get("ok") is not True:
                        exception = frame.get("exception")
                        code = (
                            exception.get("code")
                            if isinstance(exception, dict)
                            else "unknown"
                        )
                        raise RuntimeError(f"observer configuration failed: {code}")
                    configured = True
                    continue
                if configured and frame.get("op") == "observe":
                    await self._handle_observe(frame)
        except BaseException as error:
            active_error = error
            raise
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            try:
                await self._manager.cancel()
            except BaseException:
                if active_error is None:
                    raise

    async def _handle_observe(self, frame: dict[str, object]) -> None:
        source = frame.get("source")
        event = frame.get("event")
        if source == "user" and event == "text":
            self._assembler.reset()
            await self._manager.cancel()
            return
        if source != "assistant":
            return
        provider_request_id = frame.get("provider_request_id")
        if not isinstance(provider_request_id, str):
            raise TypeError("assistant observe frame has no provider request id")
        if event == "text":
            completed = self._assembler.feed(frame)
            if completed is not None:
                await self._manager.append(*completed)
            return
        self._assembler.reset()
        if event == "done":
            await self._manager.finish(provider_request_id)
        elif event == "error":
            await self._manager.cancel(provider_request_id)


async def _drain_frames(
    frames: AsyncIterator[dict[str, object]],
    queue: asyncio.Queue[dict[str, object] | BaseException | object],
) -> None:
    try:
        async for frame in frames:
            queue.put_nowait(frame)
    except Exception as error:  # noqa: BLE001 - propagate reader failures
        queue.put_nowait(error)
    finally:
        queue.put_nowait(_EOF)


def _json_event(raw: object) -> dict[str, object]:
    if not isinstance(raw, str):
        raise TypeError("TTS WebSocket returned invalid JSON")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("TTS WebSocket returned invalid JSON") from error
    if not isinstance(value, dict):
        raise TypeError("TTS WebSocket returned invalid JSON")
    return value


def _raise_tts_error(event: dict[str, object]) -> None:
    if event.get("type") != "error":
        return
    error = event.get("error")
    if not isinstance(error, dict):
        raise TypeError("TTS WebSocket error: unknown: unknown error")
    code = error.get("code")
    message = error.get("message")
    if not isinstance(code, str) or not code:
        code = "unknown"
    if not isinstance(message, str) or not message:
        message = "unknown error"
    raise RuntimeError(f"TTS WebSocket error: {code}: {message}")


def emit_frame(payload: dict[str, object]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    print(f"{FRAME_OPEN}{encoded}{FRAME_CLOSE}", flush=True)


async def stdin_frames() -> AsyncIterator[dict[str, object]]:
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_running_loop().connect_read_pipe(
        lambda: protocol,
        sys.stdin,
    )
    while line := await reader.readline():
        text = line.decode("utf-8", "replace").strip()
        if not text.startswith(FRAME_OPEN) or not text.endswith(FRAME_CLOSE):
            continue
        payload = text[len(FRAME_OPEN) : -len(FRAME_CLOSE)]
        try:
            frame = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(frame, dict):
            yield frame


def read_api_key_from_env_file(path: Path) -> str:
    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"could not read env file: {path}") from error
    keys: list[str] = []
    for line in contents.split("\n"):
        name, separator, value = line.partition("=")
        if separator and name == "BOTIFIED_TTS_API_KEY":
            keys.append(value)
    if len(keys) != 1 or API_KEY_PATTERN.fullmatch(keys[0]) is None:
        raise RuntimeError(
            "env file must contain exactly one literal BOTIFIED_TTS_API_KEY "
            "matching [A-Za-z0-9._~-]+"
        )
    return keys[0]


def build_start_message(
    *,
    voice_id: str | None,
    design: str | None,
    mode: str | None,
    style: str | None,
) -> str:
    if voice_id is not None and design is not None:
        raise ValueError("voice_id and design are mutually exclusive")
    event: dict[str, object] = {"type": "start"}
    if voice_id is not None:
        event["voice"] = {"type": "profile", "id": voice_id}
    elif design is not None:
        event["voice"] = {"type": "design", "description": design}
    if mode is not None:
        event["mode"] = mode
    if style is not None:
        event["style"] = style
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def parse_tts_url(value: str) -> str:
    try:
        endpoint = urlsplit(value)
        port = endpoint.port
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "--tts-url must be a complete ws:// or wss:// endpoint"
        ) from error
    if (
        endpoint.scheme not in {"ws", "wss"}
        or not endpoint.hostname
        or port == 0
        or endpoint.path != "/v1/speech/stream"
        or "?" in value
        or "#" in value
        or endpoint.username is not None
        or endpoint.password is not None
        or any(character.isspace() for character in endpoint.netloc)
    ):
        raise argparse.ArgumentTypeError(
            "--tts-url must be a complete ws:// or wss:// host"
            "/v1/speech/stream endpoint without query or fragment"
        )
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Speak Botified assistant preview through botified-tts.",
    )
    parser.add_argument(
        "--tts-url",
        type=parse_tts_url,
        default="ws://127.0.0.1:8000/v1/speech/stream",
    )
    parser.add_argument("--env-file", type=Path, required=True)
    voice = parser.add_mutually_exclusive_group()
    voice.add_argument("--voice-id")
    voice.add_argument("--design")
    parser.add_argument("--mode", choices=("controllable", "faithful"))
    parser.add_argument("--style")
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> None:
    api_key = read_api_key_from_env_file(args.env_file)
    start_message = build_start_message(
        voice_id=args.voice_id,
        design=args.design,
        mode=args.mode,
        style=args.style,
    )
    sidecar = PreviewSidecar(
        tts_url=args.tts_url,
        api_key=api_key,
        start_message=start_message,
    )
    await sidecar.run(stdin_frames())


def main() -> None:
    try:
        asyncio.run(async_main(parse_args()))
    except Exception as error:  # noqa: BLE001 - process boundary
        print(f"botified-tts-sidecar: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
