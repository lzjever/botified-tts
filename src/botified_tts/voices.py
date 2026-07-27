from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import wave
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


MAX_UPLOAD_BYTES = 25 * 1024 * 1024
REFERENCE_SAMPLE_RATE = 16_000
MIN_REFERENCE_SECONDS = 3
MAX_REFERENCE_SECONDS = 60
SUPPORTED_EXTENSIONS = {".wav", ".flac", ".mp3"}
VOICE_ID_PATTERN = re.compile(r"voice_[0-9a-f]{32}")


class InvalidVoice(ValueError):
    """A voice reference or its metadata is invalid."""


@dataclass(frozen=True)
class VoiceMetadata:
    id: str
    name: str
    prompt_text: str | None
    duration_seconds: float
    created_at: str


@dataclass(frozen=True)
class VoiceSnapshot:
    metadata: VoiceMetadata
    reference_wav: bytes


class VoiceStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._remove_stale_work_directories()

    def create(
        self,
        *,
        name: str,
        source: bytes,
        filename: str,
        prompt_text: str | None = None,
    ) -> VoiceMetadata:
        if not isinstance(name, str) or not name.strip():
            raise InvalidVoice("voice name must be a non-empty string")
        if prompt_text is not None and (
            not isinstance(prompt_text, str) or not prompt_text.strip()
        ):
            raise InvalidVoice(
                "prompt text must be a non-empty string when provided"
            )
        if not isinstance(source, bytes) or not source:
            raise InvalidVoice("voice reference must not be empty")
        if len(source) > MAX_UPLOAD_BYTES:
            raise InvalidVoice("voice reference exceeds 25 MiB")
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise InvalidVoice("voice reference must be WAV, FLAC, or MP3")

        work_path = Path(tempfile.mkdtemp(prefix=".tmp-", dir=self._root))
        try:
            source_path = work_path / f"source{extension}"
            reference_path = work_path / "reference.wav"
            source_path.write_bytes(source)
            self._decode_reference(source_path, reference_path)
            duration_seconds = self._validate_reference(reference_path)

            voice_id = f"voice_{uuid.uuid4().hex}"
            metadata = VoiceMetadata(
                id=voice_id,
                name=name,
                prompt_text=prompt_text,
                duration_seconds=duration_seconds,
                created_at=_utc_now(),
            )
            (work_path / "metadata.json").write_text(
                json.dumps(
                    asdict(metadata),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            source_path.unlink()
            with self._lock:
                os.rename(work_path, self._root / voice_id)
            return metadata
        finally:
            if work_path.exists():
                shutil.rmtree(work_path)

    def get(self, voice_id: str) -> VoiceMetadata | None:
        if not _valid_voice_id(voice_id):
            return None
        with self._lock:
            try:
                return self._read_metadata(self._root / voice_id)
            except FileNotFoundError:
                return None

    def list(self) -> tuple[VoiceMetadata, ...]:
        with self._lock:
            metadata = [
                self._read_metadata(path)
                for path in self._root.iterdir()
                if path.is_dir() and _valid_voice_id(path.name)
            ]
        return tuple(sorted(metadata, key=lambda item: item.id))

    def get_snapshot(self, voice_id: str) -> VoiceSnapshot | None:
        if not _valid_voice_id(voice_id):
            return None
        with self._lock:
            voice_path = self._root / voice_id
            try:
                metadata = self._read_metadata(voice_path)
                reference_wav = (voice_path / "reference.wav").read_bytes()
            except FileNotFoundError:
                return None
            return VoiceSnapshot(
                metadata=metadata,
                reference_wav=reference_wav,
            )

    def delete(self, voice_id: str) -> bool:
        if not _valid_voice_id(voice_id):
            return False
        with self._lock:
            voice_path = self._root / voice_id
            deleting_path = self._root / f".delete-{voice_id}-{uuid.uuid4().hex}"
            try:
                os.rename(voice_path, deleting_path)
            except FileNotFoundError:
                return False
            shutil.rmtree(deleting_path)
            return True

    def _decode_reference(
        self,
        source_path: Path,
        reference_path: Path,
    ) -> None:
        try:
            completed = subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(source_path),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-sn",
                    "-dn",
                    "-ac",
                    "1",
                    "-ar",
                    str(REFERENCE_SAMPLE_RATE),
                    "-c:a",
                    "pcm_s16le",
                    str(reference_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InvalidVoice("voice reference could not be decoded") from error
        if completed.returncode != 0:
            raise InvalidVoice("voice reference could not be decoded")

    def _validate_reference(self, reference_path: Path) -> float:
        try:
            with wave.open(str(reference_path), "rb") as reference:
                if (
                    reference.getnchannels() != 1
                    or reference.getsampwidth() != 2
                    or reference.getframerate() != REFERENCE_SAMPLE_RATE
                    or reference.getcomptype() != "NONE"
                ):
                    raise InvalidVoice("decoded voice reference is not standard PCM")
                frame_count = reference.getnframes()
                frames = reference.readframes(frame_count)
        except (OSError, EOFError, wave.Error) as error:
            raise InvalidVoice("decoded voice reference is not valid WAV") from error

        duration_seconds = frame_count / REFERENCE_SAMPLE_RATE
        if not MIN_REFERENCE_SECONDS <= duration_seconds <= MAX_REFERENCE_SECONDS:
            raise InvalidVoice("voice reference duration must be 3 to 60 seconds")
        if not frames or not any(frames):
            raise InvalidVoice("voice reference must not be silent")
        return duration_seconds

    def _read_metadata(self, voice_path: Path) -> VoiceMetadata:
        payload = json.loads(
            (voice_path / "metadata.json").read_text(encoding="utf-8")
        )
        expected = {
            "id",
            "name",
            "prompt_text",
            "duration_seconds",
            "created_at",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise InvalidVoice("stored voice metadata is invalid")
        metadata = VoiceMetadata(**payload)
        if (
            metadata.id != voice_path.name
            or not _valid_voice_id(metadata.id)
            or not isinstance(metadata.name, str)
            or not isinstance(metadata.prompt_text, (str, type(None)))
            or not isinstance(metadata.duration_seconds, (int, float))
            or not isinstance(metadata.created_at, str)
        ):
            raise InvalidVoice("stored voice metadata is invalid")
        return metadata

    def _remove_stale_work_directories(self) -> None:
        for path in self._root.iterdir():
            if path.name.startswith((".tmp-", ".delete-")):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()


def _valid_voice_id(value: object) -> bool:
    return isinstance(value, str) and VOICE_ID_PATTERN.fullmatch(value) is not None


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
