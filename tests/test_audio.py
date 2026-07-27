from __future__ import annotations

import io
import wave

import numpy as np
import pytest

from botified_tts.audio import (
    InvalidWaveform,
    float32_to_pcm_s16le,
    pcm_s16le_chunks_to_wav,
)


def test_float32_to_pcm_s16le_clips_and_scales_exactly() -> None:
    waveform = np.array(
        [-2.0, -1.0, -0.5, -0.1, 0.0, 0.1, 0.5, 1.0, 2.0],
        dtype=np.float32,
    )

    assert float32_to_pcm_s16le(waveform) == np.array(
        [-32767, -32767, -16384, -3277, 0, 3277, 16384, 32767, 32767],
        dtype="<i2",
    ).tobytes()


@pytest.mark.parametrize("sample", [np.nan, np.inf, -np.inf])
def test_float32_to_pcm_s16le_rejects_non_finite_values(sample: float) -> None:
    with pytest.raises(InvalidWaveform):
        float32_to_pcm_s16le(np.array([sample], dtype=np.float32))


def test_pcm_s16le_chunks_to_wav_preserves_order_and_sets_fixed_format() -> None:
    chunks = [b"\x01\x02\x03\x04", b"\x05\x06", b"\x07\x08\x09\x0a"]

    encoded = pcm_s16le_chunks_to_wav(iter(chunks))

    with wave.open(io.BytesIO(encoded), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 48_000
        assert wav.getnframes() == 5
        assert wav.readframes(wav.getnframes()) == b"".join(chunks)


def test_pcm_s16le_chunks_to_wav_allows_empty_audio() -> None:
    encoded = pcm_s16le_chunks_to_wav(())

    with wave.open(io.BytesIO(encoded), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 48_000
        assert wav.getnframes() == 0
        assert wav.readframes(1) == b""


def test_pcm_s16le_chunks_to_wav_rejects_each_odd_sized_chunk() -> None:
    with pytest.raises(ValueError, match="complete 16-bit samples"):
        pcm_s16le_chunks_to_wav([b"\x00", b"\x01"])
