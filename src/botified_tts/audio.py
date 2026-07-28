from __future__ import annotations

import io
import subprocess
import wave
from collections.abc import Iterable

import numpy as np
from numpy.typing import NDArray


class InvalidWaveform(ValueError):
    """The inference engine returned an invalid waveform."""


class AudioEncodingError(RuntimeError):
    """The audio encoder failed to produce a complete output."""


def float32_to_pcm_s16le(waveform: NDArray[np.float32]) -> bytes:
    if (
        not isinstance(waveform, np.ndarray)
        or waveform.dtype != np.float32
        or waveform.ndim != 1
    ):
        raise InvalidWaveform("waveform must be a one-dimensional float32 array")
    if not np.isfinite(waveform).all():
        raise InvalidWaveform("waveform contains non-finite samples")

    scaled = np.rint(np.clip(waveform, -1.0, 1.0) * 32767.0)
    pcm = scaled.astype("<i2")
    return pcm.tobytes()


def pcm_s16le_chunks_to_wav(chunks: Iterable[bytes]) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48_000)
        for chunk in _validated_pcm_s16le_chunks(chunks):
            wav.writeframesraw(chunk)
    return output.getvalue()


def pcm_s16le_chunks_to_ogg_opus(chunks: Iterable[bytes]) -> bytes:
    pcm = b"".join(_validated_pcm_s16le_chunks(chunks))
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "s16le",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-i",
                "pipe:0",
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "libopus",
                "-b:a",
                "48k",
                "-application",
                "voip",
                "-vbr",
                "on",
                "-f",
                "ogg",
                "pipe:1",
            ],
            input=pcm,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AudioEncodingError("FFmpeg failed to encode audio") from error
    if result.returncode != 0 or not result.stdout:
        raise AudioEncodingError("FFmpeg failed to encode audio")
    return result.stdout


def _validated_pcm_s16le_chunks(chunks: Iterable[bytes]) -> Iterable[bytes]:
    for chunk in chunks:
        if len(chunk) % 2 != 0:
            raise ValueError("PCM chunk must contain complete 16-bit samples")
        yield chunk
