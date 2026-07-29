from __future__ import annotations

# ruff: noqa: E402

import argparse
import asyncio
import sys
import tempfile
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import cast


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from botified_tts.audio import pcm_s16le_chunks_to_wav
from botified_tts.config import ModelSource, Settings
from botified_tts.engine import VoxCPMEngine
from botified_tts.schemas import DesignVoice, ProfileVoice, SynthesisOptions
from botified_tts.speech import SpeechService
from botified_tts.voices import VoiceStore


SAMPLES_PER_CHUNK = 7_680
PCM_BYTES_PER_CHUNK = SAMPLES_PER_CHUNK * 2
MODEL_SOURCES: tuple[ModelSource, ...] = ("modelscope", "huggingface")
DESIGN_SPOKEN_SEGMENTS = (
    "你好，我是 Botified 的语音助手。",
    "今天我会用温暖自然的声音，陪你一起了解这项语音服务。",
    "希望接下来的交流清晰轻松，也让每一句回应都保持真诚和亲切。",
)
DESIGN_SPOKEN_TEXT = "".join(DESIGN_SPOKEN_SEGMENTS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Botified TTS integration checks on one CUDA GPU.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="Persistent model cache directory.",
    )
    parser.add_argument(
        "--full-source",
        choices=MODEL_SOURCES,
        required=True,
        help="Model source that runs the full synthesis path.",
    )
    return parser.parse_args()


def settings(source: ModelSource, data_dir: Path) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8000,
        model_source=source,
        gpu_device=0,
        data_dir=data_dir,
        api_key="gpu-integration-key",
        log_level="INFO",
    )


async def segments(values: Sequence[str]) -> AsyncIterator[str]:
    for value in values:
        yield value


def validate_pcm_chunk(chunk: bytes, name: str) -> None:
    if not isinstance(chunk, bytes) or len(chunk) != PCM_BYTES_PER_CHUNK:
        raise AssertionError(
            f"{name}: expected one 7680-sample mono s16le chunk",
        )


async def synthesize(
    speech: SpeechService,
    *,
    name: str,
    options: SynthesisOptions,
    text_segments: Sequence[str],
) -> list[bytes]:
    chunks: list[bytes] = []
    async for chunk in speech.synthesize(
        options,
        segments(text_segments),
    ):
        validate_pcm_chunk(chunk, name)
        chunks.append(chunk)
    if not chunks:
        raise AssertionError(f"{name}: synthesis returned no audio")
    print(f"{name}: {len(chunks)} PCM chunks", flush=True)
    return chunks


async def check_other_source(source: ModelSource, data_dir: Path) -> None:
    print(f"{source}: create, load, wait, and warmup", flush=True)
    engine = await VoxCPMEngine.create(settings(source, data_dir))
    await engine.close()
    print(f"{source}: warmup passed and engine closed", flush=True)


async def check_cancellation_recovery(speech: SpeechService) -> None:
    default_options = SynthesisOptions(voice=None, mode=None, style=None)
    stream = speech.synthesize(
        default_options,
        segments(
            (
                "这是一段用于验证生成取消的较长文本。"
                "客户端收到第一段音频后会立即关闭外层音频流，"
                "底层推理流也应随之关闭，并释放当前生成所占用的资源。",
            )
        ),
    )
    first_chunk = await anext(stream)
    validate_pcm_chunk(first_chunk, "cancel")
    await stream.aclose()

    await synthesize(
        speech,
        name="post-cancel",
        options=default_options,
        text_segments=("上一条生成取消后，同一推理池仍然可以继续工作。",),
    )


async def check_full_source(source: ModelSource, data_dir: Path) -> None:
    print(f"{source}: starting full integration path", flush=True)
    engine = await VoxCPMEngine.create(settings(source, data_dir))
    try:
        with tempfile.TemporaryDirectory(
            prefix="gpu-integration-voices-",
            dir=data_dir,
        ) as voice_directory:
            voices = VoiceStore(voice_directory)
            speech = SpeechService(engine, voices)

            design_chunks = await synthesize(
                speech,
                name="voice-design-reference",
                options=SynthesisOptions(
                    voice=DesignVoice(
                        description="温暖自然、吐字清晰、亲切而有活力的年轻女声",
                    ),
                    mode=None,
                    style=None,
                ),
                text_segments=DESIGN_SPOKEN_SEGMENTS,
            )
            profile = voices.create(
                name="gpu-integration-reference",
                source=pcm_s16le_chunks_to_wav(design_chunks),
                filename="design-reference.wav",
                prompt_text=DESIGN_SPOKEN_TEXT,
            )
            if not 3 <= profile.duration_seconds <= 60:
                raise AssertionError(
                    "voice-design-reference: duration must be 3 to 60 seconds",
                )

            profile_voice = ProfileVoice(profile.id)
            await synthesize(
                speech,
                name="controllable-clone",
                options=SynthesisOptions(
                    voice=profile_voice,
                    mode="controllable",
                    style="平静亲切，语速稍慢，带一点微笑",
                ),
                text_segments=(
                    "这是使用参考音色生成的第一段可控表达。",
                    "第二段继续保持平静亲切的说话方式，[laughing]表达也更轻松。",
                    "第三段确认多段内容始终使用同一份参考音色。",
                ),
            )
            await synthesize(
                speech,
                name="faithful-clone",
                options=SynthesisOptions(
                    voice=profile_voice,
                    mode="faithful",
                    style=None,
                ),
                text_segments=(
                    "这是忠实复刻参考音色的第一段语音。",
                    "第二段仍然使用原始参考音频和精确文本。",
                ),
            )
            await check_cancellation_recovery(speech)
    finally:
        await engine.close()
    print(f"{source}: full integration path passed", flush=True)


async def run(args: argparse.Namespace) -> None:
    data_dir = args.data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    full_source = cast(ModelSource, args.full_source)
    other_source = next(source for source in MODEL_SOURCES if source != full_source)

    await check_other_source(other_source, data_dir)
    await check_full_source(full_source, data_dir)
    print(
        "PASS: both sources completed real create and warmup; "
        f"{full_source} completed the full synthesis path",
        flush=True,
    )


def main() -> None:
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
