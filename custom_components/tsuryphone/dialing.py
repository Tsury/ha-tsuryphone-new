"""Dialing and phone number normalization helpers for the TsuryPhone integration."""

from __future__ import annotations

from dataclasses import dataclass

_FORMATTING_CHARS = {" ", "-", "(", ")", ".", "\t", "\r", "\n"}


def _strip_formatting(value: str) -> str:
    """Remove formatting characters while preserving a leading plus and digits."""
    result: list[str] = []
    for index, char in enumerate(value):
        if char in _FORMATTING_CHARS:
            continue
        if char == "+":
            if not result:
                result.append(char)
            continue
        if char.isdigit():
            result.append(char)
    return "".join(result)


def _strip_leading_zeros(digits: str) -> str:
    """Remove leading zeros while keeping at least one digit when available."""
    stripped = digits.lstrip("0")
    return stripped if stripped else (digits if digits.count("0") == len(digits) else "")


def _ensure_plus_prefix(digits: str) -> str:
    if not digits:
        return ""
    return digits if digits.startswith("+") else f"+{digits}"


def strip_to_digits(value: str) -> str:
    """Return only the digit characters from *value*."""
    return "".join(ch for ch in value if ch.isdigit())


def sanitize_default_dialing_code(value: str | None) -> str:
    """Return the sanitized default dialing code (digits only, no leading zeros)."""
    if not value:
        return ""
    digits = strip_to_digits(str(value))
    if not digits:
        return ""
    sanitized = _strip_leading_zeros(digits)
    return sanitized


def normalize_phone_number(raw_number: str | None, default_dialing_code: str | None) -> str:
    """Normalize a phone number using firmware-compatible semantics."""
    if raw_number is None:
        return ""

    trimmed = str(raw_number).strip()
    if not trimmed:
        return ""

    cleaned = _strip_formatting(trimmed)
    if not cleaned:
        return ""

    has_plus = cleaned.startswith("+")
    digits = cleaned[1:] if has_plus else cleaned

    if not digits:
        return ""

    if has_plus:
        return _ensure_plus_prefix(digits)

    if digits.startswith("00") and len(digits) > 2:
        international = _strip_leading_zeros(digits[2:])
        if international:
            return _ensure_plus_prefix(international)

    sanitized_code = sanitize_default_dialing_code(default_dialing_code or "")

    if sanitized_code:
        if digits.startswith(sanitized_code):
            return _ensure_plus_prefix(digits)
        if digits[0] == "0" and len(digits) >= 7:
            local = _strip_leading_zeros(digits[1:])
            if local:
                return _ensure_plus_prefix(f"{sanitized_code}{local}")
        if len(digits) >= 8:
            return _ensure_plus_prefix(f"{sanitized_code}{digits}")

    return digits


def numbers_equivalent(lhs: str | None, rhs: str | None, default_dialing_code: str | None) -> bool:
    """Return True when *lhs* and *rhs* represent the same phone number."""
    if lhs == rhs:
        return True

    norm_lhs = normalize_phone_number(lhs, default_dialing_code)
    norm_rhs = normalize_phone_number(rhs, default_dialing_code)

    if norm_lhs and norm_rhs:
        if norm_lhs.lower() == norm_rhs.lower():
            return True
        if strip_to_digits(norm_lhs) == strip_to_digits(norm_rhs) and strip_to_digits(norm_lhs):
            return True

    raw_digits_lhs = strip_to_digits(lhs or "")
    raw_digits_rhs = strip_to_digits(rhs or "")
    if raw_digits_lhs and raw_digits_lhs == raw_digits_rhs:
        return True

    return False


@dataclass(frozen=True, slots=True)
class DialingContext:
    """Convenience structure for exposing dialing metadata to entities and diagnostics."""

    default_code: str
    default_prefix: str

    @property
    def has_default(self) -> bool:
        return bool(self.default_code)

    def normalize(self, number: str | None) -> str:
        return normalize_phone_number(number, self.default_code)

    def sanitize_code(self, value: str | None) -> str:
        return sanitize_default_dialing_code(value)
