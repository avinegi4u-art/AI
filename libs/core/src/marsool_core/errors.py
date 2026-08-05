"""Platform error taxonomy.

Every service raises subclasses of :class:`AppError`; the HTTP layer renders them
into the single platform-wide error envelope (see ``schemas.ErrorEnvelope``) so that
clients can branch on a stable machine-readable ``code`` instead of parsing prose.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "AppError",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "DependencyUnavailableError",
    "InternalError",
    "NotFoundError",
    "PermissionDeniedError",
    "PreconditionFailedError",
    "RateLimitedError",
    "ValidationFailedError",
]


class AppError(Exception):
    """Base class for expected, client-communicable failures.

    Attributes:
        code: Stable machine-readable identifier, e.g. ``"order_not_found"``.
        message: Human-readable description safe to return to clients.
        status_code: HTTP status the API layer should use.
        details: Structured context (field errors, limits, retry hints).
    """

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ) -> None:
        self.message = message or self.__class__.__doc__ or "Unexpected error"
        self.code = code or self.__class__.code
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code
        super().__init__(self.message)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class BadRequestError(AppError):
    """The request was malformed or semantically invalid."""

    status_code = 400
    code = "bad_request"


class ValidationFailedError(BadRequestError):
    """One or more request fields failed validation."""

    status_code = 422
    code = "validation_failed"


class AuthenticationError(AppError):
    """Authentication credentials are missing, expired or invalid."""

    status_code = 401
    code = "unauthenticated"


class PermissionDeniedError(AppError):
    """The caller is authenticated but not allowed to perform this action."""

    status_code = 403
    code = "permission_denied"


class NotFoundError(AppError):
    """The requested resource does not exist."""

    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    """The request conflicts with the current state of the resource."""

    status_code = 409
    code = "conflict"


class PreconditionFailedError(AppError):
    """A required precondition for this operation is not satisfied."""

    status_code = 412
    code = "precondition_failed"


class RateLimitedError(AppError):
    """Too many requests; the caller should back off and retry later."""

    status_code = 429
    code = "rate_limited"

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int | None = None,
        **kwargs: Any,
    ) -> None:
        details = dict(kwargs.pop("details", None) or {})
        if retry_after_seconds is not None:
            details["retry_after_seconds"] = retry_after_seconds
        super().__init__(message, details=details, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class DependencyUnavailableError(AppError):
    """A downstream dependency is unavailable; the request may be retried."""

    status_code = 503
    code = "dependency_unavailable"


class InternalError(AppError):
    """An unexpected internal error occurred."""

    status_code = 500
    code = "internal_error"
