from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


class InvalidWaveform(ValueError):
    """The inference engine returned an invalid waveform."""


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
