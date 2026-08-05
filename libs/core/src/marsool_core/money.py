"""Money handling.

Money is stored as integer minor units (fils for AED, cents for USD) and exposed on
the wire as a decimal string with the currency's exponent, e.g. ``"24.50"``. Floats
are never used for money anywhere in the platform.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Final

# ISO 4217 exponents for the currencies we support. Extend deliberately: adding a
# currency without its exponent would silently mis-scale amounts.
CURRENCY_EXPONENTS: Final[dict[str, int]] = {
    "AED": 2,
    "SAR": 2,
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "KWD": 3,
    "BHD": 3,
    "OMR": 3,
    "JPY": 0,
}

DEFAULT_CURRENCY: Final = "AED"


class UnsupportedCurrencyError(ValueError):
    """Raised when a currency code has no registered exponent."""


def exponent_for(currency: str) -> int:
    """Return the number of decimal places used by ``currency``."""
    try:
        return CURRENCY_EXPONENTS[currency.upper()]
    except KeyError as exc:
        raise UnsupportedCurrencyError(f"unsupported currency: {currency!r}") from exc


def to_minor_units(amount: Decimal | int | str, currency: str) -> int:
    """Convert a decimal amount to integer minor units, rounding half-up.

    >>> to_minor_units(Decimal("24.505"), "AED")
    2451
    """
    exponent = exponent_for(currency)
    try:
        decimal_amount = Decimal(str(amount))
    except InvalidOperation as exc:
        raise ValueError(f"not a valid monetary amount: {amount!r}") from exc
    if not decimal_amount.is_finite():
        raise ValueError(f"not a finite monetary amount: {amount!r}")
    scaled = decimal_amount.scaleb(exponent).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(scaled)


def from_minor_units(minor_units: int, currency: str) -> Decimal:
    """Convert integer minor units back to a quantized decimal amount.

    >>> str(from_minor_units(2450, "AED"))
    '24.50'
    """
    exponent = exponent_for(currency)
    quantum = Decimal(1).scaleb(-exponent)
    return (Decimal(minor_units).scaleb(-exponent)).quantize(quantum)


def format_amount(minor_units: int, currency: str) -> str:
    """Render minor units as a wire-format decimal string, e.g. ``"24.50"``."""
    return str(from_minor_units(minor_units, currency))


def apply_basis_points(minor_units: int, basis_points: int) -> int:
    """Apply a basis-point rate (1 bp = 0.01%) with half-up rounding.

    Used for commission and tax-style calculations where the rate is configured as an
    integer to avoid storing floats.
    """
    if basis_points < 0:
        raise ValueError("basis_points must be non-negative")
    scaled = (Decimal(minor_units) * Decimal(basis_points) / Decimal(10_000)).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )
    return int(scaled)
