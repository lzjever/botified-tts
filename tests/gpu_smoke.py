from __future__ import annotations

# ruff: noqa: E402

import argparse
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from botified_tts.audio import pcm_s16le_chunks_to_wav
from botified_tts.config import DEFAULT_MODEL_REVISION, Settings
from botified_tts.engine import VoxCPMEngine
from botified_tts.schemas import (
    DesignVoice,
    ProfileVoice,
    SynthesisOptions,
)
from botified_tts.speech import SpeechService, SynthesisSummary
from botified_tts.streaming import _StreamingSession
from botified_tts.voices import VoiceStore


SAMPLE_RATE = 48_000
SAMPLES_PER_CHUNK = 7_680
PCM_BYTES_PER_CHUNK = SAMPLES_PER_CHUNK * 2
CHUNKS_PER_AUDIO_SECOND = SAMPLE_RATE / SAMPLES_PER_CHUNK
NORMAL_TEXT = (
    "你好，我是 Botified 的语音助手。"
    "今天我们会验证稳定、自然、清晰并且连续的语音合成效果。"
)


@dataclass(frozen=True)
class SpeechResult:
    name: str
    chunks: int
    audio_seconds: float
    generation_seconds: float
    rtf: float


class InProcessWebSocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.json_messages: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self.pcm_chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self.accepted = False
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict[str, object]:
        return await self.incoming.get()

    async def send_json(self, value: dict[str, object]) -> None:
        await self.json_messages.put(value)

    async def send_bytes(self, value: bytes) -> None:
        await self.pcm_chunks.put(value)

    async def close(self) -> None:
        self.closed = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the focused Botified TTS smoke on one CUDA GPU.",
    )
    parser.add_argument("--model", default="openbmb/VoxCPM2")
    parser.add_argument("--revision", default=DEFAULT_MODEL_REVISION)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


async def iter_segments(segments: Sequence[str]) -> AsyncIterator[str]:
    for segment in segments:
        yield segment


def assert_pcm_chunks(chunks: Sequence[bytes], name: str) -> None:
    assert chunks, f"{name}: no PCM chunks"
    assert all(
        isinstance(chunk, bytes) and len(chunk) == PCM_BYTES_PER_CHUNK
        for chunk in chunks
    ), f"{name}: every chunk must contain 7680 mono s16le samples"


async def synthesize(
    speech: SpeechService,
    output_dir: Path,
    name: str,
    options: SynthesisOptions,
    segments: Sequence[str],
) -> tuple[list[bytes], SpeechResult]:
    summary = SynthesisSummary(
        id=f"gpu_smoke_{name}",
        ttfb_started_at=time.monotonic(),
    )
    for segment in segments:
        summary.accept_text(segment)
    chunks = [
        chunk
        async for chunk in speech.synthesize(
            options,
            iter_segments(segments),
            summary=summary,
        )
    ]
    assert_pcm_chunks(chunks, name)
    terminal = summary.terminal("ok")
    assert terminal["segments"] == len(segments), (
        f"{name}: expected {len(segments)} segments, "
        f"got {terminal['segments']}"
    )
    audio_seconds = len(chunks) * SAMPLES_PER_CHUNK / SAMPLE_RATE
    generation_seconds = summary.generation_seconds
    rtf = generation_seconds / audio_seconds
    (output_dir / f"{name}.wav").write_bytes(
        pcm_s16le_chunks_to_wav(chunks)
    )
    result = SpeechResult(
        name=name,
        chunks=len(chunks),
        audio_seconds=audio_seconds,
        generation_seconds=generation_seconds,
        rtf=rtf,
    )
    print(
        f"{name}: {result.chunks} chunks, "
        f"{result.audio_seconds:.2f}s audio, RTF={result.rtf:.3f}",
        flush=True,
    )
    return chunks, result


async def streaming_smoke(
    speech: SpeechService,
    output_dir: Path,
) -> SpeechResult:
    websocket = InProcessWebSocket()
    releases = 0

    def release() -> None:
        nonlocal releases
        releases += 1

    session = _StreamingSession(
        websocket=websocket,  # type: ignore[arg-type]
        speech=speech,
        authorize=lambda _: None,
        try_acquire=lambda: True,
        release=release,
    )
    await websocket.incoming.put({"type": "start"})
    session_task = asyncio.create_task(session.run())
    ready = await asyncio.wait_for(
        websocket.json_messages.get(),
        timeout=10,
    )
    assert ready == {
        "type": "ready",
        "audio": {
            "encoding": "pcm_s16le",
            "sample_rate": SAMPLE_RATE,
            "channels": 1,
        },
    }

    for text in ("你好，", "这是逐块输入的第一句话。"):
        await websocket.incoming.put({"type": "append", "text": text})

    first_pcm = await asyncio.wait_for(
        websocket.pcm_chunks.get(),
        timeout=180,
    )
    assert len(first_pcm) == PCM_BYTES_PER_CHUNK

    for text in ("生成声音的同时，", "客户端仍然可以继续追加文本。"):
        await websocket.incoming.put({"type": "append", "text": text})
    await websocket.incoming.put({"type": "finish"})

    chunks = [first_pcm]
    terminal: dict[str, object] | None = None
    while terminal is None:
        pcm_task = asyncio.create_task(websocket.pcm_chunks.get())
        json_task = asyncio.create_task(websocket.json_messages.get())
        done, pending = await asyncio.wait(
            (pcm_task, json_task),
            timeout=180,
            return_when=asyncio.FIRST_COMPLETED,
        )
        assert done, "streaming: timed out waiting for audio or terminal event"
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if pcm_task in done:
            chunks.append(pcm_task.result())
        if json_task in done:
            event = json_task.result()
            if event.get("type") == "error":
                raise AssertionError(f"streaming: {event}")
            if event.get("type") == "done":
                terminal = event

    await asyncio.wait_for(session_task, timeout=10)
    while not websocket.pcm_chunks.empty():
        chunks.append(websocket.pcm_chunks.get_nowait())
    assert terminal == {"type": "done", "cancelled": False}
    assert releases == 1
    assert websocket.accepted and websocket.closed
    assert_pcm_chunks(chunks, "streaming")
    (output_dir / "streaming.wav").write_bytes(
        pcm_s16le_chunks_to_wav(chunks)
    )
    audio_seconds = len(chunks) * SAMPLES_PER_CHUNK / SAMPLE_RATE
    result = SpeechResult(
        name="streaming",
        chunks=len(chunks),
        audio_seconds=audio_seconds,
        generation_seconds=0.0,
        rtf=0.0,
    )
    print(
        "streaming: received the first 7680-sample PCM chunk before finish",
        flush=True,
    )
    return result


async def cancel_smoke(
    speech: SpeechService,
    output_dir: Path,
) -> SpeechResult:
    options = SynthesisOptions(voice=None, mode=None, style=None)
    stream = speech.synthesize(
        options,
        iter_segments(
            (
                "这是一段用于验证取消传播的长文本。" * 20,
            )
        ),
    )
    first_chunk = await asyncio.wait_for(anext(stream), timeout=180)
    assert len(first_chunk) == PCM_BYTES_PER_CHUNK
    await asyncio.wait_for(stream.aclose(), timeout=10)
    (output_dir / "cancelled-partial.wav").write_bytes(
        pcm_s16le_chunks_to_wav((first_chunk,))
    )

    _, result = await synthesize(
        speech,
        output_dir,
        "post-cancel",
        options,
        ("取消上一条流之后，新的请求仍然可以正常生成。",),
    )
    print("cancel: outer aclose completed and the next request passed", flush=True)
    return result


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_runtime_ready(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(
                f"runtime exited before ready with code {process.returncode}"
            )
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                payload = json.load(response)
            if response.status == 200 and payload.get("status") == "ready":
                return
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(0.1)
    raise AssertionError("runtime did not become ready")


def nano_child_pid(runtime_pid: int) -> int:
    children_path = Path(
        f"/proc/{runtime_pid}/task/{runtime_pid}/children"
    )
    child_ids = [
        int(value)
        for value in children_path.read_text(encoding="ascii").split()
    ]
    candidates: list[int] = []
    for child_id in child_ids:
        try:
            command = (
                Path(f"/proc/{child_id}/cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", "replace")
            )
        except FileNotFoundError:
            continue
        if (
            "multiprocessing.spawn" in command
            and "--multiprocessing-fork" in command
        ):
            candidates.append(child_id)
    assert len(candidates) == 1, (
        "runtime must have exactly one direct Nano multiprocessing child; "
        f"found {candidates} among {child_ids}"
    )
    return candidates[0]


def runtime_child_fatal_smoke(settings: Settings) -> None:
    port = available_port()
    runtime_settings = Settings(
        host="127.0.0.1",
        port=port,
        model=settings.model,
        model_revision=settings.model_revision,
        gpu_device=settings.gpu_device,
        data_dir=settings.data_dir,
        api_key="gpu-smoke-runtime-key",
        log_level="INFO",
    )
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("BOTIFIED_TTS_")
    }
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join(
                filter(
                    None,
                    (
                        str(PROJECT_ROOT / "src"),
                        environment.get("PYTHONPATH", ""),
                    ),
                )
            ),
            "BOTIFIED_TTS_HOST": runtime_settings.host,
            "BOTIFIED_TTS_PORT": str(runtime_settings.port),
            "BOTIFIED_TTS_MODEL": runtime_settings.model,
            "BOTIFIED_TTS_MODEL_REVISION": runtime_settings.model_revision,
            "BOTIFIED_TTS_GPU_DEVICE": str(runtime_settings.gpu_device),
            "BOTIFIED_TTS_DATA_DIR": str(runtime_settings.data_dir),
            "BOTIFIED_TTS_API_KEY": runtime_settings.api_key,
            "BOTIFIED_TTS_LOG_LEVEL": runtime_settings.log_level,
        }
    )
    with tempfile.TemporaryDirectory(
        prefix="runtime-smoke-",
        dir=settings.data_dir,
    ) as temporary:
        stdout_path = Path(temporary) / "stdout"
        stderr_path = Path(temporary) / "stderr"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "from botified_tts.runtime import main; main()",
                ],
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=stdout,
                stderr=stderr,
            )
            try:
                health_url = f"http://127.0.0.1:{port}/health"
                wait_for_runtime_ready(health_url, process)
                child_pid = nano_child_pid(process.pid)
                os.kill(child_pid, signal.SIGKILL)
                return_code = process.wait(timeout=60)
            except BaseException:
                process.kill()
                process.wait(timeout=10)
                raise

        stdout_text = stdout_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
        stderr_text = stderr_path.read_text(
            encoding="utf-8",
            errors="replace",
        )
        assert return_code != 0, "runtime exited successfully after Nano child kill"
        assert '"event":"fatal"' in stdout_text + stderr_text, (
            "runtime did not emit its terminal fatal event"
        )
        try:
            urllib.request.urlopen(health_url, timeout=1)
        except (urllib.error.URLError, TimeoutError):
            pass
        else:
            raise AssertionError("runtime remained ready after Nano child fatal")
    print(
        "runtime fatal: idle Nano child kill revoked readiness and exited nonzero",
        flush=True,
    )


async def run(args: argparse.Namespace) -> None:
    args.data_dir.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        host="127.0.0.1",
        port=8000,
        model=args.model,
        model_revision=args.revision,
        gpu_device=0,
        data_dir=args.data_dir.resolve(),
        api_key="gpu-smoke-key",
        log_level="INFO",
    )

    print("engine: checking CUDA, loading model, and running warmup", flush=True)
    engine = await VoxCPMEngine.create(settings)
    results: list[SpeechResult] = []
    voice_directory = tempfile.TemporaryDirectory(
        prefix="voices-",
        dir=settings.data_dir,
    )
    try:
        voices = VoiceStore(voice_directory.name)
        speech = SpeechService(engine, voices)
        default = SynthesisOptions(voice=None, mode=None, style=None)

        normal_chunks, normal_result = await synthesize(
            speech,
            args.output_dir,
            "normal",
            default,
            (NORMAL_TEXT,),
        )
        results.append(normal_result)
        normal_wav = pcm_s16le_chunks_to_wav(normal_chunks)
        assert normal_result.audio_seconds >= 3, (
            "normal reference must be at least three seconds"
        )
        profile = voices.create(
            name="gpu-smoke-reference",
            source=normal_wav,
            filename="normal.wav",
            prompt_text=NORMAL_TEXT,
        )

        _, result = await synthesize(
            speech,
            args.output_dir,
            "design",
            SynthesisOptions(
                voice=DesignVoice("温暖、自然、略带微笑的年轻女性声音"),
                mode=None,
                style=None,
            ),
            ("欢迎使用 Botified 语音服务。",),
        )
        results.append(result)

        _, result = await synthesize(
            speech,
            args.output_dir,
            "controllable-clone",
            SynthesisOptions(
                voice=ProfileVoice(profile.id),
                mode="controllable",
                style="平静、亲切、语速稍慢",
            ),
            ("这是参考音色下的可控表达测试。",),
        )
        results.append(result)

        _, result = await synthesize(
            speech,
            args.output_dir,
            "faithful-clone",
            SynthesisOptions(
                voice=ProfileVoice(profile.id),
                mode="faithful",
                style=None,
            ),
            ("这是忠实复刻参考音色的测试。",),
        )
        results.append(result)

        _, result = await synthesize(
            speech,
            args.output_dir,
            "official-tags",
            default,
            ("[Uhm] 我们继续。[laughing] 今天真开心。[sigh] 放松一下。",),
        )
        results.append(result)

        _, result = await synthesize(
            speech,
            args.output_dir,
            "continuation",
            default,
            (
                "第一段介绍已经完成。",
                "第二段沿用上一段的生成状态。",
                "第三段继续保持自然和连贯。",
            ),
        )
        results.append(result)

        results.append(await streaming_smoke(speech, args.output_dir))
        results.append(await cancel_smoke(speech, args.output_dir))
    finally:
        try:
            await engine.close()
        finally:
            voice_directory.cleanup()

    measured = [result for result in results if result.generation_seconds > 0]
    total_generation = sum(result.generation_seconds for result in measured)
    total_audio = sum(result.audio_seconds for result in measured)
    aggregate_rtf = total_generation / total_audio
    assert aggregate_rtf < 1, f"aggregate RTF must be below 1, got {aggregate_rtf:.3f}"
    assert CHUNKS_PER_AUDIO_SECOND == 6.25
    print(
        f"performance: aggregate RTF={aggregate_rtf:.3f}; "
        "every chunk=7680 samples=160ms=6.25 chunks/audio-second",
        flush=True,
    )

    runtime_child_fatal_smoke(settings)
    print(
        f"PASS: wrote {len(list(args.output_dir.glob('*.wav')))} WAV files "
        f"to {args.output_dir}",
        flush=True,
    )


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
