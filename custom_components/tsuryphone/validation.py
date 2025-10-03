"""Validation helpers for TsuryPhone integration."""

from __future__ import annotations

from typing import Final

from .const import MAX_PATTERN_LENGTH

_VALID_PATTERN_CHARS: Final = set("0123456789,x")


def _normalize_pattern(pattern: str | None) -> str:
    """Return a trimmed pattern string, treating None as empty."""
    if pattern is None:
        return ""
    return pattern.strip()


def is_valid_ring_pattern(pattern: str | None) -> bool:
    """Validate ring pattern syntax to mirror firmware expectations."""
    normalized = _normalize_pattern(pattern)
    if not normalized:
        # Empty pattern defers to the device's native default
        return True

    if len(normalized) > MAX_PATTERN_LENGTH:
        return False

    if any(char not in _VALID_PATTERN_CHARS for char in normalized):
        return False

    base = normalized
    repeat_count = 1

    if "x" in normalized:
        base, repeat_str = normalized.rsplit("x", 1)
        if not repeat_str or not repeat_str.isdigit():
            return False
        repeat_count = int(repeat_str)
        if repeat_count <= 0:
            repeat_count = 1

    segments = base.split(",")
    if not segments or any(segment == "" for segment in segments):
        return False

    for segment in segments:
        if not segment.isdigit():
            return False
        if int(segment) <= 0:
            return False

    if repeat_count > 1:
        return len(segments) % 2 == 0

    return len(segments) % 2 == 1
