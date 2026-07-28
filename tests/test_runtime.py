from __future__ import annotations

import asyncio
import json
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import botified_tts.runtime as runtime
from botified_tts.config import CudaPreflightError, Settings
from botified_tts.engine import EngineError

MODEL_REVISION = "bffb3df5a29440629464e5e839f4d214c8714c3d"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=18000,
        model="openbmb/VoxCPM2",
        model_revision=MODEL_REVISION,
        gpu_device=0,
        data_dir=tmp_path,
        api_key="test-secret",
        log_level="INFO",
    )


class _FakeEngine:
    def __init__(
        self,
        wait_for_fatal: Callable[[], Awaitable[None]],
        events: list[object],
        *,
        close_error: Exception | None = None,
    ) -> None:
        self.wait_for_fatal = wait_for_fatal
        self.events = events
        self.close_error = close_error
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        self.events.append("engine.close")
        if self.close_error is not None:
            raise self.close_error


def _install_composition(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    engine: _FakeEngine,
    server_class: type,
    events: list[object],
) -> SimpleNamespace:
    voice_store = object()
    speech = object()
    app = object()
    captured = SimpleNamespace(readiness=None, config=None)

    class FakeEngineFactory:
        @classmethod
        async def create(cls, actual: Settings) -> _FakeEngine:
            events.append(("engine.create", actual))
            return engine

    def make_voice_store(root: Path) -> object:
        events.append(("voices", root))
        return voice_store

    def make_speech(actual_engine: object, voices: object) -> object:
        events.append(("speech", actual_engine, voices))
        assert actual_engine is engine
        assert voices is voice_store
        return speech

    def make_app(**kwargs: object) -> object:
        events.append(("app", kwargs))
        captured.readiness = kwargs["readiness"]
        assert kwargs == {
            "api_key": settings.api_key,
            "model": settings.model,
            "readiness": captured.readiness,
            "voices": voice_store,
            "speech": speech,
        }
        assert captured.readiness.ready is False
        return app

    def make_config(actual_app: object, **kwargs: object) -> object:
        events.append(("config", actual_app, kwargs))
        assert actual_app is app
        captured.config = SimpleNamespace(app=actual_app, **kwargs)
        return captured.config

    monkeypatch.setattr(runtime, "VoxCPMEngine", FakeEngineFactory)
    monkeypatch.setattr(runtime, "VoiceStore", make_voice_store)
    monkeypatch.setattr(runtime, "SpeechService", make_speech)
    monkeypatch.setattr(runtime, "create_app", make_app)
    monkeypatch.setattr(runtime.uvicorn, "Config", make_config)
    monkeypatch.setattr(runtime, "RuntimeServer", server_class)
    return captured


def test_normal_http_shutdown_cancels_fatal_waiter_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="uvicorn.error.botified_tts")
    async def exercise() -> tuple[_FakeEngine, SimpleNamespace, list[object]]:
        events: list[object] = []

        async def wait_for_fatal() -> None:
            events.append("fatal.wait")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("fatal.cancel")
                raise

        engine = _FakeEngine(wait_for_fatal, events)

        class FakeServer:
            def __init__(self, config: object) -> None:
                events.append(("server", config))
                self.should_exit = False
                self.force_exit = False

            async def serve(self) -> None:
                events.append(("http.serve", captured.readiness.ready))

        captured = _install_composition(
            monkeypatch,
            _settings(tmp_path),
            engine,
            FakeServer,
            events,
        )
        await runtime.serve(_settings(tmp_path))
        return engine, captured, events

    engine, captured, events = asyncio.run(exercise())

    assert captured.config.host == "127.0.0.1"
    assert captured.config.port == 18000
    assert captured.config.workers == 1
    assert captured.config.log_level == "info"
    assert captured.config.timeout_graceful_shutdown == 10
    assert events[0] == ("engine.create", _settings(tmp_path))
    assert events[1] == ("voices", tmp_path / "voices")
    assert ("http.serve", True) in events
    assert "fatal.cancel" in events
    assert captured.readiness.ready is False
    assert engine.close_calls == 1
    ready_logs = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "uvicorn.error.botified_tts"
        and json.loads(record.getMessage()).get("event") == "ready"
    ]
    assert ready_logs == [{"event": "ready"}]


def test_http_error_is_propagated_after_fatal_waiter_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def exercise() -> _FakeEngine:
        events: list[object] = []
        http_error = RuntimeError("HTTP failed")

        async def wait_for_fatal() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("fatal.cancel")
                raise

        engine = _FakeEngine(wait_for_fatal, events)

        class FailingServer:
            def __init__(self, _: object) -> None:
                self.should_exit = False
                self.force_exit = False

            async def serve(self) -> None:
                raise http_error

        _install_composition(
            monkeypatch,
            _settings(tmp_path),
            engine,
            FailingServer,
            events,
        )
        with pytest.raises(RuntimeError) as caught:
            await runtime.serve(_settings(tmp_path))
        assert caught.value is http_error
        assert events[-2:] == ["fatal.cancel", "engine.close"]
        return engine

    engine = asyncio.run(exercise())
    assert engine.close_calls == 1


def test_close_error_is_propagated_after_normal_http_shutdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def exercise() -> _FakeEngine:
        events: list[object] = []
        close_error = RuntimeError("engine close failed")

        async def wait_for_fatal() -> None:
            await asyncio.Event().wait()

        engine = _FakeEngine(
            wait_for_fatal,
            events,
            close_error=close_error,
        )

        class FinishedServer:
            def __init__(self, _: object) -> None:
                self.should_exit = False
                self.force_exit = False

            async def serve(self) -> None:
                pass

        _install_composition(
            monkeypatch,
            _settings(tmp_path),
            engine,
            FinishedServer,
            events,
        )
        with pytest.raises(RuntimeError) as caught:
            await runtime.serve(_settings(tmp_path))
        assert caught.value is close_error
        return engine

    engine = asyncio.run(exercise())
    assert engine.close_calls == 1


def test_idle_fatal_revokes_readiness_before_stopping_http_and_preserves_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def exercise() -> tuple[_FakeEngine, SimpleNamespace, list[object]]:
        events: list[object] = []
        fatal_error = RuntimeError("worker died")

        async def wait_for_fatal() -> None:
            events.append("fatal")
            raise fatal_error

        cleanup_error = RuntimeError("cleanup failed")
        engine = _FakeEngine(
            wait_for_fatal,
            events,
            close_error=cleanup_error,
        )

        class WaitingServer:
            def __init__(self, _: object) -> None:
                self._should_exit = False
                self.force_exit = False
                self.exit_requested = asyncio.Event()

            @property
            def should_exit(self) -> bool:
                return self._should_exit

            @should_exit.setter
            def should_exit(self, value: bool) -> None:
                self._should_exit = value
                if value:
                    events.append(
                        ("http.stop", captured.readiness.ready)
                    )
                    self.exit_requested.set()

            async def serve(self) -> None:
                events.append("http.wait")
                await self.exit_requested.wait()
                events.append("http.done")

        captured = _install_composition(
            monkeypatch,
            _settings(tmp_path),
            engine,
            WaitingServer,
            events,
        )
        with pytest.raises(RuntimeError) as caught:
            await runtime.serve(_settings(tmp_path))
        assert caught.value is fatal_error
        return engine, captured, events

    engine, captured, events = asyncio.run(exercise())

    assert ("http.stop", False) in events
    assert events.index(("http.stop", False)) < events.index("http.done")
    assert captured.readiness.ready is False
    assert engine.close_calls == 1


def test_fatal_shutdown_timeout_forces_and_cancels_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def exercise() -> tuple[object, _FakeEngine]:
        events: list[object] = []
        fatal_error = RuntimeError("worker died")

        async def wait_for_fatal() -> None:
            raise fatal_error

        engine = _FakeEngine(wait_for_fatal, events)

        class HangingServer:
            instance: HangingServer

            def __init__(self, _: object) -> None:
                type(self).instance = self
                self.should_exit = False
                self.force_exit = False
                self.cancelled = False

            async def serve(self) -> None:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise

        _install_composition(
            monkeypatch,
            _settings(tmp_path),
            engine,
            HangingServer,
            events,
        )
        monkeypatch.setattr(runtime, "SHUTDOWN_TIMEOUT_SECONDS", 0.001)
        with pytest.raises(RuntimeError) as caught:
            await runtime.serve(_settings(tmp_path))
        assert caught.value is fatal_error
        return HangingServer.instance, engine

    server, engine = asyncio.run(exercise())

    assert server.should_exit is True
    assert server.force_exit is True
    assert server.cancelled is True
    assert engine.close_calls == 1


def test_signal_callback_revokes_readiness_and_second_signal_forces_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def exercise() -> tuple[object, SimpleNamespace, list[int]]:
        events: list[object] = []
        handlers: dict[int, Callable[[], None]] = {}
        removed: list[int] = []
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(
            loop,
            "add_signal_handler",
            lambda number, callback: handlers.setdefault(number, callback),
        )
        monkeypatch.setattr(
            loop,
            "remove_signal_handler",
            lambda number: removed.append(number) or True,
        )

        async def wait_for_fatal() -> None:
            await asyncio.Event().wait()

        engine = _FakeEngine(wait_for_fatal, events)

        class SignalledServer:
            instance: SignalledServer

            def __init__(self, _: object) -> None:
                type(self).instance = self
                self.should_exit = False
                self.force_exit = False

            async def serve(self) -> None:
                assert captured.readiness.ready is True
                handlers[signal.SIGTERM]()
                assert captured.readiness.ready is False
                assert self.should_exit is True
                assert self.force_exit is False
                handlers[signal.SIGINT]()
                assert self.force_exit is True

        captured = _install_composition(
            monkeypatch,
            _settings(tmp_path),
            engine,
            SignalledServer,
            events,
        )
        await runtime.serve(_settings(tmp_path))
        return SignalledServer.instance, captured, removed

    server, captured, removed = asyncio.run(exercise())

    assert server.should_exit is True
    assert server.force_exit is True
    assert captured.readiness.ready is False
    assert removed == [signal.SIGINT, signal.SIGTERM]


def test_composition_failure_never_constructs_server_and_closes_engine_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def exercise() -> _FakeEngine:
        events: list[object] = []
        failure = RuntimeError("voice storage failed")

        async def wait_for_fatal() -> None:
            raise AssertionError("fatal waiter must not start")

        engine = _FakeEngine(wait_for_fatal, events)

        class FakeEngineFactory:
            @classmethod
            async def create(cls, _: Settings) -> _FakeEngine:
                events.append("engine.create")
                return engine

        def fail_voice_store(_: Path) -> object:
            raise failure

        def reject_server(_: object) -> object:
            raise AssertionError("server must not be constructed")

        monkeypatch.setattr(runtime, "VoxCPMEngine", FakeEngineFactory)
        monkeypatch.setattr(runtime, "VoiceStore", fail_voice_store)
        monkeypatch.setattr(runtime, "RuntimeServer", reject_server)

        with pytest.raises(RuntimeError) as caught:
            await runtime.serve(_settings(tmp_path))
        assert caught.value is failure
        return engine

    engine = asyncio.run(exercise())
    assert engine.close_calls == 1


def test_outer_cancellation_reaps_children_removes_signals_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def exercise() -> tuple[
        _FakeEngine,
        object,
        SimpleNamespace,
        list[int],
    ]:
        events: list[object] = []
        children_started = asyncio.Event()
        started_count = 0
        removed: list[int] = []
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "add_signal_handler", lambda *_: None)
        monkeypatch.setattr(
            loop,
            "remove_signal_handler",
            lambda number: removed.append(number) or True,
        )

        def mark_started() -> None:
            nonlocal started_count
            started_count += 1
            if started_count == 2:
                children_started.set()

        async def wait_for_fatal() -> None:
            engine.fatal_task = asyncio.current_task()
            mark_started()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                events.append("fatal.cancel")
                raise

        engine = _FakeEngine(wait_for_fatal, events)
        engine.fatal_task = None

        class WaitingServer:
            instance: WaitingServer

            def __init__(self, _: object) -> None:
                type(self).instance = self
                self.should_exit = False
                self.force_exit = False
                self.http_task: asyncio.Task[None] | None = None

            async def serve(self) -> None:
                self.http_task = asyncio.current_task()
                mark_started()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    events.append("http.cancel")
                    raise

        captured = _install_composition(
            monkeypatch,
            _settings(tmp_path),
            engine,
            WaitingServer,
            events,
        )
        serve_task = asyncio.create_task(runtime.serve(_settings(tmp_path)))
        await children_started.wait()
        serve_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await serve_task
        return engine, WaitingServer.instance, captured, removed

    engine, server, captured, removed = asyncio.run(exercise())

    assert server.should_exit is True
    assert server.http_task.done()
    assert engine.fatal_task.done()
    assert captured.readiness.ready is False
    assert removed == [signal.SIGINT, signal.SIGTERM]
    assert engine.close_calls == 1


def test_fatal_with_blocking_close_is_bounded_and_preserves_fatal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def exercise() -> tuple[
        _FakeEngine,
        RuntimeError,
        RuntimeError,
    ]:
        events: list[object] = []
        fatal_error = RuntimeError("worker died")

        async def wait_for_fatal() -> None:
            raise fatal_error

        class BlockingCloseEngine(_FakeEngine):
            close_task: asyncio.Task[None] | None = None

            async def close(self) -> None:
                self.close_calls += 1
                self.close_task = asyncio.current_task()
                await asyncio.Event().wait()

        engine = BlockingCloseEngine(wait_for_fatal, events)

        class ExitingServer:
            def __init__(self, _: object) -> None:
                self._should_exit = False
                self.force_exit = False
                self.exit_requested = asyncio.Event()

            @property
            def should_exit(self) -> bool:
                return self._should_exit

            @should_exit.setter
            def should_exit(self, value: bool) -> None:
                self._should_exit = value
                if value:
                    self.exit_requested.set()

            async def serve(self) -> None:
                await self.exit_requested.wait()

        _install_composition(
            monkeypatch,
            _settings(tmp_path),
            engine,
            ExitingServer,
            events,
        )
        monkeypatch.setattr(runtime, "CLEANUP_TIMEOUT_SECONDS", 0.001)
        serve_task = asyncio.create_task(runtime.serve(_settings(tmp_path)))
        done, _ = await asyncio.wait({serve_task}, timeout=0.2)
        if not done:
            serve_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await serve_task
            pytest.fail("runtime did not bound engine close")
        with pytest.raises(RuntimeError) as caught:
            await serve_task
        return engine, caught.value, fatal_error

    engine, caught, fatal_error = asyncio.run(exercise())

    assert caught is fatal_error
    assert engine.close_calls == 1
    assert engine.close_task.done()


def test_main_loads_environment_and_runs_serve(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    calls: list[object] = []

    monkeypatch.setattr(
        runtime.Settings,
        "from_env",
        lambda: calls.append("settings") or settings,
    )

    async def fake_serve(actual: Settings) -> None:
        calls.append(("serve", actual))

    monkeypatch.setattr(runtime, "serve", fake_serve)

    runtime.main()

    assert calls == ["settings", ("serve", settings)]


@pytest.mark.parametrize(
    ("error", "expected_result"),
    [
        (
            CudaPreflightError("cuda_unavailable", "RAW_FATAL_SENTINEL"),
            "cuda_unavailable",
        ),
        (
            CudaPreflightError("cuda_device_invalid", "RAW_FATAL_SENTINEL"),
            "cuda_device_invalid",
        ),
        (
            EngineError("model_load_failed", "RAW_FATAL_SENTINEL"),
            "model_load_failed",
        ),
        (
            EngineError("engine_error", "RAW_FATAL_SENTINEL"),
            "engine_error",
        ),
        (RuntimeError("RAW_FATAL_SENTINEL"), "engine_error"),
    ],
)
def test_main_logs_safe_fatal_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
    expected_result: str,
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(runtime.Settings, "from_env", lambda: settings)

    async def failing_serve(_: Settings) -> None:
        raise error

    monkeypatch.setattr(runtime, "serve", failing_serve)
    with pytest.raises(SystemExit) as caught:
        runtime.main()

    assert caught.value.code == 1
    fatal_logs = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "uvicorn.error.botified_tts"
        and json.loads(record.getMessage()).get("event") == "fatal"
    ]
    assert fatal_logs == [{"event": "fatal", "result": expected_result}]
    assert "RAW_FATAL_SENTINEL" not in "\n".join(
        record.getMessage() for record in caplog.records
    )
