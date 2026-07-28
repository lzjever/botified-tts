from __future__ import annotations

import io
import json
import subprocess
import wave

import numpy as np
import pytest

import botified_tts.audio as audio
from botified_tts.audio import (
    AudioEncodingError,
    InvalidWaveform,
    float32_to_pcm_s16le,
    pcm_s16le_chunks_to_ogg_opus,
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


def test_pcm_s16le_chunks_to_ogg_opus_produces_decodable_fixed_format() -> None:
    sample_count = 12_000
    t = np.arange(sample_count, dtype=np.float32) / 48_000
    pcm = float32_to_pcm_s16le(
        (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    )

    encoded = pcm_s16le_chunks_to_ogg_opus(
        [pcm[:4_000], pcm[4_000:16_000], pcm[16_000:]]
    )

    assert encoded.startswith(b"OggS")
    assert b"OpusHead" in encoded[:256]

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=format_name:stream=codec_name,sample_rate,channels",
            "-of",
            "json",
            "pipe:0",
        ],
        input=encoded,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr.decode(errors="replace")
    metadata = json.loads(probe.stdout)
    assert metadata["format"]["format_name"] == "ogg"
    assert metadata["streams"] == [
        {"codec_name": "opus", "sample_rate": "48000", "channels": 1}
    ]

    decoded = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "ogg",
            "-i",
            "pipe:0",
            "-f",
            "s16le",
            "pipe:1",
        ],
        input=encoded,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert decoded.returncode == 0, decoded.stderr.decode(errors="replace")
    assert len(decoded.stdout) == len(pcm)


def test_pcm_s16le_chunks_to_ogg_opus_maps_encoder_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audio.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, b"", b"private"
        ),
    )

    with pytest.raises(AudioEncodingError, match="FFmpeg failed to encode audio"):
        pcm_s16le_chunks_to_ogg_opus([b"\x00\x00"])
