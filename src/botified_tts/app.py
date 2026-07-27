from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Literal

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from botified_tts.audio import pcm_s16le_chunks_to_wav
from botified_tts.config import MAX_CONCURRENT_SYNTHESIS
from botified_tts.engine import EngineError
from botified_tts.schemas import (
    AppendMessage,
    CancelMessage,
    FinishMessage,
    FlushMessage,
    InputTooLarge,
    InvalidSynthesisOptions,
    SpeechRequest,
    StartMessage,
    parse_client_message,
    parse_speech_request,
)
from botified_tts.segmenter import Segmenter
from botified_tts.speech import SpeechService
from botified_tts.voices import (
    MAX_UPLOAD_BYTES,
    InvalidVoice,
    VoiceMetadata,
    VoiceStore,
)

_SEGMENT_DEADLINE_SECONDS = 0.8
_IDLE_TIMEOUT_SECONDS = 60.0
_SEND_TIMEOUT_SECONDS = 5.0
_SESSION_TEXT_MAX_BYTES = 64 * 1024
_SEGMENT_END = object()


@dataclass
class Readiness:
    ready: bool = False


class _ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        error_type: str = "invalid_request_error",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.error_type = error_type
        self.headers = headers


class _Admission:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0

    def try_acquire(self) -> bool:
        if self._active >= self._limit:
            return False
        self._active += 1
        return True

    def release(self) -> None:
        self._active -= 1


class _IdleTimeout(Exception):
    pass


class _ClientTooSlow(Exception):
    pass


class _StreamingSession:
    def __init__(
        self,
        *,
        websocket: WebSocket,
        speech: SpeechService,
        admission: _Admission,
        authenticate: Callable[[Request | WebSocket], None],
        require_ready: Callable[[], None],
    ) -> None:
        self._websocket = websocket
        self._speech = speech
        self._admission = admission
        self._authenticate = authenticate
        self._require_ready = require_ready

    async def run(self) -> None:
        acquired = False
        terminal: dict[str, object] | None = None
        receive_task: asyncio.Task[Literal["finish", "cancel"]] | None = None
        generate_task: asyncio.Task[None] | None = None
        cancel_event = asyncio.Event()

        await self._websocket.accept()
        try:
            self._authenticate(self._websocket)
            self._require_ready()
            try:
                async with asyncio.timeout(_IDLE_TIMEOUT_SECONDS):
                    first = await _receive_client_message(self._websocket)
            except TimeoutError:
                raise _IdleTimeout from None
            if not isinstance(first, StartMessage):
                raise InvalidSynthesisOptions(
                    "first client message must be start"
                )
            if not self._admission.try_acquire():
                raise _ApiError(
                    503,
                    "service_busy",
                    "Service is busy",
                    error_type="server_error",
                )
            acquired = True
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
                self._receive_loop(queue, cancel_event)
            )
            generate_task = asyncio.create_task(
                self._generate_and_send(
                    first,
                    queue,
                    cancel_event,
                )
            )
            cancelled = await self._coordinate(
                receive_task,
                generate_task,
                cancel_event,
            )
            terminal = {"type": "done", "cancelled": cancelled}
        except _IdleTimeout:
            terminal = {"type": "done", "cancelled": True}
        except _ClientTooSlow:
            terminal = _ws_error(
                "client_too_slow",
                "Client is too slow",
            )
        except InputTooLarge as error:
            terminal = _ws_error("input_too_large", str(error))
        except InvalidVoice as error:
            terminal = _ws_error("invalid_voice", str(error))
        except InvalidSynthesisOptions as error:
            terminal = _ws_error("invalid_request", str(error))
        except EngineError:
            terminal = _ws_error(
                "engine_error",
                "Speech synthesis failed",
            )
        except _ApiError as error:
            terminal = _ws_error(error.code, error.message)
        except WebSocketDisconnect:
            terminal = None
        except asyncio.CancelledError:
            raise
        except Exception:
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
                self._admission.release()
            with contextlib.suppress(
                RuntimeError,
                WebSocketDisconnect,
            ):
                await self._websocket.close()

    async def _coordinate(
        self,
        receive_task: asyncio.Task[Literal["finish", "cancel"]],
        generate_task: asyncio.Task[None],
        cancel_event: asyncio.Event,
    ) -> bool:
        done, _ = await asyncio.wait(
            {receive_task, generate_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if receive_task in done:
            outcome = receive_task.result()
            if outcome == "finish":
                await generate_task
                return False
            cancel_event.set()
            await _stop_task(generate_task)
            return True

        await generate_task
        if not receive_task.done():
            raise EngineError(
                "engine_error",
                "speech stream ended before client finish",
            )
        return receive_task.result() == "cancel"

    async def _receive_loop(
        self,
        queue: asyncio.Queue[str | object],
        cancel_event: asyncio.Event,
    ) -> Literal["finish", "cancel"]:
        segmenter = Segmenter()
        loop = asyncio.get_running_loop()
        segment_deadline: float | None = None
        idle_deadline = loop.time() + _IDLE_TIMEOUT_SECONDS
        accepted_bytes = 0

        while True:
            now = loop.time()
            next_deadline = (
                idle_deadline
                if segment_deadline is None
                else min(idle_deadline, segment_deadline)
            )
            try:
                async with asyncio.timeout(max(0.0, next_deadline - now)):
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
            idle_deadline = now + _IDLE_TIMEOUT_SECONDS
            if isinstance(message, AppendMessage):
                text_bytes = len(message.text.encode("utf-8"))
                if accepted_bytes + text_bytes > _SESSION_TEXT_MAX_BYTES:
                    raise InputTooLarge(
                        "WebSocket session text exceeds 65536 UTF-8 bytes"
                    )
                accepted_bytes += text_bytes
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
                return "finish"

            if isinstance(message, CancelMessage):
                segment_deadline = None
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
    ) -> None:
        stream = self._speech.synthesize(
            start.options,
            _segment_source(queue, cancel_event),
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
                await stream.aclose()
            except Exception:
                if not primary_failure:
                    raise


def create_app(
    *,
    api_key: str,
    model: str,
    readiness: Readiness,
    voices: VoiceStore,
    speech: SpeechService,
) -> Starlette:
    if not api_key:
        raise ValueError("api_key must not be empty")
    if not model:
        raise ValueError("model must not be empty")

    expected_api_key_digest = hashlib.sha256(api_key.encode("utf-8")).digest()
    admission = _Admission(MAX_CONCURRENT_SYNTHESIS)

    def authenticate(request: Request | WebSocket) -> None:
        values = [
            value
            for name, value in request.scope.get("headers", ())
            if name.lower() == b"authorization"
        ]
        authorization = values[0] if len(values) == 1 else b""
        prefix = b"Bearer "
        candidate = (
            authorization[len(prefix) :]
            if authorization.startswith(prefix)
            else b""
        )
        candidate_digest = hashlib.sha256(candidate).digest()
        if not (
            len(values) == 1
            and candidate
            and candidate.isascii()
            and hmac.compare_digest(candidate_digest, expected_api_key_digest)
        ):
            raise _ApiError(
                401,
                "invalid_api_key",
                "Invalid authentication credentials",
                error_type="authentication_error",
            )

    def require_ready() -> None:
        if not readiness.ready:
            raise _ApiError(
                500,
                "engine_error",
                "Service is not ready",
                error_type="server_error",
            )

    def authorize(request: Request) -> None:
        authenticate(request)
        require_ready()

    async def health(_: Request) -> Response:
        return JSONResponse(
            {
                "status": "ready" if readiness.ready else "not_ready",
                "cuda": True,
                "model": model,
                "sample_rate": 48_000,
            },
            status_code=200 if readiness.ready else 503,
        )

    async def synthesize(request: Request) -> Response:
        authorize(request)
        if not admission.try_acquire():
            raise _ApiError(
                503,
                "service_busy",
                "Service is busy",
                error_type="server_error",
                headers={"Retry-After": "1"},
            )
        try:
            speech_request = await _parse_speech_body(request)
            segments = _segment_text(speech_request)
            try:
                chunks = [
                    chunk
                    async for chunk in speech.synthesize(
                        speech_request.options,
                        segments,
                    )
                ]
            except InvalidVoice as error:
                raise _ApiError(404, "invalid_voice", str(error)) from error
            except InvalidSynthesisOptions as error:
                raise _ApiError(400, "invalid_request", str(error)) from error
            except EngineError as error:
                raise _ApiError(
                    500,
                    "engine_error",
                    "Speech synthesis failed",
                    error_type="server_error",
                ) from error
            return Response(
                pcm_s16le_chunks_to_wav(chunks),
                media_type="audio/wav",
            )
        finally:
            admission.release()

    async def create_voice(request: Request) -> Response:
        authorize(request)
        name, source, filename, prompt_text = await _voice_form(request)
        try:
            metadata = await run_in_threadpool(
                voices.create,
                name=name,
                source=source,
                filename=filename,
                prompt_text=prompt_text,
            )
        except InvalidVoice as error:
            raise _ApiError(400, "invalid_request", str(error)) from error
        return JSONResponse(_voice_object(metadata), status_code=201)

    async def list_voices(request: Request) -> Response:
        authorize(request)
        metadata = await run_in_threadpool(voices.list)
        return JSONResponse(
            {
                "object": "list",
                "data": [_voice_object(item) for item in metadata],
            }
        )

    async def delete_voice(request: Request) -> Response:
        authorize(request)
        deleted = await run_in_threadpool(
            voices.delete,
            request.path_params["voice_id"],
        )
        if not deleted:
            raise _ApiError(
                404,
                "invalid_voice",
                "Voice profile does not exist",
            )
        return Response(status_code=204)

    async def stream_speech(websocket: WebSocket) -> None:
        await _StreamingSession(
            websocket=websocket,
            speech=speech,
            admission=admission,
            authenticate=authenticate,
            require_ready=require_ready,
        ).run()

    app = Starlette(
        debug=False,
        routes=[
            Route("/health", health, methods=["GET"]),
            Route("/v1/speech", synthesize, methods=["POST"]),
            Route("/v1/voices", create_voice, methods=["POST"]),
            Route("/v1/voices", list_voices, methods=["GET"]),
            Route(
                "/v1/voices/{voice_id}",
                delete_voice,
                methods=["DELETE"],
            ),
            WebSocketRoute("/v1/speech/stream", stream_speech),
        ],
        exception_handlers={
            _ApiError: _api_error_response,
            HTTPException: _http_error_response,
            Exception: _internal_error_response,
        },
    )
    return app


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
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task


def _ws_error(code: str, message: str) -> dict[str, object]:
    return {
        "type": "error",
        "error": {
            "code": code,
            "message": message,
        },
    }


async def _parse_speech_body(request: Request) -> SpeechRequest:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        "application/json"
    ):
        raise _ApiError(
            400,
            "invalid_request",
            "Content-Type must be application/json",
        )
    try:
        value = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise _ApiError(
            400,
            "invalid_request",
            "Request body must be valid JSON",
        ) from None
    try:
        return parse_speech_request(value)
    except InputTooLarge as error:
        raise _ApiError(413, "input_too_large", str(error)) from error
    except InvalidSynthesisOptions as error:
        raise _ApiError(400, "invalid_request", str(error)) from error


async def _segment_text(request: SpeechRequest) -> AsyncIterator[str]:
    segmenter = Segmenter()
    for segment in (*segmenter.append(request.text), *segmenter.finish()):
        yield segment


async def _voice_form(
    request: Request,
) -> tuple[str, bytes, str, str | None]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        "multipart/form-data"
    ):
        raise _ApiError(
            400,
            "invalid_request",
            "Content-Type must be multipart/form-data",
        )
    form = await request.form(max_files=1, max_fields=2)

    try:
        fields: dict[str, str | UploadFile] = {}
        for name, value in form.multi_items():
            if name not in {"name", "file", "prompt_text"}:
                raise _ApiError(
                    400,
                    "invalid_request",
                    f"Unknown multipart field: {name}",
                )
            if name in fields:
                raise _ApiError(
                    400,
                    "invalid_request",
                    f"Multipart field must not be repeated: {name}",
                )
            fields[name] = value

        name = fields.get("name")
        upload = fields.get("file")
        prompt_text = fields.get("prompt_text")
        if not isinstance(name, str):
            raise _ApiError(400, "invalid_request", "name is required")
        if not isinstance(upload, UploadFile) or not upload.filename:
            raise _ApiError(400, "invalid_request", "file is required")
        if prompt_text is not None and not isinstance(prompt_text, str):
            raise _ApiError(
                400,
                "invalid_request",
                "prompt_text must be a string",
            )

        source = await upload.read(MAX_UPLOAD_BYTES + 1)
        filename = upload.filename
    finally:
        await form.close()
    if len(source) > MAX_UPLOAD_BYTES:
        raise _ApiError(
            400,
            "invalid_request",
            "voice reference exceeds 25 MiB",
        )
    return name, source, filename, prompt_text


def _voice_object(metadata: VoiceMetadata) -> dict[str, object]:
    return asdict(metadata)


def _api_error_response(_: Request, error: _ApiError) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "message": error.message,
                "type": error.error_type,
                "param": None,
                "code": error.code,
            }
        },
        status_code=error.status_code,
        headers=error.headers,
    )


def _http_error_response(request: Request, _: HTTPException) -> JSONResponse:
    return _api_error_response(
        request,
        _ApiError(
            400,
            "invalid_request",
            "Invalid request",
        ),
    )


def _internal_error_response(request: Request, _: Exception) -> JSONResponse:
    return _api_error_response(
        request,
        _ApiError(
            500,
            "engine_error",
            "Internal server error",
            error_type="server_error",
        ),
    )
