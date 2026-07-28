from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


MAX_CONCURRENT_SYNTHESIS = 16
ENV_PREFIX = "BOTIFIED_TTS_"
ENV_NAMES = {
    "BOTIFIED_TTS_HOST",
    "BOTIFIED_TTS_PORT",
    "BOTIFIED_TTS_MODEL_SOURCE",
    "BOTIFIED_TTS_GPU_DEVICE",
    "BOTIFIED_TTS_DATA_DIR",
    "BOTIFIED_TTS_API_KEY",
    "BOTIFIED_TTS_LOG_LEVEL",
}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
API_KEY_PATTERN = re.compile(r"[A-Za-z0-9._~-]+")
ModelSource = Literal["modelscope", "huggingface"]
CudaErrorCode = Literal["cuda_unavailable", "cuda_device_invalid"]


class InvalidConfiguration(ValueError):
    """Application environment configuration is invalid."""


class CudaPreflightError(RuntimeError):
    """CUDA is unavailable or the selected device does not exist."""

    def __init__(self, code: CudaErrorCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    model_source: ModelSource
    gpu_device: int
    data_dir: Path
    api_key: str
    log_level: str

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> Settings:
        values = os.environ if environ is None else environ
        unknown = sorted(
            name
            for name in values
            if name.startswith(ENV_PREFIX) and name not in ENV_NAMES
        )
        if unknown:
            raise InvalidConfiguration(
                f"unknown environment variable: {unknown[0]}"
            )

        host = _plain_value(values, "BOTIFIED_TTS_HOST", "0.0.0.0")
        port = _decimal_value(
            values,
            "BOTIFIED_TTS_PORT",
            default=8000,
            minimum=1,
            maximum=65535,
        )
        try:
            model_source = values["BOTIFIED_TTS_MODEL_SOURCE"]
        except KeyError as error:
            raise InvalidConfiguration(
                "BOTIFIED_TTS_MODEL_SOURCE is required"
            ) from error
        if model_source not in ("modelscope", "huggingface"):
            raise InvalidConfiguration(
                "BOTIFIED_TTS_MODEL_SOURCE must be modelscope or huggingface"
            )

        gpu_device = _decimal_value(
            values,
            "BOTIFIED_TTS_GPU_DEVICE",
            default=0,
            minimum=0,
        )
        data_dir_value = _plain_value(
            values,
            "BOTIFIED_TTS_DATA_DIR",
            "/data",
        )
        data_dir = Path(data_dir_value)
        if not data_dir.is_absolute():
            raise InvalidConfiguration(
                "BOTIFIED_TTS_DATA_DIR must be an absolute path"
            )

        try:
            api_key = values["BOTIFIED_TTS_API_KEY"]
        except KeyError as error:
            raise InvalidConfiguration(
                "BOTIFIED_TTS_API_KEY is required"
            ) from error
        if (
            not isinstance(api_key, str)
            or API_KEY_PATTERN.fullmatch(api_key) is None
        ):
            raise InvalidConfiguration(
                "BOTIFIED_TTS_API_KEY must match [A-Za-z0-9._~-]+ "
                "without quotes"
            )

        log_level = _plain_value(
            values,
            "BOTIFIED_TTS_LOG_LEVEL",
            "INFO",
        )
        if log_level not in LOG_LEVELS:
            raise InvalidConfiguration(
                "BOTIFIED_TTS_LOG_LEVEL must be DEBUG, INFO, WARNING, "
                "ERROR, or CRITICAL"
            )

        return cls(
            host=host,
            port=port,
            model_source=model_source,
            gpu_device=gpu_device,
            data_dir=data_dir,
            api_key=api_key,
            log_level=log_level,
        )


@dataclass(frozen=True)
class CudaDeviceInfo:
    index: int
    name: str
    total_memory_bytes: int
    capability: tuple[int, int]


def require_cuda(device: int) -> CudaDeviceInfo:
    if isinstance(device, bool) or not isinstance(device, int) or device < 0:
        raise CudaPreflightError(
            "cuda_device_invalid",
            "CUDA device index must be a non-negative integer",
        )

    try:
        import torch
    except Exception as error:
        raise CudaPreflightError(
            "cuda_unavailable",
            "PyTorch CUDA runtime could not be imported",
        ) from error

    try:
        if not torch.cuda.is_available():
            raise CudaPreflightError(
                "cuda_unavailable",
                "CUDA is not available",
            )
        device_count = int(torch.cuda.device_count())
    except CudaPreflightError:
        raise
    except Exception as error:
        raise CudaPreflightError(
            "cuda_unavailable",
            "CUDA runtime could not enumerate devices",
        ) from error

    if device_count < 1:
        raise CudaPreflightError(
            "cuda_unavailable",
            "CUDA reported no visible devices",
        )
    if device >= device_count:
        raise CudaPreflightError(
            "cuda_device_invalid",
            f"CUDA device {device} is not visible",
        )

    try:
        torch.cuda.set_device(device)
        name = str(torch.cuda.get_device_name(device))
        properties = torch.cuda.get_device_properties(device)
        capability = torch.cuda.get_device_capability(device)
        return CudaDeviceInfo(
            index=device,
            name=name,
            total_memory_bytes=int(properties.total_memory),
            capability=(int(capability[0]), int(capability[1])),
        )
    except Exception as error:
        raise CudaPreflightError(
            "cuda_unavailable",
            f"CUDA device {device} could not be initialized",
        ) from error


def _plain_value(
    values: Mapping[str, str],
    name: str,
    default: str,
) -> str:
    value = values.get(name, default)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
    ):
        raise InvalidConfiguration(
            f"{name} must be a non-empty value without surrounding whitespace"
        )
    return value


def _decimal_value(
    values: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw = values.get(name)
    if raw is None:
        return default
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        raise InvalidConfiguration(f"{name} must be a decimal integer")
    value = int(raw)
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            expected = f"at least {minimum}"
        else:
            expected = f"between {minimum} and {maximum}"
        raise InvalidConfiguration(f"{name} must be {expected}")
    return value
