"""Catalog domain logic: menu assembly, caching, item management, basket validation."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from marsool_catalog import repository
from marsool_catalog.basket import validate_basket
from marsool_catalog.config import CatalogSettings
from marsool_catalog.events import item_availability_changed, item_updated
from marsool_catalog.models import MenuItem
from marsool_catalog.schemas import (
    BasketValidationRequest,
    BasketValidationResponse,
    ItemAvailabilityUpdate,
    MenuCategoryResponse,
    MenuItemResponse,
    MenuItemUpdate,
    MenuResponse,
    OptionGroupResponse,
    OptionResponse,
)
from marsool_core.cache import JsonCache, cache_key
from marsool_core.errors import NotFoundError, PermissionDeniedError
from marsool_core.events.outbox import OutboxRepository
from marsool_core.logging import get_logger
from marsool_core.money import from_minor_units, to_minor_units
from marsool_core.security.principal import Principal, Role

logger = get_logger(__name__)

# Item fields that map straight onto a column of the same name.
_DIRECT_ITEM_FIELDS = (
    "name",
    "description",
    "image_url",
    "tags",
    "calories",
    "spice_level",
    "is_available",
)


class CatalogService:
    """Menu reads, item management and basket validation."""

    def __init__(
        self,
        *,
        settings: CatalogSettings,
        outbox: OutboxRepository,
        menu_cache: JsonCache,
    ) -> None:
        self._settings = settings
        self._outbox = outbox
        self._menu_cache = menu_cache

    # ---------------------------------------------------------------------- menu reads

    async def get_menu(self, session: AsyncSession, *, merchant_id: str) -> MenuResponse:
        """Return a merchant's active menu, nested and cached.

        The cache is checked first because this is the most-read endpoint in the platform;
        entries are invalidated on any item change, so a sold-out item disappears
        immediately rather than lingering for the TTL.
        """
        key = self._menu_key(merchant_id)
        cached = await self._menu_cache.get(key)
        if cached is not None:
            return MenuResponse.model_validate(cached)

        response = await self._build_menu(session, merchant_id=merchant_id)
        await self._menu_cache.set(
            key, response.model_dump(mode="json"), ttl_seconds=self._settings.menu_cache_ttl_seconds
        )
        return response

    async def _build_menu(self, session: AsyncSession, *, merchant_id: str) -> MenuResponse:
        menu = await repository.get_active_menu(session, merchant_id)
        if menu is None:
            raise NotFoundError(
                "This merchant has no active menu", code="menu_not_found",
                details={"merchant_id": merchant_id},
            )

        categories, items, groups, options = await repository.load_menu_tree(session, menu.id)

        options_by_group: dict[str, list[OptionResponse]] = {}
        for option in options:
            options_by_group.setdefault(option.group_id, []).append(
                OptionResponse(
                    id=option.id,
                    name=option.name,
                    price_delta=from_minor_units(option.price_delta_minor, menu_currency(items)),
                    is_available=option.is_available,
                    is_default=option.is_default,
                )
            )

        groups_by_item: dict[str, list[OptionGroupResponse]] = {}
        for group in groups:
            groups_by_item.setdefault(group.item_id, []).append(
                OptionGroupResponse(
                    id=group.id,
                    name=group.name,
                    selection_type=group.selection_type,
                    min_select=group.min_select,
                    max_select=group.max_select,
                    is_required=group.is_required,
                    options=options_by_group.get(group.id, []),
                )
            )

        items_by_category: dict[str, list[MenuItemResponse]] = {}
        for item in items:
            items_by_category.setdefault(item.category_id, []).append(
                MenuItemResponse(
                    id=item.id,
                    name=item.name,
                    description=item.description,
                    price=from_minor_units(item.price_minor, item.currency),
                    currency=item.currency,
                    is_available=item.is_available,
                    image_url=item.image_url,
                    tags=list(item.tags),
                    calories=item.calories,
                    spice_level=item.spice_level,
                    option_groups=groups_by_item.get(item.id, []),
                )
            )

        return MenuResponse(
            merchant_id=merchant_id,
            menu_id=menu.id,
            name=menu.name,
            currency=menu_currency(items),
            categories=[
                MenuCategoryResponse(
                    id=category.id,
                    name=category.name,
                    description=category.description,
                    items=items_by_category.get(category.id, []),
                )
                for category in categories
            ],
            item_count=len(items),
            available_item_count=sum(1 for item in items if item.is_available),
        )

    # ----------------------------------------------------------------- item management

    async def set_item_availability(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        item_id: str,
        payload: ItemAvailabilityUpdate,
    ) -> MenuItemResponse:
        """Mark an item in or out of stock."""
        item = await self._require_item(session, item_id)
        self._require_merchant_access(principal, item.merchant_id)

        if item.is_available != payload.is_available:
            item.is_available = payload.is_available
            await session.flush()
            self._outbox.enqueue(
                session,
                item_availability_changed(
                    item_id=item.id,
                    merchant_id=item.merchant_id,
                    is_available=item.is_available,
                    reason=payload.reason,
                ),
            )
            await self._invalidate_menu(item.merchant_id)
            logger.info(
                "item_availability_changed",
                item_id=item.id,
                is_available=item.is_available,
                reason=payload.reason,
            )
        return self._item_response(item)

    async def update_item(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        item_id: str,
        payload: MenuItemUpdate,
    ) -> MenuItemResponse:
        """Apply a partial item update."""
        item = await self._require_item(session, item_id)
        self._require_merchant_access(principal, item.merchant_id)

        changes = payload.model_dump(exclude_unset=True)
        for field in _DIRECT_ITEM_FIELDS:
            if field in changes:
                setattr(item, field, changes[field])
        if "price" in changes:
            item.price_minor = to_minor_units(changes["price"], item.currency)

        if changes:
            await session.flush()
            self._outbox.enqueue(
                session,
                item_updated(
                    item_id=item.id,
                    merchant_id=item.merchant_id,
                    changed_fields=sorted(changes),
                ),
            )
            await self._invalidate_menu(item.merchant_id)
            logger.info("item_updated", item_id=item.id, fields=sorted(changes))
        return self._item_response(item)

    # -------------------------------------------------------------- basket validation

    async def validate_basket(
        self,
        session: AsyncSession,
        *,
        merchant_id: str,
        payload: BasketValidationRequest,
    ) -> BasketValidationResponse:
        """Validate and price a basket against the merchant's catalogue."""
        snapshot = await repository.load_basket_snapshot(
            session, item_ids=[line.item_id for line in payload.lines]
        )
        currency = next(
            (item.currency for item in snapshot.items.values()), "AED"
        )
        return validate_basket(
            merchant_id=merchant_id,
            lines=payload.lines,
            snapshot=snapshot,
            currency=currency,
        )

    # ------------------------------------------------------------------------ helpers

    @staticmethod
    def _menu_key(merchant_id: str) -> str:
        return cache_key("catalog", "menu", merchant_id)

    async def _invalidate_menu(self, merchant_id: str) -> None:
        """Drop the cached menu so the next read reflects the change immediately."""
        await self._menu_cache.delete(self._menu_key(merchant_id))

    async def _require_item(self, session: AsyncSession, item_id: str) -> MenuItem:
        item = await repository.get_item(session, item_id)
        if item is None:
            raise NotFoundError("Item not found", code="item_not_found")
        return item

    @staticmethod
    def _require_merchant_access(principal: Principal, merchant_id: str) -> None:
        if principal.role is Role.ADMIN:
            return
        if not principal.can_act_for_merchant(merchant_id):
            raise PermissionDeniedError(
                "You may not administer this merchant's catalogue",
                code="merchant_access_denied",
                details={"merchant_id": merchant_id},
            )

    @staticmethod
    def _item_response(item: MenuItem) -> MenuItemResponse:
        return MenuItemResponse(
            id=item.id,
            name=item.name,
            description=item.description,
            price=from_minor_units(item.price_minor, item.currency),
            currency=item.currency,
            is_available=item.is_available,
            image_url=item.image_url,
            tags=list(item.tags),
            calories=item.calories,
            spice_level=item.spice_level,
        )


def menu_currency(items: list[MenuItem], *, default: str = "AED") -> str:
    """Return the currency a menu is priced in.

    A merchant prices its whole menu in one currency, so the first item's currency is
    authoritative; the default only applies to an empty menu.
    """
    return items[0].currency if items else default
