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
    return (
        stripped if stripped else (digits if digits.count("0") == len(digits) else "")
    )


def _ensure_plus_prefix(digits: str) -> str:
    if not digits:
        return ""
    return digits if digits.startswith("+") else f"+{digits}"


def _localize_with_default(digits: str, sanitized_code: str) -> str | None:
    """Try to convert international digits into local format using the default code."""

    if not sanitized_code or not digits.startswith(sanitized_code):
        return None

    remainder = digits[len(sanitized_code) :]
    if not remainder:
        return None

    return remainder if remainder.startswith("0") else f"0{remainder}"


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


def normalize_phone_number(
    raw_number: str | None, default_dialing_code: str | None
) -> str:
    """Normalize a phone number using firmware-compatible semantics."""
    if raw_number is None:
        return ""

    trimmed = str(raw_number).strip()
    if not trimmed:
        return ""

    cleaned = _strip_formatting(trimmed)
    if not cleaned:
        return ""

    digits_only = strip_to_digits(cleaned)
    if not digits_only:
        return ""

    has_plus = cleaned.startswith("+")

    sanitized_code = sanitize_default_dialing_code(default_dialing_code or "")

    if has_plus:
        localized = _localize_with_default(digits_only, sanitized_code)
        return localized if localized else digits_only

    if digits_only.startswith("00") and len(digits_only) > 2:
        stripped = digits_only[2:]
        localized = _localize_with_default(stripped, sanitized_code)
        return localized if localized else digits_only

    localized = _localize_with_default(digits_only, sanitized_code)
    if localized:
        return localized

    if digits_only.startswith("0"):
        return digits_only

    if sanitized_code and len(digits_only) >= 7:
        return f"0{digits_only}"

    return digits_only


def canonicalize_phone_number_for_device(
    raw_number: str | None, default_dialing_code: str | None
) -> str:
    """Return a device-friendly canonical phone number (E.164 when possible)."""

    if raw_number is None:
        return ""

    trimmed = str(raw_number).strip()
    if not trimmed:
        return ""

    sanitized_code = sanitize_default_dialing_code(default_dialing_code or "")

    normalized = normalize_phone_number(trimmed, sanitized_code)
    if not normalized:
        return ""

    if normalized.startswith("+"):
        return normalized

    digits_only = strip_to_digits(normalized)
    if not digits_only:
        return ""

    if digits_only.startswith("00") and len(digits_only) > 2:
        return f"+{digits_only[2:]}"

    if sanitized_code:
        if digits_only.startswith(sanitized_code):
            return f"+{digits_only}"

        if digits_only.startswith("0") and len(digits_only) >= 7:
            local_digits = _strip_leading_zeros(digits_only[1:])
            if local_digits:
                return f"+{sanitized_code}{local_digits}"

        if len(digits_only) >= 8:
            return f"+{sanitized_code}{digits_only}"

    return digits_only


def format_phone_number_for_display(
    raw_number: str | None, default_dialing_code: str | None
) -> str:
    """Format *raw_number* for UI display, localizing when possible."""

    if raw_number is None:
        return ""

    trimmed = str(raw_number).strip()
    if not trimmed:
        return ""

    sanitized_code = sanitize_default_dialing_code(default_dialing_code or "")
    cleaned = _strip_formatting(trimmed)
    digits_only = strip_to_digits(cleaned)

    if cleaned.startswith("+") and digits_only:
        if sanitized_code and digits_only.startswith(sanitized_code):
            remainder = digits_only[len(sanitized_code) :]
            if remainder.startswith("0"):
                return remainder
            if remainder:
                return f"0{remainder}"
        return f"+{digits_only}"

    if digits_only.startswith("00") and sanitized_code:
        remainder = digits_only[2:]
        if remainder.startswith(sanitized_code):
            remainder = remainder[len(sanitized_code) :]
            if remainder.startswith("0"):
                return remainder
            if remainder:
                return f"0{remainder}"
            return trimmed

    if sanitized_code and digits_only.startswith(sanitized_code):
        remainder = digits_only[len(sanitized_code) :]
        if remainder.startswith("0"):
            return remainder
        if remainder:
            return f"0{remainder}"

    return trimmed


def numbers_equivalent(
    lhs: str | None, rhs: str | None, default_dialing_code: str | None
) -> bool:
    """Return True when *lhs* and *rhs* represent the same phone number."""
    if lhs == rhs:
        return True

    norm_lhs = normalize_phone_number(lhs, default_dialing_code)
    norm_rhs = normalize_phone_number(rhs, default_dialing_code)

    if not norm_lhs or not norm_rhs:
        return False

    return norm_lhs.lower() == norm_rhs.lower()


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

    def canonicalize(self, number: str | None) -> str:
        """Return a device-friendly canonical value (E.164 when possible)."""

        return canonicalize_phone_number_for_device(number, self.default_code)

    def format_for_display(self, number: str | None) -> str:
        """Return a display-friendly representation for UI use."""

        return format_phone_number_for_display(number, self.default_code)
