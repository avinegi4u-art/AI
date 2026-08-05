"""User domain logic: profiles, addresses, courier details."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from marsool_core.errors import ConflictError, NotFoundError, PermissionDeniedError
from marsool_core.events.outbox import OutboxRepository
from marsool_core.logging import get_logger
from marsool_core.security.principal import Principal, Role
from marsool_user import repository
from marsool_user.config import UserSettings
from marsool_user.events import address_added, address_deleted, address_updated, profile_updated
from marsool_user.models import Address, CourierProfile, User, UserStatus
from marsool_user.repository import point_from_coordinates
from marsool_user.schemas import (
    AddressCreate,
    AddressResponse,
    AddressUpdate,
    CourierProfileResponse,
    CourierProfileUpdate,
    UserResponse,
    UserUpdate,
)

logger = get_logger(__name__)

# Address fields copied straight from the payload to the row. Coordinates are handled
# separately because they also drive the PostGIS geography column.
_ADDRESS_SCALAR_FIELDS = (
    "label",
    "nickname",
    "line1",
    "line2",
    "building",
    "apartment",
    "community",
    "makani_number",
    "city",
    "emirate",
    "country_code",
    "delivery_notes",
)


class UserService:
    """Profile, address and courier-detail operations."""

    def __init__(self, *, settings: UserSettings, outbox: OutboxRepository) -> None:
        self._settings = settings
        self._outbox = outbox

    # ------------------------------------------------------------------------ profile

    async def get_profile(self, session: AsyncSession, *, principal: Principal) -> UserResponse:
        """Return the caller's profile, provisioning it if the event has not arrived yet.

        Just-in-time provisioning closes the consistency gap after registration: auth
        creates the account and publishes ``identity.user_registered``, but a client can
        call this endpoint before the consumer has applied it. Rather than returning a
        confusing 404 for a user who just logged in successfully, the row is created from
        the verified JWT claims. The event consumer performs the same upsert, so whichever
        path runs first wins and the other is a no-op.
        """
        user = await repository.get_user(session, principal.id)
        if user is None:
            user = await self.provision_user(
                session, user_id=principal.id, phone=None, role=principal.role
            )
        return await self._to_response(session, user)

    async def update_profile(
        self, session: AsyncSession, *, principal: Principal, payload: UserUpdate
    ) -> UserResponse:
        """Apply a partial profile update."""
        user = await self._require_active_user(session, principal.id)
        changes = payload.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(user, field, value)

        if changes:
            await session.flush()
            self._outbox.enqueue(
                session,
                profile_updated(
                    user_id=user.id,
                    name=user.name,
                    email=user.email,
                    locale=user.locale,
                    dietary_tags=list(user.dietary_tags),
                ),
            )
            logger.info("profile_updated", user_id=user.id, fields=sorted(changes))
        return await self._to_response(session, user)

    async def provision_user(
        self,
        session: AsyncSession,
        *,
        user_id: str,
        phone: str | None,
        role: Role,
    ) -> User:
        """Create a profile row for an authenticated principal, idempotently.

        Called both by the ``identity.user_registered`` consumer and by just-in-time
        provisioning; either may run first.
        """
        existing = await repository.get_user(session, user_id)
        if existing is not None:
            return existing

        user = User(
            id=user_id,
            # Left unset when provisioning from a JWT, which carries no phone claim; the
            # registration event fills it in via ``reconcile_registration``.
            phone=phone,
            role=role,
            status=UserStatus.ACTIVE,
            locale=self._settings.default_locale,
        )
        session.add(user)
        if role is Role.COURIER:
            session.add(CourierProfile(user_id=user_id))
        await session.flush()
        # ``created_at``/``updated_at`` come from database defaults, so they are unset on
        # the in-memory object until refreshed. Reading them without this refresh triggers
        # implicit IO during response serialisation, which fails under asyncio.
        await session.refresh(user)
        logger.info("user_provisioned", user_id=user_id, role=role.value)
        return user

    async def reconcile_registration(
        self, session: AsyncSession, *, user_id: str, phone: str, role: Role
    ) -> User:
        """Apply ``identity.user_registered``, filling in a JIT-provisioned placeholder."""
        user = await self.provision_user(session, user_id=user_id, phone=phone, role=role)
        if user.phone != phone:
            user.phone = phone
            await session.flush()
        return user

    # ---------------------------------------------------------------------- addresses

    async def list_addresses(
        self, session: AsyncSession, *, principal: Principal
    ) -> list[AddressResponse]:
        """List the caller's delivery addresses, oldest first."""
        user = await self._require_active_user(session, principal.id)
        addresses = await repository.list_addresses(session, user_id=user.id)
        return [self._address_response(address, user) for address in addresses]

    async def create_address(
        self, session: AsyncSession, *, principal: Principal, payload: AddressCreate
    ) -> AddressResponse:
        """Add a delivery address.

        The first address a user adds becomes their default, since a customer with exactly
        one address should never have to pick it at checkout.
        """
        user = await self._require_active_user(session, principal.id)
        existing_count = await repository.count_addresses(session, user_id=user.id)
        if existing_count >= self._settings.max_addresses_per_user:
            raise ConflictError(
                "Address limit reached; delete an address before adding another",
                code="address_limit_reached",
                details={"limit": self._settings.max_addresses_per_user},
            )

        address = Address(
            user_id=user.id,
            latitude=payload.latitude,
            longitude=payload.longitude,
            location=point_from_coordinates(payload.latitude, payload.longitude),
            **{field: getattr(payload, field) for field in _ADDRESS_SCALAR_FIELDS},
        )
        session.add(address)
        await session.flush()
        # Load the database-generated timestamps and the normalised geography value.
        await session.refresh(address)

        if payload.set_as_default or existing_count == 0:
            user.default_address_id = address.id

        self._outbox.enqueue(
            session,
            address_added(
                user_id=user.id,
                address_id=address.id,
                latitude=str(address.latitude),
                longitude=str(address.longitude),
                city=address.city,
                is_default=user.default_address_id == address.id,
            ),
        )
        logger.info("address_added", user_id=user.id, address_id=address.id)
        return self._address_response(address, user)

    async def update_address(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        address_id: str,
        payload: AddressUpdate,
    ) -> AddressResponse:
        """Apply a partial update to one of the caller's addresses."""
        user = await self._require_active_user(session, principal.id)
        address = await repository.get_address(session, user_id=user.id, address_id=address_id)
        if address is None:
            raise NotFoundError("Address not found", code="address_not_found")

        changes = payload.model_dump(exclude_unset=True)
        set_as_default = changes.pop("set_as_default", None)
        for field, value in changes.items():
            setattr(address, field, value)

        if "latitude" in changes or "longitude" in changes:
            # The geography column must be recomputed whenever either coordinate moves,
            # or distance queries would keep using the old position.
            address.location = point_from_coordinates(  # type: ignore[assignment]
                Decimal(str(address.latitude)), Decimal(str(address.longitude))
            )

        if set_as_default:
            user.default_address_id = address.id
        elif set_as_default is False and user.default_address_id == address.id:
            user.default_address_id = None

        if changes or set_as_default is not None:
            await session.flush()
            # ``updated_at`` is regenerated by the database on update.
            await session.refresh(address)
            self._outbox.enqueue(
                session,
                address_updated(
                    user_id=user.id, address_id=address.id, changed_fields=sorted(changes)
                ),
            )
        return self._address_response(address, user)

    async def delete_address(
        self, session: AsyncSession, *, principal: Principal, address_id: str
    ) -> None:
        """Delete one of the caller's addresses, promoting a new default if needed."""
        user = await self._require_active_user(session, principal.id)
        address = await repository.get_address(session, user_id=user.id, address_id=address_id)
        if address is None:
            raise NotFoundError("Address not found", code="address_not_found")

        was_default = user.default_address_id == address.id
        await session.delete(address)
        await session.flush()

        if was_default:
            # Leaving a dangling default would break checkout, so promote the oldest
            # remaining address.
            remaining = await repository.list_addresses(session, user_id=user.id)
            user.default_address_id = remaining[0].id if remaining else None

        self._outbox.enqueue(session, address_deleted(user_id=user.id, address_id=address.id))
        logger.info("address_deleted", user_id=user.id, address_id=address_id)

    # ---------------------------------------------------------------- courier profile

    async def update_courier_profile(
        self, session: AsyncSession, *, principal: Principal, payload: CourierProfileUpdate
    ) -> CourierProfileResponse:
        """Update the caller's courier details."""
        if principal.role is not Role.COURIER:
            raise PermissionDeniedError(
                "Only couriers have a courier profile", code="not_a_courier"
            )
        user = await self._require_active_user(session, principal.id)
        profile = await repository.get_courier_profile(session, user.id)
        if profile is None:
            profile = CourierProfile(user_id=user.id)
            session.add(profile)

        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(profile, field, value)
        await session.flush()
        return CourierProfileResponse.model_validate(profile)

    # ------------------------------------------------------------------------ helpers

    async def _require_active_user(self, session: AsyncSession, user_id: str) -> User:
        user = await repository.get_user(session, user_id)
        if user is None:
            raise NotFoundError("User not found", code="user_not_found")
        if not user.is_active:
            raise PermissionDeniedError(
                "This account is not active",
                code="account_not_active",
                details={"status": user.status.value},
            )
        return user

    @staticmethod
    def _address_response(address: Address, user: User) -> AddressResponse:
        response = AddressResponse.model_validate(address)
        # ``is_default`` lives on the user, not the address, so that a single column update
        # switches the default rather than two rows needing to stay consistent.
        return response.model_copy(update={"is_default": user.default_address_id == address.id})

    async def _to_response(self, session: AsyncSession, user: User) -> UserResponse:
        """Assemble the profile response from explicit queries.

        Built field by field rather than with ``model_validate(user)`` because the
        relationship attributes are configured to refuse lazy loading, and role-specific
        data should only be queried for the roles that have it.
        """
        courier_profile = (
            await repository.get_courier_profile(session, user.id)
            if user.role is Role.COURIER
            else None
        )
        merchant_ids = (
            await repository.list_merchant_ids(session, user.id)
            if user.role is Role.MERCHANT
            else []
        )
        return UserResponse(
            id=user.id,
            phone=user.phone,
            role=user.role,
            status=user.status,
            name=user.name,
            email=user.email,
            locale=user.locale,
            date_of_birth=user.date_of_birth,
            avatar_url=user.avatar_url,
            marketing_opt_in=user.marketing_opt_in,
            dietary_tags=list(user.dietary_tags),
            default_address_id=user.default_address_id,
            created_at=user.created_at,
            courier_profile=(
                CourierProfileResponse.model_validate(courier_profile)
                if courier_profile is not None
                else None
            ),
            merchant_ids=merchant_ids,
        )
