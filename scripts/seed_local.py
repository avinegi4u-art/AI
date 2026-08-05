#!/usr/bin/env python
"""Seed a local database with realistic Abu Dhabi data.

Creates merchants at real coordinates with opening hours and a service-area polygon, a
full menu for each, and a demo customer with two addresses. Everything a developer needs to
open the app and see a working storefront.

    python scripts/seed_local.py
    python scripts/seed_local.py --reset   # delete existing seed data first
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import time
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from marsool_auth.models import AuthPrincipal
from marsool_catalog import models as catalog_models
from marsool_catalog.config import CatalogSettings
from marsool_catalog.testing import seed_menu
from marsool_core.ids import PREFIX_USER, new_id
from marsool_core.security.principal import Role
from marsool_merchant import models as merchant_models
from marsool_merchant.models import MerchantStatus, ServiceArea
from marsool_merchant.testing import make_hours, make_merchant
from marsool_user import models as user_models
from marsool_user.models import Address, User
from marsool_user.repository import point_from_coordinates
from marsool_user.testing import AL_REEM_ISLAND, YAS_ISLAND

DEMO_PHONE = "+971501234567"

# Real Abu Dhabi venues, spread across the island so distance and radius filters do
# something visible in local development.
MERCHANTS = (
    {
        "name": "Al Mrzab Restaurant",
        "slug": "al-mrzab-restaurant",
        "latitude": "24.481200",
        "longitude": "54.365500",
        "cuisines": ["emirati", "grills"],
        "prep_time_minutes": 25,
        "delivery_radius_m": 8_000,
        "hours": [(day, time(11, 0), time(23, 30)) for day in range(7)],
    },
    {
        "name": "Corniche Karak House",
        "slug": "corniche-karak-house",
        "latitude": "24.475900",
        "longitude": "54.322000",
        "cuisines": ["coffee", "breakfast", "bakery"],
        "prep_time_minutes": 10,
        "delivery_radius_m": 6_000,
        # Late-night trading that runs past midnight.
        "hours": [(day, time(6, 0), time(2, 0)) for day in range(7)],
    },
    {
        "name": "Reem Island Sushi",
        "slug": "reem-island-sushi",
        "latitude": "24.496000",
        "longitude": "54.401000",
        "cuisines": ["japanese", "seafood", "healthy"],
        "prep_time_minutes": 20,
        "delivery_radius_m": 5_000,
        "hours": [(day, time(12, 0), time(23, 0)) for day in range(7)],
        # Polygon coverage instead of a radius: Al Reem Island only.
        "service_area": (
            "POLYGON((54.380 24.480, 54.425 24.480, 54.425 24.512, 54.380 24.512, "
            "54.380 24.480))"
        ),
    },
    {
        "name": "Yas Marina Grill",
        "slug": "yas-marina-grill",
        "latitude": "24.488600",
        "longitude": "54.607000",
        "cuisines": ["grills", "burgers", "american"],
        "prep_time_minutes": 30,
        "delivery_radius_m": 10_000,
        "hours": [(day, time(12, 0), time(1, 0)) for day in range(7)],
    },
    {
        "name": "Masdar Vegan Kitchen",
        "slug": "masdar-vegan-kitchen",
        "latitude": "24.427300",
        "longitude": "54.615600",
        "cuisines": ["vegan", "healthy"],
        "prep_time_minutes": 15,
        "delivery_radius_m": 7_000,
        "hours": [(day, time(9, 0), time(21, 0)) for day in range(6)],
        # Closed on Sundays, so `open_now=true` visibly filters it out one day a week.
        "status": MerchantStatus.ACTIVE,
    },
)


def build_engine() -> AsyncEngine:
    # Any service's settings resolve the same DSN; catalog is arbitrary.
    return create_async_engine(str(CatalogSettings().database_dsn))


async def reset(engine: AsyncEngine) -> None:
    """Delete seed data, leaving the schemas in place."""
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with factory() as session:
        # Cascades handle categories, items, groups, options, hours and service areas.
        await session.execute(delete(catalog_models.Menu))
        await session.execute(delete(merchant_models.Merchant))
        await session.execute(delete(user_models.User).where(User.phone == DEMO_PHONE))
        await session.execute(delete(AuthPrincipal).where(AuthPrincipal.phone == DEMO_PHONE))
        await session.commit()
    print("deleted existing seed data")


async def seed(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with factory() as session:
        for spec in MERCHANTS:
            existing = (
                await session.execute(
                    select(merchant_models.Merchant).where(
                        merchant_models.Merchant.slug == spec["slug"]
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                print(f"skip merchant {spec['slug']} (already present)")
                continue

            merchant = make_merchant(
                name=spec["name"],
                slug=spec["slug"],
                latitude=spec["latitude"],
                longitude=spec["longitude"],
                cuisines=spec["cuisines"],
                status=spec.get("status", MerchantStatus.ACTIVE),
                prep_time_minutes=spec["prep_time_minutes"],
                delivery_radius_m=spec["delivery_radius_m"],
                rating=Decimal("4.50"),
                rating_count=120,
                min_order_minor=2_000,
            )
            session.add(merchant)
            await session.flush()
            session.add_all(make_hours(merchant.id, spec["hours"]))
            if spec.get("service_area"):
                session.add(
                    ServiceArea(
                        merchant_id=merchant.id,
                        name="Al Reem Island",
                        boundary=func.ST_GeogFromText(f"SRID=4326;{spec['service_area']}"),
                        is_active=True,
                    )
                )
            await seed_menu(session, merchant_id=merchant.id)
            print(f"seeded merchant {merchant.slug} ({merchant.id})")
        await session.commit()

    async with factory() as session:
        customer = (
            await session.execute(select(User).where(User.phone == DEMO_PHONE))
        ).scalar_one_or_none()
        if customer is not None:
            print(f"skip demo customer (already present: {customer.id})")
            return

        # The auth principal and the profile must share one identifier. In production auth
        # mints the id and the user service adopts it from identity.user_registered; the seed
        # short-circuits that handshake so the demo customer can actually log in and see
        # their own name and addresses.
        user_id = new_id(PREFIX_USER)
        session.add(
            AuthPrincipal(
                id=user_id,
                phone=DEMO_PHONE,
                role=Role.CUSTOMER,
                display_name="Layla Al Mansouri",
            )
        )
        customer = User(
            id=user_id,
            phone=DEMO_PHONE,
            role=Role.CUSTOMER,
            name="Layla Al Mansouri",
            email="layla@example.ae",
            dietary_tags=["halal"],
        )
        session.add(customer)
        await session.flush()

        addresses = []
        for spec in (AL_REEM_ISLAND, YAS_ISLAND):
            address = Address(
                user_id=customer.id,
                label=spec["label"],
                line1=spec["line1"],
                apartment=spec.get("apartment"),
                community=spec.get("community"),
                city=spec["city"],
                emirate=spec["emirate"],
                country_code=spec["country_code"],
                latitude=Decimal(spec["latitude"]),
                longitude=Decimal(spec["longitude"]),
                location=point_from_coordinates(
                    Decimal(spec["latitude"]), Decimal(spec["longitude"])
                ),
                delivery_notes=spec.get("delivery_notes"),
            )
            session.add(address)
            addresses.append(address)
        await session.flush()
        customer.default_address_id = addresses[0].id
        await session.commit()
        print(f"seeded demo customer {customer.id} ({DEMO_PHONE}) with 2 addresses")


async def main_async(*, do_reset: bool) -> int:
    engine = build_engine()
    try:
        if do_reset:
            await reset(engine)
        await seed(engine)
    finally:
        await engine.dispose()
    print("\nSeed complete. Try:")
    print("  curl 'http://localhost:8000/merchants?lat=24.49464&lng=54.39946&radius_m=10000'")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="Delete existing seed data first")
    arguments = parser.parse_args()
    return asyncio.run(main_async(do_reset=arguments.reset))


if __name__ == "__main__":
    raise SystemExit(main())
