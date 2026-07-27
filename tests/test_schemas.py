from __future__ import annotations

import pytest

from botified_tts.schemas import (
    DesignVoice,
    InvalidSynthesisOptions,
    ProfileVoice,
    SynthesisOptions,
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
