from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal

from starlette.websockets import WebSocket, WebSocketDisconnect

from botified_tts.engine import EngineError
from botified_tts.schemas import (
    AppendMessage,
    CancelMessage,
    FinishMessage,
    FlushMessage,
    InputTooLarge,
    InvalidSynthesisOptions,
    StartMessage,
    parse_client_message,
)
from botified_tts.segmenter import Segmenter
from botified_tts.speech import SpeechService, SynthesisSummary
from botified_tts.voices import InvalidVoice


_SEGMENT_DEADLINE_SECONDS = 0.8
_IDLE_TIMEOUT_SECONDS = 60.0
_SEND_TIMEOUT_SECONDS = 5.0
_CLEANUP_TIMEOUT_SECONDS = 1.0
_SESSION_TEXT_MAX_BYTES = 64 * 1024
_SEGMENT_END = object()


class _IdleTimeout(Exception):
    pass


class _ClientTooSlow(Exception):
    pass


class _CleanupFailed(Exception):
    pass


class _Rejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _StreamingSession:
    def __init__(
        self,
        *,
        websocket: WebSocket,
        speech: SpeechService,
        authorize: Callable[[WebSocket], tuple[str, str] | None],
        try_acquire: Callable[[], bool],
        release: Callable[[], None],
    ) -> None:
        self._websocket = websocket
        self._speech = speech
        self._authorize = authorize
        self._try_acquire = try_acquire
        self._release = release

    async def run(self) -> None:
        acquired = False
        terminal: dict[str, object] | None = None
        receive_task: asyncio.Task[Literal["cancel"]] | None = None
        generate_task: asyncio.Task[None] | None = None
        cancel_event = asyncio.Event()
        finish_event = asyncio.Event()
        summary: SynthesisSummary | None = None
        result = "engine_error"

        await self._websocket.accept()
        try:
            rejection = self._authorize(self._websocket)
            if rejection is not None:
                raise _Rejected(*rejection)
            try:
                async with asyncio.timeout(_IDLE_TIMEOUT_SECONDS):
                    first = await _receive_client_message(self._websocket)
            except TimeoutError:
                raise _IdleTimeout from None
            if not isinstance(first, StartMessage):
                raise InvalidSynthesisOptions(
                    "first client message must be start"
                )
            if not self._try_acquire():
                raise _Rejected("service_busy", "Service is busy")
            acquired = True
            summary = SynthesisSummary(
                id=f"session_{uuid.uuid4().hex}",
                ttfb_started_at=None,
            )
            summary.set_options(first.options)
            active_idle_deadline = (
                asyncio.get_running_loop().time() + _IDLE_TIMEOUT_SECONDS
            )
            await _send_json(
                self._websocket,
                {
                    "type": "ready",
                    "audio": {
                        "encoding": "pcm_s16le",
                        "sample_rate": 48_000,
                        "channels": 1,
                    },
                },
            )

            queue: asyncio.Queue[str | object] = asyncio.Queue()
            receive_task = asyncio.create_task(
                self._receive_loop(
                    queue,
                    cancel_event,
                    finish_event,
                    active_idle_deadline,
                    summary,
                )
            )
            generate_task = asyncio.create_task(
                self._generate_and_send(
                    first,
                    queue,
                    cancel_event,
                    summary,
                )
            )
            cancelled = await self._coordinate(
                receive_task,
                generate_task,
                cancel_event,
                finish_event,
            )
            terminal = {"type": "done", "cancelled": cancelled}
            result = "cancelled" if cancelled else "ok"
        except _IdleTimeout:
            terminal = {"type": "done", "cancelled": True}
            result = "cancelled"
        except _ClientTooSlow:
            result = "client_too_slow"
            terminal = _ws_error(
                "client_too_slow",
                "Client is too slow",
            )
        except InputTooLarge as error:
            result = "input_too_large"
            terminal = _ws_error("input_too_large", str(error))
        except InvalidVoice as error:
            result = "invalid_voice"
            terminal = _ws_error("invalid_voice", str(error))
        except InvalidSynthesisOptions as error:
            result = "invalid_request"
            terminal = _ws_error("invalid_request", str(error))
        except EngineError:
            result = "engine_error"
            terminal = _ws_error(
                "engine_error",
                "Speech synthesis failed",
            )
        except _Rejected as error:
            result = error.code
            terminal = _ws_error(error.code, error.message)
        except WebSocketDisconnect:
            result = "client_disconnected"
            terminal = None
        except asyncio.CancelledError:
            result = "cancelled"
            raise
        except Exception:
            result = "engine_error"
            terminal = _ws_error(
                "engine_error",
                "Internal server error",
            )
        finally:
            cancel_event.set()
            await _stop_task(receive_task)
            await _stop_task(generate_task)
            if terminal is not None:
                await _best_effort_send_json(self._websocket, terminal)
            if acquired:
                self._release()
            if summary is not None:
                summary.log_terminal(result)
            with contextlib.suppress(_CleanupFailed):
                await _bounded_cleanup(self._websocket.close())

    async def _coordinate(
        self,
        receive_task: asyncio.Task[Literal["cancel"]],
        generate_task: asyncio.Task[None],
        cancel_event: asyncio.Event,
        finish_event: asyncio.Event,
    ) -> bool:
        done, _ = await asyncio.wait(
            {receive_task, generate_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if generate_task in done:
            await generate_task
        if receive_task.done():
            receive_task.result()
            cancel_event.set()
            await _stop_task(generate_task)
            return True
        if not finish_event.is_set():
            raise EngineError(
                "engine_error",
                "speech stream ended before client finish",
            )
        await _stop_task(receive_task)
        return False

    async def _receive_loop(
        self,
        queue: asyncio.Queue[str | object],
        cancel_event: asyncio.Event,
        finish_event: asyncio.Event,
        idle_deadline: float,
        summary: SynthesisSummary,
    ) -> Literal["cancel"]:
        segmenter = Segmenter()
        loop = asyncio.get_running_loop()
        segment_deadline: float | None = None
        accepted_bytes = 0
        draining = False

        while True:
            if draining:
                message = await _receive_client_message(self._websocket)
            else:
                now = loop.time()
                next_deadline = (
                    idle_deadline
                    if segment_deadline is None
                    else min(idle_deadline, segment_deadline)
                )
                try:
                    async with asyncio.timeout(
                        max(0.0, next_deadline - now)
                    ):
                        message = await _receive_client_message(self._websocket)
                except TimeoutError:
                    now = loop.time()
                    if (
                        segment_deadline is not None
                        and segment_deadline <= now
                    ):
                        segments = segmenter.expire_deadline()
                        _enqueue_segments(queue, segments)
                        segment_deadline = (
                            now + _SEGMENT_DEADLINE_SECONDS
                            if segments and segmenter.has_pending_text
                            else None
                        )
                        continue
                    cancel_event.set()
                    _clear_queue(queue)
                    return "cancel"

            now = loop.time()
            if draining:
                if isinstance(message, CancelMessage):
                    cancel_event.set()
                    _clear_queue(queue)
                    return "cancel"
                raise InvalidSynthesisOptions(
                    "only cancel is valid after finish"
                )

            idle_deadline = now + _IDLE_TIMEOUT_SECONDS
            if isinstance(message, AppendMessage):
                text_bytes = len(message.text.encode("utf-8"))
                if accepted_bytes + text_bytes > _SESSION_TEXT_MAX_BYTES:
                    raise InputTooLarge(
                        "WebSocket session text exceeds 65536 UTF-8 bytes"
                    )
                accepted_bytes += text_bytes
                summary.accept_text(message.text)
                segments = segmenter.append(message.text)
                _enqueue_segments(queue, segments)
                if segments:
                    segment_deadline = (
                        now + _SEGMENT_DEADLINE_SECONDS
                        if segmenter.has_pending_text
                        else None
                    )
                elif (
                    segment_deadline is None
                    and segmenter.has_pending_text
                    and not segmenter.deadline_is_expired
                ):
                    segment_deadline = (
                        now + _SEGMENT_DEADLINE_SECONDS
                    )
                continue

            if isinstance(message, FlushMessage):
                segment_deadline = None
                _enqueue_segments(queue, segmenter.flush())
                continue

            if isinstance(message, FinishMessage):
                segment_deadline = None
                _enqueue_segments(queue, segmenter.finish())
                queue.put_nowait(_SEGMENT_END)
                finish_event.set()
                draining = True
                continue

            if isinstance(message, CancelMessage):
                cancel_event.set()
                _clear_queue(queue)
                return "cancel"

            raise InvalidSynthesisOptions(
                "start is only valid as the first client message"
            )

    async def _generate_and_send(
        self,
        start: StartMessage,
        queue: asyncio.Queue[str | object],
        cancel_event: asyncio.Event,
        summary: SynthesisSummary,
    ) -> None:
        stream = self._speech.synthesize(
            start.options,
            _segment_source(queue, cancel_event),
            summary=summary,
        )
        primary_failure = False
        try:
            async for pcm in stream:
                if cancel_event.is_set():
                    return
                try:
                    async with asyncio.timeout(_SEND_TIMEOUT_SECONDS):
                        await self._websocket.send_bytes(pcm)
                except TimeoutError:
                    raise _ClientTooSlow from None
        except BaseException:
            primary_failure = True
            raise
        finally:
            try:
                await _bounded_cleanup(stream.aclose())
            except _CleanupFailed as error:
                if not primary_failure:
                    raise EngineError(
                        "engine_error",
                        "speech stream cleanup failed",
                    ) from error


async def _receive_client_message(websocket: WebSocket) -> object:
    try:
        value = await websocket.receive_json()
    except WebSocketDisconnect:
        raise
    except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
        raise InvalidSynthesisOptions(
            "client message must be valid JSON text"
        ) from None
    return parse_client_message(value)


async def _segment_source(
    queue: asyncio.Queue[str | object],
    cancel_event: asyncio.Event,
) -> AsyncIterator[str]:
    while not cancel_event.is_set():
        item = await queue.get()
        if item is _SEGMENT_END:
            return
        if not isinstance(item, str):
            raise RuntimeError("segment queue contains an invalid item")
        yield item


def _enqueue_segments(
    queue: asyncio.Queue[str | object],
    segments: list[str],
) -> None:
    for segment in segments:
        queue.put_nowait(segment)


def _clear_queue(queue: asyncio.Queue[str | object]) -> None:
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return


async def _send_json(
    websocket: WebSocket,
    value: object,
) -> None:
    try:
        async with asyncio.timeout(_SEND_TIMEOUT_SECONDS):
            await websocket.send_json(value)
    except TimeoutError:
        raise _ClientTooSlow from None


async def _best_effort_send_json(
    websocket: WebSocket,
    value: object,
) -> None:
    with contextlib.suppress(
        TimeoutError,
        RuntimeError,
        WebSocketDisconnect,
    ):
        async with asyncio.timeout(_SEND_TIMEOUT_SECONDS):
            await websocket.send_json(value)


async def _stop_task(task: asyncio.Task[object] | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    done, _ = await asyncio.wait(
        {task},
        timeout=_CLEANUP_TIMEOUT_SECONDS,
    )
    if task not in done:
        task.add_done_callback(_consume_task)
        return
    _consume_task(task)


async def _bounded_cleanup(awaitable: Awaitable[object]) -> None:
    task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait(
            {task},
            timeout=_CLEANUP_TIMEOUT_SECONDS,
        )
    except BaseException:
        task.cancel()
        task.add_done_callback(_consume_task)
        raise
    if task not in done:
        task.cancel()
        task.add_done_callback(_consume_task)
        raise _CleanupFailed
    try:
        task.result()
    except (asyncio.CancelledError, Exception) as error:
        raise _CleanupFailed from error


def _consume_task(task: asyncio.Future[object]) -> None:
    with contextlib.suppress(asyncio.CancelledError, Exception):
        task.result()


def _ws_error(code: str, message: str) -> dict[str, object]:
    return {
        "type": "error",
        "error": {
            "code": code,
            "message": message,
        },
    }
