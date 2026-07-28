from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import asdict, dataclass

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from botified_tts.audio import (
    AudioEncodingError,
    pcm_s16le_chunks_to_ogg_opus,
    pcm_s16le_chunks_to_wav,
)
from botified_tts.config import MAX_CONCURRENT_SYNTHESIS
from botified_tts.engine import EngineError
from botified_tts.schemas import (
    InputTooLarge,
    InvalidSynthesisOptions,
    SpeechRequest,
    parse_speech_request,
)
from botified_tts.segmenter import Segmenter
from botified_tts.speech import SpeechService, SynthesisSummary
from botified_tts.streaming import _StreamingSession
from botified_tts.voices import (
    MAX_UPLOAD_BYTES,
    InvalidVoice,
    VoiceMetadata,
    VoiceStore,
)

MODEL_NAME = "VoxCPM2"


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


def create_app(
    *,
    api_key: str,
    readiness: Readiness,
    voices: VoiceStore,
    speech: SpeechService,
) -> Starlette:
    if not api_key:
        raise ValueError("api_key must not be empty")

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

    def authorize_stream(websocket: WebSocket) -> tuple[str, str] | None:
        try:
            authenticate(websocket)
            require_ready()
        except _ApiError as error:
            return error.code, error.message
        return None

    async def health(_: Request) -> Response:
        return JSONResponse(
            {
                "status": "ready" if readiness.ready else "not_ready",
                "cuda": True,
                "model": MODEL_NAME,
                "sample_rate": 48_000,
            },
            status_code=200 if readiness.ready else 503,
        )

    async def synthesize(request: Request) -> Response:
        authorize(request)
        media_type = _speech_media_type(request)
        if not admission.try_acquire():
            raise _ApiError(
                503,
                "service_busy",
                "Service is busy",
                error_type="server_error",
                headers={"Retry-After": "1"},
            )
        summary = SynthesisSummary(
            id=f"req_{uuid.uuid4().hex}",
            ttfb_started_at=time.monotonic(),
        )
        result = "engine_error"
        try:
            speech_request = await _parse_speech_body(request)
            summary.set_options(speech_request.options)
            summary.accept_text(speech_request.text)
            segments = _segment_text(speech_request)
            try:
                chunks = [
                    chunk
                    async for chunk in speech.synthesize(
                        speech_request.options,
                        segments,
                        summary=summary,
                    )
                ]
            except InvalidVoice as error:
                result = "invalid_voice"
                raise _ApiError(404, "invalid_voice", str(error)) from error
            except InvalidSynthesisOptions as error:
                result = "invalid_request"
                raise _ApiError(400, "invalid_request", str(error)) from error
            except EngineError as error:
                result = "engine_error"
                raise _ApiError(
                    500,
                    "engine_error",
                    "Speech synthesis failed",
                    error_type="server_error",
                ) from error
            if media_type == "audio/ogg":
                try:
                    content = await run_in_threadpool(
                        pcm_s16le_chunks_to_ogg_opus,
                        chunks,
                    )
                except AudioEncodingError as error:
                    raise _ApiError(
                        500,
                        "engine_error",
                        "Speech encoding failed",
                        error_type="server_error",
                    ) from error
            else:
                content = pcm_s16le_chunks_to_wav(chunks)
            response = Response(
                content,
                media_type=media_type,
                headers={"Vary": "Accept"},
            )
            result = "ok"
            return response
        except _ApiError as error:
            result = error.code
            raise
        except asyncio.CancelledError:
            result = "cancelled"
            raise
        finally:
            admission.release()
            summary.log_terminal(result)

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
            authorize=authorize_stream,
            try_acquire=admission.try_acquire,
            release=admission.release,
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


def _speech_media_type(request: Request) -> str:
    accept = request.headers.get("accept")
    if accept is None:
        return "audio/wav"
    normalized = accept.strip().lower()
    if normalized in {"*/*", "audio/wav"}:
        return "audio/wav"
    if normalized == "audio/ogg":
        return "audio/ogg"
    raise _ApiError(
        406,
        "invalid_request",
        "Accept must be audio/wav or audio/ogg",
    )


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
