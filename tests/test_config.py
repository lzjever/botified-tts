import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from botified_tts.config import (
    CudaPreflightError,
    InvalidConfiguration,
    Settings,
    require_cuda,
)


REQUIRED_ENV = {
    "BOTIFIED_TTS_API_KEY": "test-secret",
    "BOTIFIED_TTS_MODEL_SOURCE": "modelscope",
}


def test_settings_load_product_defaults() -> None:
    settings = Settings.from_env(REQUIRED_ENV)

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.model_source == "modelscope"
    assert settings.gpu_device == 0
    assert settings.data_dir == Path("/data")
    assert settings.api_key == "test-secret"
    assert settings.log_level == "INFO"
    with pytest.raises(FrozenInstanceError):
        settings.port = 9000  # type: ignore[misc]


def test_settings_load_supported_overrides() -> None:
    settings = Settings.from_env(
        {
            "PATH": "/usr/bin",
            "BOTIFIED_TTS_HOST": "127.0.0.1",
            "BOTIFIED_TTS_PORT": "9000",
            "BOTIFIED_TTS_MODEL_SOURCE": "huggingface",
            "BOTIFIED_TTS_GPU_DEVICE": "2",
            "BOTIFIED_TTS_DATA_DIR": "/srv/tts",
            "BOTIFIED_TTS_API_KEY": "another-secret",
            "BOTIFIED_TTS_LOG_LEVEL": "DEBUG",
        }
    )

    assert settings.host == "127.0.0.1"
    assert settings.port == 9000
    assert settings.model_source == "huggingface"
    assert settings.gpu_device == 2
    assert settings.data_dir == Path("/srv/tts")
    assert settings.api_key == "another-secret"
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BOTIFIED_TTS_PORT", "0"),
        ("BOTIFIED_TTS_PORT", "65536"),
        ("BOTIFIED_TTS_PORT", "8000.0"),
        ("BOTIFIED_TTS_GPU_DEVICE", "-1"),
        ("BOTIFIED_TTS_GPU_DEVICE", "device-0"),
        ("BOTIFIED_TTS_LOG_LEVEL", "TRACE"),
        ("BOTIFIED_TTS_HOST", ""),
        ("BOTIFIED_TTS_MODEL_SOURCE", "auto"),
        ("BOTIFIED_TTS_DATA_DIR", "relative/path"),
        ("BOTIFIED_TTS_API_KEY", ""),
    ],
)
def test_settings_reject_invalid_values(name: str, value: str) -> None:
    environ = dict(REQUIRED_ENV)
    environ[name] = value

    with pytest.raises(InvalidConfiguration):
        Settings.from_env(environ)


def test_settings_reject_unknown_prefixed_variable() -> None:
    with pytest.raises(InvalidConfiguration, match="BOTIFIED_TTS_TIMEOUT"):
        Settings.from_env(
            {
                **REQUIRED_ENV,
                "BOTIFIED_TTS_TIMEOUT": "30",
            }
        )


@pytest.mark.parametrize(
    "name",
    ["BOTIFIED_TTS_MODEL", "BOTIFIED_TTS_MODEL_REVISION"],
)
def test_settings_reject_removed_model_overrides(name: str) -> None:
    with pytest.raises(InvalidConfiguration, match=name):
        Settings.from_env({**REQUIRED_ENV, name: "removed"})


def test_settings_require_api_key() -> None:
    with pytest.raises(InvalidConfiguration, match="BOTIFIED_TTS_API_KEY"):
        Settings.from_env({"BOTIFIED_TTS_MODEL_SOURCE": "modelscope"})


def test_settings_require_model_source_without_a_default() -> None:
    with pytest.raises(InvalidConfiguration, match="BOTIFIED_TTS_MODEL_SOURCE"):
        Settings.from_env({"BOTIFIED_TTS_API_KEY": "test-secret"})


@pytest.mark.parametrize("api_key", ["a", "Az09._~-"])
def test_settings_accept_api_key_token_grammar(api_key: str) -> None:
    settings = Settings.from_env(
        {
            "BOTIFIED_TTS_API_KEY": api_key,
            "BOTIFIED_TTS_MODEL_SOURCE": "modelscope",
        }
    )

    assert settings.api_key == api_key


@pytest.mark.parametrize(
    "api_key",
    ['"quoted"', "has space", "key=value"],
)
def test_settings_reject_api_key_outside_token_grammar(api_key: str) -> None:
    with pytest.raises(InvalidConfiguration, match="BOTIFIED_TTS_API_KEY"):
        Settings.from_env(
            {
                "BOTIFIED_TTS_API_KEY": api_key,
                "BOTIFIED_TTS_MODEL_SOURCE": "modelscope",
            }
        )


def test_settings_reject_non_ascii_api_key() -> None:
    with pytest.raises(InvalidConfiguration, match="BOTIFIED_TTS_API_KEY"):
        Settings.from_env(
            {
                "BOTIFIED_TTS_API_KEY": "内部密钥",
                "BOTIFIED_TTS_MODEL_SOURCE": "modelscope",
            }
        )


class FakeCuda:
    def __init__(
        self,
        *,
        available: bool,
        count: int,
    ) -> None:
        self._available = available
        self._count = count
        self.selected_device: int | None = None

    def is_available(self) -> bool:
        return self._available

    def device_count(self) -> int:
        return self._count

    def set_device(self, device: int) -> None:
        self.selected_device = device

    def get_device_name(self, device: int) -> str:
        return f"Fake GPU {device}"

    def get_device_properties(self, device: int) -> SimpleNamespace:
        return SimpleNamespace(total_memory=24 * 1024**3)

    def get_device_capability(self, device: int) -> tuple[int, int]:
        return (8, 9)


def _install_fake_torch(
    monkeypatch: pytest.MonkeyPatch,
    cuda: FakeCuda,
) -> None:
    torch = ModuleType("torch")
    torch.cuda = cuda  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch)


def test_require_cuda_reports_import_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)

    with pytest.raises(CudaPreflightError) as caught:
        require_cuda(0)

    assert caught.value.code == "cuda_unavailable"


@pytest.mark.parametrize(
    ("available", "count"),
    [(False, 1), (True, 0)],
)
def test_require_cuda_rejects_unavailable_runtime(
    monkeypatch: pytest.MonkeyPatch,
    available: bool,
    count: int,
) -> None:
    _install_fake_torch(
        monkeypatch,
        FakeCuda(available=available, count=count),
    )

    with pytest.raises(CudaPreflightError) as caught:
        require_cuda(0)

    assert caught.value.code == "cuda_unavailable"


def test_require_cuda_rejects_invalid_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_torch(
        monkeypatch,
        FakeCuda(available=True, count=1),
    )

    with pytest.raises(CudaPreflightError) as caught:
        require_cuda(1)

    assert caught.value.code == "cuda_device_invalid"


def test_require_cuda_selects_device_and_returns_device_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cuda = FakeCuda(available=True, count=2)
    _install_fake_torch(monkeypatch, cuda)

    device = require_cuda(1)

    assert cuda.selected_device == 1
    assert device.index == 1
    assert device.name == "Fake GPU 1"
    assert device.total_memory_bytes == 24 * 1024**3
    assert device.capability == (8, 9)
