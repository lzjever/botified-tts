from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Generator

import uvicorn

from botified_tts.app import Readiness, create_app
from botified_tts.config import Settings
from botified_tts.engine import VoxCPMEngine
from botified_tts.speech import SpeechService
from botified_tts.voices import VoiceStore

GRACEFUL_SHUTDOWN_SECONDS = 10
SHUTDOWN_TIMEOUT_SECONDS = 15
HANDLED_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class RuntimeServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


async def serve(settings: Settings) -> None:
    engine = await VoxCPMEngine.create(settings)
    try:
        await _serve(settings, engine)
    except BaseException:
        with contextlib.suppress(Exception):
            await engine.close()
        raise
    else:
        await engine.close()


async def _serve(settings: Settings, engine: VoxCPMEngine) -> None:
    readiness = Readiness(False)
    voices = VoiceStore(settings.data_dir / "voices")
    speech = SpeechService(engine, voices)
    app = create_app(
        api_key=settings.api_key,
        model=settings.model,
        readiness=readiness,
        voices=voices,
        speech=speech,
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
        http_task = asyncio.create_task(server.serve())
        fatal_task = asyncio.create_task(engine.wait_for_fatal())
        done, _ = await asyncio.wait(
            (http_task, fatal_task),
            return_when=asyncio.FIRST_COMPLETED,
        )
        readiness.ready = False

        if fatal_task in done:
            try:
                await fatal_task
            except BaseException as error:
                fatal_error = error
            else:
                fatal_error = RuntimeError(
                    "VoxCPM2 fatal waiter stopped unexpectedly"
                )
            server.should_exit = True
            await _wait_for_http_shutdown(server, http_task)
            raise fatal_error

        fatal_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await fatal_task
        await http_task
    finally:
        readiness.ready = False
        for handled_signal in installed_signals:
            loop.remove_signal_handler(handled_signal)


async def _wait_for_http_shutdown(
    server: RuntimeServer,
    http_task: asyncio.Task[None],
) -> None:
    try:
        await asyncio.wait_for(
            asyncio.shield(http_task),
            timeout=SHUTDOWN_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        server.force_exit = True
        http_task.cancel()
        with contextlib.suppress(BaseException):
            await http_task
    except BaseException:
        pass


def main() -> None:
    asyncio.run(serve(Settings.from_env()))
