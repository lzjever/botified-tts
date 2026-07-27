"""Incremental sentence segmentation for TTS input."""

from __future__ import annotations

import re
from dataclasses import dataclass


SOFT_MIN_CHARS = 24
DEADLINE_MIN_CHARS = 12
TARGET_MAX_CHARS = 100
HARD_MAX_CHARS = 160

OFFICIAL_TAGS = (
    "[laughing]",
    "[sigh]",
    "[Uhm]",
    "[Shh]",
    "[Question-ah]",
    "[Question-ei]",
    "[Question-en]",
    "[Question-oh]",
    "[Surprise-wa]",
    "[Surprise-yo]",
    "[Dissatisfaction-hnn]",
)

_STRONG_ENDINGS = frozenset("。！？!?")
_SOFT_ENDINGS = frozenset("，,；;：:")
_DECIMAL = re.compile(r"\d+\.\d+")
_TRAILING_DECIMAL_PREFIX = re.compile(r"\d+\.$")


@dataclass(frozen=True)
class _ProtectedRange:
    start: int
    end: int
    protect_end: bool = False

    def contains_cut(self, cut: int) -> bool:
        if self.protect_end:
            return self.start < cut <= self.end
        return self.start < cut < self.end


class Segmenter:
    """Synchronously segment text appended at arbitrary chunk boundaries."""

    def __init__(self) -> None:
        self._buffer = ""
        self._deadline_expired = False

    @property
    def pending_text(self) -> str:
        return self._buffer

    @property
    def has_pending_text(self) -> bool:
        return bool(self._buffer)

    @property
    def deadline_is_expired(self) -> bool:
        return self._deadline_expired

    def append(self, text: str) -> list[str]:
        if not text:
            return []

        self._buffer += text
        segments = self._extract_regular_segments()
        if segments:
            self._deadline_expired = False

        if self._deadline_expired and len(self._buffer) >= DEADLINE_MIN_CHARS:
            cut = self._deadline_cut()
            if cut:
                segments.append(self._take(cut))
                self._deadline_expired = False

        return segments

    def expire_deadline(self) -> list[str]:
        if not self._buffer:
            self._deadline_expired = False
            return []

        self._deadline_expired = True
        if len(self._buffer) < DEADLINE_MIN_CHARS:
            return []

        cut = self._deadline_cut()
        if not cut:
            return []

        segment = self._take(cut)
        self._deadline_expired = False
        return [segment]

    def flush(self) -> list[str]:
        self._deadline_expired = False
        if not self._buffer:
            return []
        return [self._take(len(self._buffer))]

    def finish(self) -> list[str]:
        return self.flush()

    def _extract_regular_segments(self) -> list[str]:
        segments: list[str] = []
        while self._buffer:
            cut = self._regular_cut()
            if not cut:
                break
            segments.append(self._take(cut))
        return segments

    def _regular_cut(self) -> int | None:
        protected = self._protected_ranges()
        upper = min(len(self._buffer), HARD_MAX_CHARS)

        for cut in range(1, upper + 1):
            if self._is_strong_cut(cut) and self._is_safe_cut(cut, protected):
                return cut

        if len(self._buffer) >= TARGET_MAX_CHARS:
            target_soft_cuts = [
                cut
                for cut in range(SOFT_MIN_CHARS, TARGET_MAX_CHARS + 1)
                if self._is_soft_cut(cut) and self._is_safe_cut(cut, protected)
            ]
            if target_soft_cuts:
                return target_soft_cuts[-1]
        else:
            for cut in range(SOFT_MIN_CHARS, len(self._buffer) + 1):
                if self._is_soft_cut(cut) and self._is_safe_cut(cut, protected):
                    return cut

        if len(self._buffer) < HARD_MAX_CHARS:
            return None

        for cut in range(HARD_MAX_CHARS, 0, -1):
            if self._is_safe_cut(cut, protected):
                return cut
        return None

    def _deadline_cut(self) -> int | None:
        protected = self._protected_ranges()
        soft_cuts = [
            cut
            for cut in range(1, len(self._buffer) + 1)
            if self._is_soft_cut(cut) and self._is_safe_cut(cut, protected)
        ]
        if soft_cuts:
            return soft_cuts[-1]

        for cut in range(len(self._buffer), 0, -1):
            if self._is_safe_cut(cut, protected):
                return cut
        return None

    def _protected_ranges(self) -> list[_ProtectedRange]:
        ranges: list[_ProtectedRange] = []

        for start, char in enumerate(self._buffer):
            if char != "[":
                continue

            remainder = self._buffer[start:]
            complete = next((tag for tag in OFFICIAL_TAGS if remainder.startswith(tag)), None)
            if complete is not None:
                ranges.append(_ProtectedRange(start, start + len(complete)))
            elif any(tag.startswith(remainder) for tag in OFFICIAL_TAGS):
                ranges.append(_ProtectedRange(start, len(self._buffer), protect_end=True))

        ranges.extend(_ProtectedRange(match.start(), match.end()) for match in _DECIMAL.finditer(self._buffer))

        trailing_decimal = _TRAILING_DECIMAL_PREFIX.search(self._buffer)
        if trailing_decimal is not None:
            ranges.append(
                _ProtectedRange(
                    trailing_decimal.start(),
                    trailing_decimal.end(),
                    protect_end=True,
                )
            )

        return ranges

    def _is_safe_cut(self, cut: int, protected: list[_ProtectedRange]) -> bool:
        return not any(item.contains_cut(cut) for item in protected)

    def _is_strong_cut(self, cut: int) -> bool:
        char = self._buffer[cut - 1]
        if char in _STRONG_ENDINGS:
            return True
        if char != ".":
            return False

        previous_is_digit = cut >= 2 and self._buffer[cut - 2].isdigit()
        next_is_digit = cut < len(self._buffer) and self._buffer[cut].isdigit()
        next_is_unknown = cut == len(self._buffer)
        return not (previous_is_digit and (next_is_digit or next_is_unknown))

    def _is_soft_cut(self, cut: int) -> bool:
        char = self._buffer[cut - 1]
        return char in _SOFT_ENDINGS or char.isspace()

    def _take(self, cut: int) -> str:
        segment = self._buffer[:cut]
        self._buffer = self._buffer[cut:]
        return segment
