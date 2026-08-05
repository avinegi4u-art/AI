"""The authenticated caller and the platform role model."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    """Platform roles. A user has exactly one primary role.

    ``SERVICE`` is used for machine-to-machine calls (background workers, AI agents),
    which are authorized against an explicit scope list rather than a user role.
    """

    CUSTOMER = "customer"
    MERCHANT = "merchant"
    COURIER = "courier"
    ADMIN = "admin"
    SERVICE = "service"

    @property
    def is_staff(self) -> bool:
        return self is Role.ADMIN

    @property
    def is_human(self) -> bool:
        return self is not Role.SERVICE


class Principal(BaseModel):
    """The authenticated caller for the current request."""

    model_config = ConfigDict(frozen=True)

    id: str
    role: Role
    scopes: frozenset[str] = Field(default_factory=frozenset)
    # Populated for merchant users so authorization can be scoped to their merchants.
    merchant_ids: tuple[str, ...] = ()
    session_id: str | None = None

    def has_role(self, *roles: Role) -> bool:
        return self.role in roles

    def has_scope(self, scope: str) -> bool:
        """Return True when the principal holds ``scope``.

        Admins bypass scope checks; service principals must hold the scope explicitly.
        """
        return self.role is Role.ADMIN or scope in self.scopes

    def can_act_for_merchant(self, merchant_id: str) -> bool:
        """Return True when the principal may administer ``merchant_id``."""
        if self.role is Role.ADMIN:
            return True
        return self.role is Role.MERCHANT and merchant_id in self.merchant_ids
