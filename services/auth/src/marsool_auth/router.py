"""Auth HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from marsool_auth.deps import (
    AuthServiceDep,
    ClientContextDep,
    CurrentPrincipal,
    SessionDep,
)
from marsool_auth.schemas import (
    LoginRequest,
    LogoutRequest,
    OtpChallengeResponse,
    OtpRequest,
    RefreshRequest,
    SessionSummary,
    TokenPair,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/otp",
    response_model=OtpChallengeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Request a one-time password",
    description=(
        "Sends a one-time password to the given phone number and returns the challenge "
        "metadata. The response is identical whether or not the number is registered, so "
        "it cannot be used to enumerate accounts. Requests are rate limited per phone "
        "number; exceeding the limit returns 429 with a `Retry-After` header."
    ),
    name="request_otp",
)
async def request_otp(
    payload: OtpRequest,
    session: SessionDep,
    auth_service: AuthServiceDep,
) -> OtpChallengeResponse:
    return await auth_service.request_otp(session, phone=payload.phone)


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange a phone number and OTP for tokens",
    description=(
        "Verifies the one-time password and issues an access/refresh token pair. The "
        "account is created on first successful login. Invalid, expired and unknown codes "
        "all return the same 401 error code."
    ),
    name="login",
)
async def login(
    payload: LoginRequest,
    session: SessionDep,
    auth_service: AuthServiceDep,
    client: ClientContextDep,
) -> TokenPair:
    return await auth_service.login(
        session, phone=payload.phone, otp=payload.otp, client=client
    )


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate a refresh token",
    description=(
        "Exchanges a refresh token for a new pair and invalidates the presented token. "
        "Reusing an already-rotated token revokes the entire session and returns 401."
    ),
    name="refresh_token",
)
async def refresh_token(
    payload: RefreshRequest,
    session: SessionDep,
    auth_service: AuthServiceDep,
    client: ClientContextDep,
) -> TokenPair:
    return await auth_service.refresh(
        session, refresh_token=payload.refresh_token, client=client
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke one session or all sessions",
    description=(
        "Revokes the session behind the supplied refresh token, or every session for the "
        "authenticated user when `all_sessions` is true. Idempotent: revoking an unknown "
        "or already-revoked token succeeds."
    ),
    name="logout",
)
async def logout(
    payload: LogoutRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
    auth_service: AuthServiceDep,
) -> Response:
    await auth_service.logout(
        session,
        principal=principal,
        refresh_token=payload.refresh_token,
        all_sessions=payload.all_sessions,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/sessions",
    response_model=list[SessionSummary],
    summary="List the caller's active sessions",
    description="Returns active, unexpired sessions newest first, flagging the current one.",
    name="list_sessions",
)
async def list_sessions(
    principal: CurrentPrincipal,
    session: SessionDep,
    auth_service: AuthServiceDep,
) -> list[SessionSummary]:
    return await auth_service.list_sessions(session, principal=principal)
