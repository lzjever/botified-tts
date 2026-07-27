from __future__ import annotations

import pytest

from botified_tts.schemas import (
    AppendMessage,
    CancelMessage,
    DesignVoice,
    FinishMessage,
    FlushMessage,
    HTTP_TEXT_MAX_BYTES,
    InputTooLarge,
    InvalidSynthesisOptions,
    ProfileVoice,
    SpeechRequest,
    StartMessage,
    SynthesisOptions,
    WS_APPEND_MAX_BYTES,
    parse_client_message,
    parse_speech_request,
    parse_synthesis_options,
)


PROFILE = {"type": "profile", "id": "voice_123"}


def test_profile_defaults_to_controllable_and_faithful_is_canonical() -> None:
    assert parse_synthesis_options({"voice": PROFILE}) == SynthesisOptions(
        voice=ProfileVoice(id="voice_123"),
        mode="controllable",
        style=None,
    )
    assert parse_synthesis_options(
        {"voice": PROFILE, "mode": "faithful"}
    ) == SynthesisOptions(
        voice=ProfileVoice(id="voice_123"),
        mode="faithful",
        style=None,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "controllable"},
        {
            "voice": {"type": "design", "description": "warm voice"},
            "mode": "controllable",
        },
        {"voice": PROFILE, "mode": "unsupported"},
        {"voice": PROFILE, "mode": "faithful", "style": "cheerful"},
    ],
)
def test_mode_is_only_valid_for_profiles_and_faithful_rejects_style(
    payload: dict[str, object],
) -> None:
    with pytest.raises(InvalidSynthesisOptions):
        parse_synthesis_options(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": True},
        {"voice": {"type": "profile", "id": "voice_123", "unexpected": True}},
        {
            "voice": {
                "type": "design",
                "description": "warm voice",
                "unexpected": True,
            }
        },
    ],
)
def test_unknown_fields_are_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(InvalidSynthesisOptions):
        parse_synthesis_options(payload)


def test_style_limit_counts_utf8_bytes() -> None:
    exact = "a" * 509 + "语"
    too_large = exact + "a"

    assert len(exact.encode("utf-8")) == 512
    assert parse_synthesis_options({"style": exact}).style == exact
    with pytest.raises(InvalidSynthesisOptions):
        parse_synthesis_options({"style": too_large})


def test_design_description_limit_counts_utf8_bytes() -> None:
    exact = "a" * 1021 + "语"
    too_large = exact + "a"

    assert len(exact.encode("utf-8")) == 1024
    assert parse_synthesis_options(
        {"voice": {"type": "design", "description": exact}}
    ) == SynthesisOptions(
        voice=DesignVoice(description=exact),
        mode=None,
        style=None,
    )
    with pytest.raises(InvalidSynthesisOptions):
        parse_synthesis_options(
            {"voice": {"type": "design", "description": too_large}}
        )


def test_http_speech_body_reuses_canonical_synthesis_options() -> None:
    payload = {
        "text": "你好，Botified。",
        "voice": PROFILE,
        "mode": "faithful",
    }

    assert parse_speech_request(payload) == SpeechRequest(
        text="你好，Botified。",
        options=parse_synthesis_options(
            {"voice": PROFILE, "mode": "faithful"}
        ),
    )


def test_http_speech_body_rejects_missing_empty_and_unknown_text_fields() -> None:
    for payload in (
        {},
        {"text": ""},
        {"text": "   "},
        {"text": 42},
        {"text": "hello", "unexpected": True},
    ):
        with pytest.raises(InvalidSynthesisOptions):
            parse_speech_request(payload)


def test_http_text_limit_counts_utf8_bytes() -> None:
    exact = "a" * (HTTP_TEXT_MAX_BYTES - 3) + "语"
    too_large = exact + "a"

    assert len(exact.encode("utf-8")) == HTTP_TEXT_MAX_BYTES
    assert parse_speech_request({"text": exact}).text == exact
    with pytest.raises(InputTooLarge):
        parse_speech_request({"text": too_large})
    with pytest.raises(InputTooLarge):
        parse_speech_request({"text": " " * (HTTP_TEXT_MAX_BYTES + 1)})


def test_ws_start_reuses_canonical_synthesis_options() -> None:
    payload = {
        "type": "start",
        "voice": {"type": "design", "description": "warm voice"},
        "style": "slow and calm",
    }

    assert parse_client_message(payload) == StartMessage(
        options=parse_synthesis_options(
            {
                "voice": {
                    "type": "design",
                    "description": "warm voice",
                },
                "style": "slow and calm",
            }
        )
    )


def test_ws_append_is_state_free_and_counts_utf8_bytes() -> None:
    exact = "a" * (WS_APPEND_MAX_BYTES - 3) + "语"
    too_large = exact + "a"

    assert parse_client_message({"type": "append", "text": exact}) == (
        AppendMessage(text=exact)
    )
    assert parse_client_message({"type": "append", "text": ""}) == (
        AppendMessage(text="")
    )
    with pytest.raises(InputTooLarge):
        parse_client_message({"type": "append", "text": too_large})


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"type": "flush"}, FlushMessage()),
        ({"type": "finish"}, FinishMessage()),
        ({"type": "cancel"}, CancelMessage()),
    ],
)
def test_ws_control_messages_have_no_fields(
    payload: dict[str, object],
    expected: FlushMessage | FinishMessage | CancelMessage,
) -> None:
    assert parse_client_message(payload) == expected
    with pytest.raises(InvalidSynthesisOptions):
        parse_client_message({**payload, "unexpected": True})


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"type": "unknown"},
        {"type": []},
        {"type": "append"},
        {"type": "append", "text": 42},
        {"type": "append", "text": "hello", "style": "warm"},
        {"type": "start", "text": "not an option"},
    ],
)
def test_ws_message_type_and_fields_are_closed(
    payload: dict[str, object],
) -> None:
    with pytest.raises(InvalidSynthesisOptions):
        parse_client_message(payload)
