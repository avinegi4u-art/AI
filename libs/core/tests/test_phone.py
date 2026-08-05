"""Phone number normalisation."""

from __future__ import annotations

import pytest

from marsool_core.phone import (
    InvalidPhoneNumberError,
    is_valid_phone,
    mask_phone,
    normalize_phone,
)

CANONICAL = "+971501234567"


@pytest.mark.parametrize(
    "raw",
    [
        "+971501234567",
        "+971 50 123 4567",
        "+971-50-123-4567",
        "971501234567",
        "0501234567",
        "050 123 4567",
        "  +971501234567  ",
    ],
)
def test_uae_numbers_normalise_to_one_canonical_form(raw: str) -> None:
    assert normalize_phone(raw) == CANONICAL


def test_saudi_market_normalisation() -> None:
    assert normalize_phone("0512345678", default_market="SA") == "+966512345678"


def test_international_number_from_other_country_is_preserved() -> None:
    assert normalize_phone("+442071838750") == "+442071838750"


@pytest.mark.parametrize("raw", ["", "   ", "123", "abcdefgh", "+0123456789", "12345"])
def test_invalid_numbers_rejected(raw: str) -> None:
    with pytest.raises(InvalidPhoneNumberError):
        normalize_phone(raw)


def test_unsupported_market_rejected() -> None:
    with pytest.raises(InvalidPhoneNumberError, match="Unsupported market"):
        normalize_phone("0501234567", default_market="ZZ")


def test_is_valid_phone_does_not_raise() -> None:
    assert is_valid_phone("0501234567")
    assert not is_valid_phone("nope")


def test_mask_phone_hides_the_middle() -> None:
    masked = mask_phone(CANONICAL)
    assert masked == "+9715****4567"
    assert CANONICAL not in masked


def test_mask_phone_fully_masks_short_input() -> None:
    assert mask_phone("12345") == "*****"
