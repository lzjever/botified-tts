from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


STYLE_MAX_BYTES = 512
DESCRIPTION_MAX_BYTES = 1024
ProfileMode = Literal["controllable", "faithful"]


class InvalidSynthesisOptions(ValueError):
    """The public synthesis options are invalid."""


@dataclass(frozen=True)
class ProfileVoice:
    id: str


@dataclass(frozen=True)
class DesignVoice:
    description: str


@dataclass(frozen=True)
class SynthesisOptions:
    voice: ProfileVoice | DesignVoice | None
    mode: ProfileMode | None
    style: str | None


def parse_synthesis_options(value: Mapping[str, object]) -> SynthesisOptions:
    _reject_unknown(value, {"voice", "mode", "style"}, "synthesis options")
    style = _optional_string(value, "style")
    if style is not None and len(style.encode("utf-8")) > STYLE_MAX_BYTES:
        raise InvalidSynthesisOptions("style exceeds 512 UTF-8 bytes")

    raw_voice = value.get("voice")
    if raw_voice is None:
        if "mode" in value:
            raise InvalidSynthesisOptions("mode requires a profile voice")
        return SynthesisOptions(voice=None, mode=None, style=style)
    if not isinstance(raw_voice, Mapping):
        raise InvalidSynthesisOptions("voice must be an object")

    voice_type = raw_voice.get("type")
    if voice_type == "profile":
        voice = _parse_profile_voice(raw_voice)
        raw_mode = value.get("mode", "controllable")
        if raw_mode not in ("controllable", "faithful"):
            raise InvalidSynthesisOptions(
                "profile mode must be controllable or faithful"
            )
        mode: ProfileMode = raw_mode
        if mode == "faithful" and style is not None:
            raise InvalidSynthesisOptions("faithful mode does not accept style")
        return SynthesisOptions(voice=voice, mode=mode, style=style)

    if voice_type == "design":
        if "mode" in value:
            raise InvalidSynthesisOptions("mode requires a profile voice")
        return SynthesisOptions(
            voice=_parse_design_voice(raw_voice),
            mode=None,
            style=style,
        )

    raise InvalidSynthesisOptions("voice type must be profile or design")


def _parse_profile_voice(value: Mapping[str, object]) -> ProfileVoice:
    _reject_unknown(value, {"type", "id"}, "profile voice")
    voice_id = value.get("id")
    if not isinstance(voice_id, str) or not voice_id:
        raise InvalidSynthesisOptions("profile voice id must be a non-empty string")
    return ProfileVoice(id=voice_id)


def _parse_design_voice(value: Mapping[str, object]) -> DesignVoice:
    _reject_unknown(value, {"type", "description"}, "design voice")
    description = value.get("description")
    if not isinstance(description, str) or not description.strip():
        raise InvalidSynthesisOptions(
            "design voice description must be a non-empty string"
        )
    if len(description.encode("utf-8")) > DESCRIPTION_MAX_BYTES:
        raise InvalidSynthesisOptions(
            "design voice description exceeds 1024 UTF-8 bytes"
        )
    return DesignVoice(description=description)


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    raw = value.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise InvalidSynthesisOptions(f"{key} must be a string")
    return raw


def _reject_unknown(
    value: Mapping[str, object],
    allowed: set[str],
    owner: str,
) -> None:
    unknown = sorted(key for key in value if key not in allowed)
    if unknown:
        raise InvalidSynthesisOptions(
            f"unknown {owner} field: {unknown[0]}"
        )
