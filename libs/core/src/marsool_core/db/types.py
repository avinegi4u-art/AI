"""Reusable SQLAlchemy column types."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import CHAR, BigInteger, String, TypeDecorator
from sqlalchemy.engine import Dialect

#: Monetary amounts are stored as integer minor units (fils/cents). ``BigInteger``
#: leaves headroom for high-value aggregates such as merchant payout batches.
MinorUnits = BigInteger

#: ISO 4217 alphabetic currency code.
CurrencyCode = CHAR(3)

#: WGS84 point stored as ``geography`` so PostGIS distance operators return metres
#: without any manual projection.
Point4326 = Geography(geometry_type="POINT", srid=4326, spatial_index=True)

#: WGS84 polygon for merchant service areas and delivery zones.
Polygon4326 = Geography(geometry_type="POLYGON", srid=4326, spatial_index=True)


class StringEnum(TypeDecorator[Any]):
    """Persist a :class:`enum.StrEnum` as its string value.

    Preferred over ``sqlalchemy.Enum``: adding a new variant is a code change rather
    than a locking ``ALTER TYPE`` migration, and the stored value stays human-readable.
    A CHECK constraint on the table pins the allowed values.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[StrEnum], length: int = 32) -> None:
        self.enum_class = enum_class
        # Kept as an attribute so migration rendering can read it without reaching into
        # the underlying ``impl`` instance.
        self.length = length
        super().__init__(length=length)

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, self.enum_class):
            return str(value.value)
        # Accept plain strings, but validate them so typos fail fast at write time.
        return str(self.enum_class(value).value)

    def process_result_value(self, value: Any, dialect: Dialect) -> StrEnum | None:
        return None if value is None else self.enum_class(value)

    @property
    def python_type(self) -> type[StrEnum]:
        return self.enum_class


def enum_values(enum_class: type[StrEnum]) -> tuple[str, ...]:
    """Return the string values of a ``StrEnum``, for CHECK constraints."""
    return tuple(member.value for member in enum_class)
