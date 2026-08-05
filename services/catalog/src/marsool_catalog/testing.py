"""Sample catalogue data shipped with the catalog service.

A realistic Emirati menu with a required single-select group, an optional multi-select
group, a priced add-on and a sold-out option, shared by tests and the local seed script.
"""

from __future__ import annotations

from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from marsool_catalog.models import Menu, MenuCategory, MenuItem, Option, OptionGroup, SelectionType
from marsool_core.ids import (
    PREFIX_CATEGORY,
    PREFIX_MENU,
    PREFIX_MENU_ITEM,
    PREFIX_OPTION,
    PREFIX_OPTION_GROUP,
    new_id,
)

#: Menu structure: categories, their items, and each item's option groups.
EMIRATI_MENU: Final[dict[str, Any]] = {
    "menu": {"name": "All Day Menu"},
    "categories": [
        {
            "name": "Mains",
            "position": 0,
            "items": [
                {
                    "name": "Chicken Machboos",
                    "description": "Slow-cooked spiced rice with chicken",
                    "price_minor": 3_800,
                    "tags": ["halal", "house_special"],
                    "calories": 720,
                    "spice_level": 1,
                    "option_groups": [
                        {
                            "name": "Portion",
                            "selection_type": SelectionType.SINGLE,
                            "min_select": 1,
                            "max_select": 1,
                            "options": [
                                {"name": "Regular", "price_delta_minor": 0, "is_default": True},
                                {"name": "Large", "price_delta_minor": 1_200},
                            ],
                        },
                        {
                            "name": "Add-ons",
                            "selection_type": SelectionType.MULTI,
                            "min_select": 0,
                            "max_select": 3,
                            "options": [
                                {"name": "Extra chicken", "price_delta_minor": 1_500},
                                {"name": "Salad", "price_delta_minor": 700},
                                {"name": "Laban", "price_delta_minor": 500},
                                {
                                    "name": "Sold-out side",
                                    "price_delta_minor": 400,
                                    "is_available": False,
                                },
                            ],
                        },
                    ],
                },
                {
                    "name": "Grilled Hammour",
                    "description": "Local reef fish with lemon rice",
                    "price_minor": 6_500,
                    "tags": ["halal", "seafood"],
                    "calories": 540,
                    "option_groups": [],
                },
            ],
        },
        {
            "name": "Drinks",
            "position": 1,
            "items": [
                {
                    "name": "Karak Chai",
                    "price_minor": 900,
                    "tags": ["halal"],
                    "option_groups": [],
                }
            ],
        },
    ],
}


async def seed_menu(
    session: AsyncSession, *, merchant_id: str, spec: dict[str, Any] | None = None
) -> dict[str, str]:
    """Insert a whole menu and return a name-to-id lookup.

    Rows are inserted level by level with a flush between levels. A single ``add_all`` of a
    flat graph relies on SQLAlchemy inferring insert order across five tables that have no
    ORM relationships between them, which is fragile; being explicit is not.

    Returns:
        Keys of the form ``"item:Karak Chai"``, ``"group:Portion"``, ``"option:Large"`` and
        ``"menu"``, so callers never hard-code generated identifiers.
    """
    resolved = spec or EMIRATI_MENU
    lookup: dict[str, str] = {}

    menu = Menu(
        id=new_id(PREFIX_MENU),
        merchant_id=merchant_id,
        name=resolved["menu"]["name"],
        is_active=True,
    )
    session.add(menu)
    await session.flush()
    lookup["menu"] = menu.id

    for category_spec in resolved["categories"]:
        category = MenuCategory(
            id=new_id(PREFIX_CATEGORY),
            menu_id=menu.id,
            name=category_spec["name"],
            position=category_spec.get("position", 0),
        )
        session.add(category)
        await session.flush()
        lookup[f"category:{category.name}"] = category.id

        for position, item_spec in enumerate(category_spec["items"]):
            item = MenuItem(
                id=new_id(PREFIX_MENU_ITEM),
                merchant_id=merchant_id,
                category_id=category.id,
                name=item_spec["name"],
                description=item_spec.get("description"),
                price_minor=item_spec["price_minor"],
                currency=item_spec.get("currency", "AED"),
                is_available=item_spec.get("is_available", True),
                tags=item_spec.get("tags", []),
                calories=item_spec.get("calories"),
                spice_level=item_spec.get("spice_level", 0),
                position=position,
            )
            session.add(item)
            await session.flush()
            lookup[f"item:{item.name}"] = item.id

            for group_position, group_spec in enumerate(item_spec.get("option_groups", [])):
                group = OptionGroup(
                    id=new_id(PREFIX_OPTION_GROUP),
                    item_id=item.id,
                    name=group_spec["name"],
                    selection_type=group_spec["selection_type"],
                    min_select=group_spec["min_select"],
                    max_select=group_spec["max_select"],
                    position=group_position,
                )
                session.add(group)
                await session.flush()
                lookup[f"group:{group.name}"] = group.id

                for option_position, option_spec in enumerate(group_spec["options"]):
                    option = Option(
                        id=new_id(PREFIX_OPTION),
                        group_id=group.id,
                        name=option_spec["name"],
                        price_delta_minor=option_spec["price_delta_minor"],
                        is_available=option_spec.get("is_available", True),
                        is_default=option_spec.get("is_default", False),
                        position=option_position,
                    )
                    session.add(option)
                    lookup[f"option:{option.name}"] = option.id
                await session.flush()

    return lookup
