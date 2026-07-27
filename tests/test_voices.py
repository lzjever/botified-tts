from __future__ import annotations

import io
import json
import re
import subprocess
import wave
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from botified_tts.voices import (
    MAX_UPLOAD_BYTES,
    InvalidVoice,
    VoiceSnapshot,
    VoiceStore,
)


VOICE_ID = re.compile(r"voice_[0-9a-f]{32}")


def _wav_bytes(
    duration_seconds: int,
    *,
    sample_rate: int = 8_000,
    channels: int = 1,
    single_lsb: bool = False,
    silent: bool = False,
) -> bytes:
    samples = np.zeros(duration_seconds * sample_rate, dtype="<i2")
    if single_lsb:
        samples[-1] = 1
    elif not silent:
        samples[::2] = 1_000
        samples[1::2] = -1_000
    if channels > 1:
        samples = np.repeat(samples[:, None], channels, axis=1).reshape(-1)

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(samples.tobytes())
    return output.getvalue()


def _flac_bytes(wav_bytes: bytes) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            "-f",
            "flac",
            "pipe:1",
        ],
        input=wav_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return result.stdout


def test_real_flac_create_persists_standard_wav_and_delete_preserves_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "voices"
    store = VoiceStore(root)
    source = _flac_bytes(_wav_bytes(3, sample_rate=8_000, channels=2))

    metadata = store.create(
        name="assistant",
        source=source,
        filename="reference.flac",
        prompt_text="Reference transcript.",
    )

    assert VOICE_ID.fullmatch(metadata.id)
    assert metadata.name == "assistant"
    assert metadata.prompt_text == "Reference transcript."
    assert metadata.duration_seconds == pytest.approx(3.0)
    assert datetime.fromisoformat(metadata.created_at.replace("Z", "+00:00"))
    assert store.list() == (metadata,)
    assert store.get(metadata.id) == metadata

    voice_dir = root / metadata.id
    assert {path.name for path in voice_dir.iterdir()} == {
        "metadata.json",
        "reference.wav",
    }
    assert set(json.loads((voice_dir / "metadata.json").read_text())) == {
        "id",
        "name",
        "prompt_text",
        "duration_seconds",
        "created_at",
    }

    snapshot = store.get_snapshot(metadata.id)
    assert snapshot == VoiceSnapshot(
        metadata=metadata,
        reference_wav=(voice_dir / "reference.wav").read_bytes(),
    )
    assert snapshot is not None
    with wave.open(io.BytesIO(snapshot.reference_wav), "rb") as reference:
        assert reference.getnchannels() == 1
        assert reference.getsampwidth() == 2
        assert reference.getframerate() == 16_000
        assert reference.getnframes() == 48_000

    reopened = VoiceStore(root)
    assert reopened.get_snapshot(metadata.id) == snapshot
    assert reopened.delete(metadata.id) is True
    assert reopened.get(metadata.id) is None
    assert reopened.get_snapshot(metadata.id) is None
    assert reopened.delete(metadata.id) is False
    assert snapshot.reference_wav
    assert tuple(root.iterdir()) == ()


@pytest.mark.parametrize(
    ("duration_seconds", "accepted"),
    [(2, False), (3, True), (60, True), (61, False)],
)
def test_duration_range_is_inclusive(
    tmp_path: Path,
    duration_seconds: int,
    accepted: bool,
) -> None:
    store = VoiceStore(tmp_path / "voices")

    if accepted:
        metadata = store.create(
            name="boundary",
            source=_wav_bytes(duration_seconds),
            filename="reference.wav",
        )
        assert metadata.duration_seconds == pytest.approx(duration_seconds)
    else:
        with pytest.raises(InvalidVoice):
            store.create(
                name="boundary",
                source=_wav_bytes(duration_seconds),
                filename="reference.wav",
            )
        assert store.list() == ()
        assert tuple((tmp_path / "voices").iterdir()) == ()


def test_only_all_zero_audio_is_rejected_as_silence(tmp_path: Path) -> None:
    store = VoiceStore(tmp_path / "voices")

    with pytest.raises(InvalidVoice):
        store.create(
            name="silent",
            source=_wav_bytes(3, sample_rate=16_000, silent=True),
            filename="reference.wav",
        )

    metadata = store.create(
        name="quiet",
        source=_wav_bytes(3, sample_rate=16_000, single_lsb=True),
        filename="reference.wav",
    )
    assert store.get(metadata.id) == metadata


@pytest.mark.parametrize(
    ("filename", "source"),
    [
        ("reference.wav", b"not audio"),
        ("reference.ogg", b"not a supported input"),
        ("reference.wav", b"\0" * (MAX_UPLOAD_BYTES + 1)),
    ],
)
def test_invalid_create_leaves_no_partial_resource(
    tmp_path: Path,
    filename: str,
    source: bytes,
) -> None:
    root = tmp_path / "voices"
    store = VoiceStore(root)

    with pytest.raises(InvalidVoice):
        store.create(name="invalid", source=source, filename=filename)

    assert store.list() == ()
    assert tuple(root.iterdir()) == ()


def test_invalid_voice_id_never_becomes_a_filesystem_path(tmp_path: Path) -> None:
    store = VoiceStore(tmp_path / "voices")

    assert store.get("../outside") is None
    assert store.get_snapshot("../outside") is None
    assert store.delete("../outside") is False
