from __future__ import annotations

import numpy as np
import pytest

from botified_tts.audio import (
    PCMChunkAggregator,
    PCM_TARGET_SAMPLES,
    InvalidWaveform,
    float32_to_pcm_s16le,
)


def _pcm(samples: int, value: int) -> bytes:
    return np.full(samples, value, dtype="<i2").tobytes()


def test_float32_to_pcm_s16le_clips_and_scales_exactly() -> None:
    waveform = np.array(
        [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0],
        dtype=np.float32,
    )

    assert float32_to_pcm_s16le(waveform) == np.array(
        [-32768, -32768, -16384, 0, 16384, 32767, 32767],
        dtype="<i2",
    ).tobytes()


@pytest.mark.parametrize("sample", [np.nan, np.inf, -np.inf])
def test_float32_to_pcm_s16le_rejects_non_finite_values(sample: float) -> None:
    with pytest.raises(InvalidWaveform):
        float32_to_pcm_s16le(np.array([sample], dtype=np.float32))


def test_aggregator_only_combines_chunks_needed_to_reach_160ms() -> None:
    chunk_60ms = _pcm(2_880, 1)
    chunk_100ms = _pcm(4_800, 2)
    chunk_200ms = _pcm(9_600, 3)
    chunk_50ms = _pcm(2_400, 4)
    aggregator = PCMChunkAggregator()

    assert PCM_TARGET_SAMPLES == 7_680
    assert aggregator.push(chunk_60ms) == ()
    assert aggregator.push(chunk_100ms) == (chunk_60ms + chunk_100ms,)
    assert aggregator.push(chunk_200ms) == (chunk_200ms,)
    assert aggregator.push(chunk_50ms) == ()
    assert aggregator.finish_segment() == (chunk_50ms,)
    assert aggregator.finish_segment() == ()


def test_aggregator_preserves_order_when_large_chunk_follows_pending_audio() -> None:
    pending = _pcm(2_880, 1)
    large = _pcm(9_600, 2)
    aggregator = PCMChunkAggregator()

    assert aggregator.push(pending) == ()
    assert aggregator.push(large) == (pending + large,)
    assert aggregator.finish_segment() == ()
