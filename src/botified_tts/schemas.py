from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


STYLE_MAX_BYTES = 512
DESCRIPTION_MAX_BYTES = 1024
HTTP_TEXT_MAX_BYTES = 8 * 1024
WS_APPEND_MAX_BYTES = 16 * 1024
ProfileMode = Literal["controllable", "faithful"]


class InvalidSynthesisOptions(ValueError):
    """The public synthesis options are invalid."""


class InputTooLarge(ValueError):
    """A public text field exceeds its UTF-8 byte limit."""


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


@dataclass(frozen=True)
class SpeechRequest:
    text: str
    options: SynthesisOptions


@dataclass(frozen=True)
class StartMessage:
    options: SynthesisOptions


@dataclass(frozen=True)
class AppendMessage:
    text: str


@dataclass(frozen=True)
class FlushMessage:
    pass


@dataclass(frozen=True)
class FinishMessage:
    pass


@dataclass(frozen=True)
class CancelMessage:
    pass


ClientMessage = (
    StartMessage
    | AppendMessage
    | FlushMessage
    | FinishMessage
    | CancelMessage
)


def parse_synthesis_options(value: Mapping[str, object]) -> SynthesisOptions:
    _reject_unknown(value, {"voice", "mode", "style"}, "synthesis options")
    style = _optional_string(value, "style")
    if style is not None and not style.strip():
        raise InvalidSynthesisOptions(
            "style must be a non-empty string when provided"
        )
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


def parse_speech_request(value: Mapping[str, object]) -> SpeechRequest:
    if not isinstance(value, Mapping):
        raise InvalidSynthesisOptions("speech request must be an object")
    _reject_unknown(
        value,
        {"text", "voice", "mode", "style"},
        "speech request",
    )
    text = _required_string(value, "text", "speech request")
    if len(text.encode("utf-8")) > HTTP_TEXT_MAX_BYTES:
        raise InputTooLarge("speech request text exceeds 8192 UTF-8 bytes")
    if not text.strip():
        raise InvalidSynthesisOptions(
            "speech request text must be a non-empty string"
        )
    return SpeechRequest(text=text, options=_parse_embedded_options(value))


def parse_client_message(value: Mapping[str, object]) -> ClientMessage:
    if not isinstance(value, Mapping):
        raise InvalidSynthesisOptions("client message must be an object")

    message_type = value.get("type")
    if not isinstance(message_type, str):
        raise InvalidSynthesisOptions(
            "client message type must be a string"
        )
    if message_type == "start":
        _reject_unknown(
            value,
            {"type", "voice", "mode", "style"},
            "start message",
        )
        return StartMessage(options=_parse_embedded_options(value))

    if message_type == "append":
        _reject_unknown(value, {"type", "text"}, "append message")
        text = _required_string(value, "text", "append message")
        if len(text.encode("utf-8")) > WS_APPEND_MAX_BYTES:
            raise InputTooLarge("append text exceeds 16384 UTF-8 bytes")
        return AppendMessage(text=text)

    control_messages: dict[str, type[FlushMessage | FinishMessage | CancelMessage]] = {
        "flush": FlushMessage,
        "finish": FinishMessage,
        "cancel": CancelMessage,
    }
    message_class = control_messages.get(message_type)
    if message_class is not None:
        _reject_unknown(value, {"type"}, f"{message_type} message")
        return message_class()

    raise InvalidSynthesisOptions("unknown client message type")


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


def _parse_embedded_options(value: Mapping[str, object]) -> SynthesisOptions:
    return parse_synthesis_options(
        {
            key: value[key]
            for key in ("voice", "mode", "style")
            if key in value
        }
    )


def _required_string(
    value: Mapping[str, object],
    key: str,
    owner: str,
) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise InvalidSynthesisOptions(f"{owner} {key} must be a string")
    return raw


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
