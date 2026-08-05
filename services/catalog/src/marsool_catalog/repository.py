"""Data access for the ``catalog`` schema."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marsool_catalog.basket import CatalogSnapshot
from marsool_catalog.models import Menu, MenuCategory, MenuItem, Option, OptionGroup


async def get_active_menu(session: AsyncSession, merchant_id: str) -> Menu | None:
    """Return a merchant's active menu, lowest position first."""
    return (
        await session.execute(
            select(Menu)
            .where(Menu.merchant_id == merchant_id, Menu.is_active.is_(True))
            .order_by(Menu.position, Menu.created_at)
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_item(session: AsyncSession, item_id: str) -> MenuItem | None:
    return (
        await session.execute(select(MenuItem).where(MenuItem.id == item_id))
    ).scalar_one_or_none()


async def load_menu_tree(
    session: AsyncSession, menu_id: str
) -> tuple[list[MenuCategory], list[MenuItem], list[OptionGroup], list[Option]]:
    """Load a whole menu in four queries.

    Four flat queries assembled in Python rather than a nested eager load: the join would
    multiply rows by categories x items x groups x options, and this shape stays predictable
    as menus grow.
    """
    categories = list(
        (
            await session.execute(
                select(MenuCategory)
                .where(MenuCategory.menu_id == menu_id, MenuCategory.is_active.is_(True))
                .order_by(MenuCategory.position, MenuCategory.name)
            )
        )
        .scalars()
        .all()
    )
    if not categories:
        return [], [], [], []

    category_ids = [category.id for category in categories]
    items = list(
        (
            await session.execute(
                select(MenuItem)
                .where(MenuItem.category_id.in_(category_ids))
                .order_by(MenuItem.position, MenuItem.name)
            )
        )
        .scalars()
        .all()
    )
    if not items:
        return categories, [], [], []

    item_ids = [item.id for item in items]
    groups = list(
        (
            await session.execute(
                select(OptionGroup)
                .where(OptionGroup.item_id.in_(item_ids))
                .order_by(OptionGroup.position, OptionGroup.name)
            )
        )
        .scalars()
        .all()
    )
    group_ids = [group.id for group in groups]
    options = (
        list(
            (
                await session.execute(
                    select(Option)
                    .where(Option.group_id.in_(group_ids))
                    .order_by(Option.position, Option.name)
                )
            )
            .scalars()
            .all()
        )
        if group_ids
        else []
    )
    return categories, items, groups, options


async def load_basket_snapshot(
    session: AsyncSession, *, item_ids: list[str]
) -> CatalogSnapshot:
    """Load exactly the items, groups and options a basket refers to."""
    if not item_ids:
        return CatalogSnapshot(items={}, groups_by_item={}, options_by_group={})

    items = list(
        (await session.execute(select(MenuItem).where(MenuItem.id.in_(item_ids))))
        .scalars()
        .all()
    )
    groups = list(
        (
            await session.execute(
                select(OptionGroup)
                .where(OptionGroup.item_id.in_([item.id for item in items]))
                .order_by(OptionGroup.position)
            )
        )
        .scalars()
        .all()
    )
    options = (
        list(
            (
                await session.execute(
                    select(Option)
                    .where(Option.group_id.in_([group.id for group in groups]))
                    .order_by(Option.position)
                )
            )
            .scalars()
            .all()
        )
        if groups
        else []
    )

    groups_by_item: dict[str, list[OptionGroup]] = {}
    for group in groups:
        groups_by_item.setdefault(group.item_id, []).append(group)
    options_by_group: dict[str, list[Option]] = {}
    for option in options:
        options_by_group.setdefault(option.group_id, []).append(option)

    return CatalogSnapshot(
        items={item.id: item for item in items},
        groups_by_item=groups_by_item,
        options_by_group=options_by_group,
    )
