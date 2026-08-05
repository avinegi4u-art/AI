"""Data access for the ``identity`` schema.

Keeping queries here rather than in the service keeps the PostGIS-specific details
(``ST_MakePoint``, geography casts) in one place, and lets the service layer be read as
plain domain logic.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marsool_user.models import Address, CourierProfile, MerchantStaff, User


def point_from_coordinates(latitude: Decimal, longitude: Decimal) -> object:
    """Build a PostGIS geography point.

    Note the argument order: ``ST_MakePoint`` takes longitude first (x, y), which is the
    opposite of how coordinates are written and a common source of silently wrong data.
    """
    return func.ST_SetSRID(func.ST_MakePoint(float(longitude), float(latitude)), 4326)


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    """Load a user with addresses and courier profile eagerly attached."""
    return (
        await session.execute(select(User).where(User.id == user_id))
    ).scalar_one_or_none()


async def get_user_by_phone(session: AsyncSession, phone: str) -> User | None:
    return (
        await session.execute(select(User).where(User.phone == phone))
    ).scalar_one_or_none()


async def get_address(session: AsyncSession, *, user_id: str, address_id: str) -> Address | None:
    """Load one of a user's addresses.

    Scoped by ``user_id`` so a caller cannot read another user's address by guessing an
    identifier.
    """
    return (
        await session.execute(
            select(Address).where(Address.id == address_id, Address.user_id == user_id)
        )
    ).scalar_one_or_none()


async def list_addresses(session: AsyncSession, *, user_id: str) -> list[Address]:
    return list(
        (
            await session.execute(
                select(Address).where(Address.user_id == user_id).order_by(Address.created_at)
            )
        )
        .scalars()
        .all()
    )


async def count_addresses(session: AsyncSession, *, user_id: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Address).where(Address.user_id == user_id)
            )
        ).scalar_one()
    )


async def get_courier_profile(session: AsyncSession, user_id: str) -> CourierProfile | None:
    return (
        await session.execute(select(CourierProfile).where(CourierProfile.user_id == user_id))
    ).scalar_one_or_none()


async def list_merchant_ids(session: AsyncSession, user_id: str) -> list[str]:
    """Return the merchants a user may administer."""
    return list(
        (
            await session.execute(
                select(MerchantStaff.merchant_id)
                .where(MerchantStaff.user_id == user_id)
                .order_by(MerchantStaff.merchant_id)
            )
        )
        .scalars()
        .all()
    )
