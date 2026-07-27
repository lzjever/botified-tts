from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


SAMPLE_RATE = 48_000
PCM_BYTES_PER_SAMPLE = 2
PCM_TARGET_SAMPLES = SAMPLE_RATE * 160 // 1000
PCM_TARGET_BYTES = PCM_TARGET_SAMPLES * PCM_BYTES_PER_SAMPLE


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

    scaled = np.clip(waveform, -1.0, 1.0) * 32768.0
    pcm = np.clip(scaled, -32768.0, 32767.0).astype("<i2")
    return pcm.tobytes()


class PCMChunkAggregator:
    def __init__(self) -> None:
        self._pending = bytearray()

    def push(self, chunk: bytes) -> tuple[bytes, ...]:
        if len(chunk) % PCM_BYTES_PER_SAMPLE:
            raise ValueError("PCM chunk must contain complete int16 samples")
        if not chunk:
            return ()

        if not self._pending and len(chunk) >= PCM_TARGET_BYTES:
            return (chunk,)

        self._pending.extend(chunk)
        if len(self._pending) < PCM_TARGET_BYTES:
            return ()

        combined = bytes(self._pending)
        self._pending.clear()
        return (combined,)

    def finish_segment(self) -> tuple[bytes, ...]:
        if not self._pending:
            return ()
        tail = bytes(self._pending)
        self._pending.clear()
        return (tail,)
