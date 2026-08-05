"""Money conversion and rounding."""

from __future__ import annotations

from decimal import Decimal

import pytest

from marsool_core.money import (
    UnsupportedCurrencyError,
    apply_basis_points,
    format_amount,
    from_minor_units,
    to_minor_units,
)


@pytest.mark.parametrize(
    ("amount", "currency", "expected"),
    [
        ("24.50", "AED", 2450),
        ("0.01", "AED", 1),
        ("0", "AED", 0),
        ("1.005", "KWD", 1005),
        ("100", "JPY", 100),
        (Decimal("19.99"), "USD", 1999),
    ],
)
def test_to_minor_units(amount: str | Decimal, currency: str, expected: int) -> None:
    assert to_minor_units(amount, currency) == expected


def test_rounding_is_half_up() -> None:
    # Banker's rounding would give 2450 here; half-up is the convention finance expects.
    assert to_minor_units("24.505", "AED") == 2451
    assert to_minor_units("24.504", "AED") == 2450


def test_roundtrip_preserves_value() -> None:
    for raw in ("0.00", "0.05", "13.37", "9999.99"):
        assert format_amount(to_minor_units(raw, "AED"), "AED") == raw


def test_from_minor_units_quantizes_to_currency_exponent() -> None:
    assert from_minor_units(2450, "AED") == Decimal("24.50")
    assert from_minor_units(1005, "KWD") == Decimal("1.005")
    assert from_minor_units(100, "JPY") == Decimal("100")


def test_unsupported_currency_rejected() -> None:
    with pytest.raises(UnsupportedCurrencyError):
        to_minor_units("10.00", "XYZ")


@pytest.mark.parametrize("amount", ["abc", "nan", "inf", ""])
def test_invalid_amounts_rejected(amount: str) -> None:
    with pytest.raises(ValueError, match="monetary amount"):
        to_minor_units(amount, "AED")


@pytest.mark.parametrize(
    ("minor_units", "basis_points", "expected"),
    [(10_000, 500, 500), (10_000, 0, 0), (999, 1_250, 125), (1, 5_000, 1)],
)
def test_apply_basis_points(minor_units: int, basis_points: int, expected: int) -> None:
    assert apply_basis_points(minor_units, basis_points) == expected


def test_negative_basis_points_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        apply_basis_points(100, -1)
