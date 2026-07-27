from __future__ import annotations

import numpy as np
import pytest

from botified_tts.audio import (
    InvalidWaveform,
    float32_to_pcm_s16le,
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
