from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
from collections.abc import Generator, Iterable

import uvicorn

from botified_tts.app import Readiness, create_app
from botified_tts.config import (
    CudaPreflightError,
    InvalidConfiguration,
    Settings,
)
from botified_tts.engine import EngineError, VoxCPMEngine
from botified_tts.speech import SpeechService
from botified_tts.voices import VoiceStore

GRACEFUL_SHUTDOWN_SECONDS = 10
SHUTDOWN_TIMEOUT_SECONDS = 15
CLEANUP_TIMEOUT_SECONDS = 15
HANDLED_SIGNALS = (signal.SIGINT, signal.SIGTERM)
_LOGGER = logging.getLogger("uvicorn.error.botified_tts")


class RuntimeServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


async def serve(settings: Settings) -> None:
    engine = await VoxCPMEngine.create(settings)
    try:
        await _serve(settings, engine)
    except BaseException:
        with contextlib.suppress(BaseException):
            await _close_engine(engine)
        raise
    else:
        await _close_engine(engine)


async def _serve(settings: Settings, engine: VoxCPMEngine) -> None:
    readiness = Readiness(False)
    voices = VoiceStore(settings.data_dir / "voices")
    speech = SpeechService(engine, voices)
    app = create_app(
        api_key=settings.api_key,
        readiness=readiness,
        voices=voices,
        speech=speech,
        segment_profile=settings.segment_profile,
    )
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        workers=1,
        log_level=settings.log_level.lower(),
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )
    server = RuntimeServer(config)
    loop = asyncio.get_running_loop()
    installed_signals: list[int] = []
    http_task: asyncio.Task[None] | None = None
    fatal_task: asyncio.Task[None] | None = None
    active_error: BaseException | None = None

    def request_exit() -> None:
        readiness.ready = False
        if server.should_exit:
            server.force_exit = True
        else:
            server.should_exit = True

    try:
        for handled_signal in HANDLED_SIGNALS:
            loop.add_signal_handler(handled_signal, request_exit)
            installed_signals.append(handled_signal)

        readiness.ready = True
        _LOGGER.info(
            json.dumps(
                {
                    "event": "ready",
                    "segment_profile": settings.segment_profile,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        http_task = asyncio.create_task(server.serve())
        fatal_task = asyncio.create_task(engine.wait_for_fatal())
        done, _ = await asyncio.wait(
            (http_task, fatal_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        readiness.ready = False

        if fatal_task in done:
            await fatal_task
            raise RuntimeError("VoxCPM2 fatal waiter stopped unexpectedly")
        await http_task
    except BaseException as error:
        active_error = error
        raise
    finally:
        readiness.ready = False
        try:
            await _shutdown_tasks(
                server,
                http_task,
                fatal_task,
                graceful=not isinstance(
                    active_error,
                    asyncio.CancelledError,
                ),
            )
        except asyncio.CancelledError:
            raise
        except BaseException:
            if active_error is None:
                raise
        finally:
            for handled_signal in installed_signals:
                loop.remove_signal_handler(handled_signal)


async def _shutdown_tasks(
    server: RuntimeServer,
    http_task: asyncio.Task[None] | None,
    fatal_task: asyncio.Task[None] | None,
    *,
    graceful: bool,
) -> None:
    if http_task is None:
        return
    server.should_exit = True
    try:
        if graceful and not http_task.done():
            done, _ = await asyncio.wait(
                (http_task,),
                timeout=SHUTDOWN_TIMEOUT_SECONDS,
            )
            if not done:
                server.force_exit = True
    finally:
        await _cancel_and_reap(
            task
            for task in (http_task, fatal_task)
            if task is not None
        )


async def _close_engine(engine: VoxCPMEngine) -> None:
    close_task = asyncio.create_task(engine.close())
    try:
        done, _ = await asyncio.wait(
            (close_task,),
            timeout=CLEANUP_TIMEOUT_SECONDS,
        )
    except BaseException:
        close_task.cancel()
        with contextlib.suppress(BaseException):
            await _cancel_and_reap((close_task,))
        raise
    if done:
        await close_task
        return

    await _cancel_and_reap((close_task,))
    raise TimeoutError("VoxCPM2 engine shutdown timed out")


async def _cancel_and_reap(
    tasks: Iterable[asyncio.Task[None]],
) -> None:
    owned_tasks = tuple(tasks)
    pending = {task for task in owned_tasks if not task.done()}
    for task in pending:
        task.cancel()
    if pending:
        done, pending = await asyncio.wait(
            pending,
            timeout=CLEANUP_TIMEOUT_SECONDS,
        )
        for task in done:
            _consume_task_result(task)
    if pending:
        for task in pending:
            task.cancel()
        await asyncio.sleep(0)
    for task in owned_tasks:
        if task.done():
            _consume_task_result(task)
        else:
            task.add_done_callback(_consume_task_result)


def _consume_task_result(task: asyncio.Task[None]) -> None:
    with contextlib.suppress(BaseException):
        task.result()


def main() -> None:
    try:
        asyncio.run(serve(Settings.from_env()))
    except Exception as error:
        _LOGGER.critical(
            json.dumps(
                {"event": "fatal", "result": _fatal_result(error)},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        raise SystemExit(1) from None


def _fatal_result(error: Exception) -> str:
    if isinstance(error, InvalidConfiguration):
        return "invalid_configuration"
    if isinstance(error, (CudaPreflightError, EngineError)):
        return error.code
    return "engine_error"
