import random

import pytest

from botified_tts.segmenter import OFFICIAL_TAGS, Segmenter


def _collect(text: str, chunk_sizes: list[int]) -> list[str]:
    segmenter = Segmenter()
    segments: list[str] = []
    offset = 0
    for size in chunk_sizes:
        segments.extend(segmenter.append(text[offset : offset + size]))
        offset += size
    segments.extend(segmenter.append(text[offset:]))
    segments.extend(segmenter.finish())
    return segments


def test_text_is_conserved_for_single_character_and_random_chunks() -> None:
    text = (
        "你好！This is Botified TTS 3.14, supporting mixed 中文 and English. "
        "[laughing]我们继续吧？最后一句"
    )
    randomizer = random.Random(7)
    random_sizes: list[int] = []
    remaining = len(text)
    while remaining:
        size = min(remaining, randomizer.randint(1, 7))
        random_sizes.append(size)
        remaining -= size

    chunkings = [
        [len(text)],
        [1] * len(text),
        random_sizes,
    ]
    for chunk_sizes in chunkings:
        segments = _collect(text, chunk_sizes)
        assert "".join(segments) == text
        assert all(segments)


def test_one_append_extracts_multiple_ordered_sentences() -> None:
    segmenter = Segmenter()

    assert segmenter.append("你好！Hello world?继续。尾巴") == [
        "你好！",
        "Hello world?",
        "继续。",
    ]
    assert segmenter.finish() == ["尾巴"]


def test_soft_boundary_requires_24_characters() -> None:
    segmenter = Segmenter()

    assert segmenter.append("a" * 22 + ",") == []
    assert segmenter.append("b" * 23 + ",") == ["a" * 22 + "," + "b" * 23 + ","]


def test_natural_target_100_waits_and_hard_160_forces_split() -> None:
    segmenter = Segmenter()

    assert segmenter.append("x" * 100) == []
    assert segmenter.append("y" * 59) == []
    assert segmenter.append("z") == ["x" * 100 + "y" * 59 + "z"]
    assert segmenter.finish() == []


def test_natural_target_uses_latest_soft_boundary_at_or_before_100() -> None:
    segmenter = Segmenter()
    text = "a" * 29 + " " + "b" * 39 + " " + "c" * 30

    assert len(text) == 100
    assert segmenter.append(text) == [text[:70]]
    assert segmenter.finish() == [text[70:]]


def test_short_profile_uses_target_55_and_hard_80() -> None:
    target = Segmenter(profile="short")
    target_text = "a" * 29 + " " + "b" * 14 + " " + "c" * 10

    assert target.append(target_text) == [target_text[:45]]
    assert target.finish() == [target_text[45:]]

    hard = Segmenter(profile="short")
    assert hard.append("x" * 80) == ["x" * 80]
    assert hard.finish() == []


def test_hard_limit_prefers_recent_whitespace_over_splitting_a_word() -> None:
    segmenter = Segmenter()
    text = "a" * 124 + " " + "longenglishword" * 3

    assert segmenter.append(text) == [text[:125]]
    assert segmenter.finish() == [text[125:]]


def test_trailing_digit_dot_waits_for_the_next_append() -> None:
    decimal = Segmenter()
    assert decimal.append("价格是3.") == []
    assert decimal.append("14元。") == ["价格是3.14元。"]

    sentence = Segmenter()
    assert sentence.append("版本3.") == []
    assert sentence.append("发布") == ["版本3."]
    assert sentence.finish() == ["发布"]


@pytest.mark.parametrize("tag", OFFICIAL_TAGS)
def test_complete_official_tag_is_never_split(tag: str) -> None:
    segmenter = Segmenter()
    prefix = "x" * 158

    assert segmenter.append(prefix + tag) == [prefix]
    assert segmenter.finish() == [tag]


def test_possible_official_tag_prefix_is_protected_across_appends() -> None:
    segmenter = Segmenter()
    prefix = "x" * 155

    assert segmenter.append(prefix + "[laugh") == [prefix]
    assert segmenter.append("ing]结束。") == ["[laughing]结束。"]


def test_overlong_decimal_is_bounded_without_losing_text_or_splitting_tag() -> None:
    first = "1" * 2500 + "." + "2" * 2500 + "界" * 1000 + "x" * 155 + "[laugh"
    second = "ing]结束。"
    segmenter = Segmenter()

    segments = segmenter.append(first)
    assert len(segmenter.pending_text.encode("utf-8")) < 4096
    segments.extend(segmenter.append(second))
    assert len(segmenter.pending_text.encode("utf-8")) < 4096
    segments.extend(segmenter.finish())

    assert "".join(segments) == first + second
    assert all(segments)
    assert sum("[laughing]" in segment for segment in segments) == 1
    assert all("[laugh" not in segment or "[laughing]" in segment for segment in segments)


def test_deadline_expiration_is_pure_state_and_fires_at_12_characters() -> None:
    segmenter = Segmenter()

    assert segmenter.append("x" * 11) == []
    assert segmenter.expire_deadline() == []
    assert segmenter.deadline_is_expired

    assert segmenter.append("y") == ["x" * 11 + "y"]
    assert not segmenter.deadline_is_expired
    assert not segmenter.has_pending_text


def test_deadline_prefers_the_latest_safe_soft_boundary() -> None:
    segmenter = Segmenter()
    text = "a" * 10 + " " + "b" * 10

    assert segmenter.append(text) == []
    assert segmenter.expire_deadline() == ["a" * 10 + " "]
    assert segmenter.pending_text == "b" * 10
    assert not segmenter.deadline_is_expired


def test_flush_and_finish_submit_short_remainders_without_closing_segmenter() -> None:
    segmenter = Segmenter()

    assert segmenter.append("短句") == []
    assert segmenter.flush() == ["短句"]
    assert segmenter.append("more") == []
    assert segmenter.finish() == ["more"]
    assert segmenter.finish() == []
