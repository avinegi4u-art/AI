"""User HTTP endpoints.

Every route is scoped to the authenticated caller (``/users/me``). There is deliberately
no ``GET /users/{user_id}``: making one user readable by identifier is how customer data
leaks, and the internal callers that need it (support tooling, dispatch) will get a
purpose-built, scope-gated endpoint instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from marsool_user.deps import (
    CourierOnly,
    CurrentPrincipal,
    SessionDep,
    UserServiceDep,
)
from marsool_user.schemas import (
    AddressCreate,
    AddressResponse,
    AddressUpdate,
    CourierProfileResponse,
    CourierProfileUpdate,
    UserResponse,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the authenticated user's profile",
    description=(
        "Returns the caller's profile. If the profile row has not yet been created by the "
        "`identity.user_registered` consumer, it is provisioned from the verified token "
        "claims so a freshly registered user never sees a 404."
    ),
    name="get_me",
)
async def get_me(
    principal: CurrentPrincipal,
    session: SessionDep,
    user_service: UserServiceDep,
) -> UserResponse:
    return await user_service.get_profile(session, principal=principal)


@router.put(
    "/me",
    response_model=UserResponse,
    summary="Update the authenticated user's profile",
    description=(
        "Partial update: omitted fields are unchanged. Phone number and role cannot be "
        "changed here — a phone change requires OTP verification through the auth service, "
        "and a role change is an administrative action."
    ),
    name="update_me",
)
async def update_me(
    payload: UserUpdate,
    principal: CurrentPrincipal,
    session: SessionDep,
    user_service: UserServiceDep,
) -> UserResponse:
    return await user_service.update_profile(session, principal=principal, payload=payload)


@router.get(
    "/me/addresses",
    response_model=list[AddressResponse],
    summary="List the caller's delivery addresses",
    name="list_addresses",
)
async def list_addresses(
    principal: CurrentPrincipal,
    session: SessionDep,
    user_service: UserServiceDep,
) -> list[AddressResponse]:
    return await user_service.list_addresses(session, principal=principal)


@router.post(
    "/me/addresses",
    response_model=AddressResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a delivery address",
    description=(
        "Adds an address for the caller. The first address added becomes the default. "
        "Coordinates are required: they drive serviceability checks, delivery pricing and "
        "courier routing."
    ),
    name="create_address",
)
async def create_address(
    payload: AddressCreate,
    principal: CurrentPrincipal,
    session: SessionDep,
    user_service: UserServiceDep,
) -> AddressResponse:
    return await user_service.create_address(session, principal=principal, payload=payload)


@router.put(
    "/me/addresses/{address_id}",
    response_model=AddressResponse,
    summary="Update a delivery address",
    description="Partial update of one of the caller's addresses.",
    name="update_address",
)
async def update_address(
    address_id: str,
    payload: AddressUpdate,
    principal: CurrentPrincipal,
    session: SessionDep,
    user_service: UserServiceDep,
) -> AddressResponse:
    return await user_service.update_address(
        session, principal=principal, address_id=address_id, payload=payload
    )


@router.delete(
    "/me/addresses/{address_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a delivery address",
    description=(
        "Deletes one of the caller's addresses. If it was the default, the oldest remaining "
        "address is promoted so checkout always has a default to offer."
    ),
    name="delete_address",
)
async def delete_address(
    address_id: str,
    principal: CurrentPrincipal,
    session: SessionDep,
    user_service: UserServiceDep,
) -> Response:
    await user_service.delete_address(session, principal=principal, address_id=address_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/me/courier-profile",
    response_model=CourierProfileResponse,
    summary="Update the caller's courier details",
    description=(
        "Couriers only. Rating and completed-delivery counts are derived from order "
        "outcomes and cannot be set here."
    ),
    name="update_courier_profile",
)
async def update_courier_profile(
    payload: CourierProfileUpdate,
    principal: CourierOnly,
    session: SessionDep,
    user_service: UserServiceDep,
) -> CourierProfileResponse:
    return await user_service.update_courier_profile(
        session, principal=principal, payload=payload
    )
