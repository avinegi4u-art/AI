"""Marsool shared platform library.

Every service depends on this package for cross-cutting concerns so that behaviour is
consistent platform-wide and cannot drift service by service:

- :mod:`marsool_core.config` — environment-driven settings
- :mod:`marsool_core.logging` / :mod:`marsool_core.context` — structured logs, correlation IDs
- :mod:`marsool_core.errors` — error taxonomy rendered into one HTTP envelope
- :mod:`marsool_core.http` — app factory, middleware, auth guards, pagination
- :mod:`marsool_core.db` — declarative base, async sessions, custom column types
- :mod:`marsool_core.events` — event envelope, topic registry, bus, transactional outbox
- :mod:`marsool_core.security` — JWT issuance/verification, hashing, principals
- :mod:`marsool_core.cache` — Redis cache, rate limiting, idempotency
- :mod:`marsool_core.geo`, :mod:`marsool_core.money`, :mod:`marsool_core.phone` — domain primitives
"""

from marsool_core.config import Environment, ServiceSettings
from marsool_core.errors import (
    AppError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    DependencyUnavailableError,
    NotFoundError,
    PermissionDeniedError,
    PreconditionFailedError,
    RateLimitedError,
    ValidationFailedError,
)
from marsool_core.ids import new_id, new_ulid
from marsool_core.logging import configure_logging, get_logger
from marsool_core.security.principal import Principal, Role

__version__ = "0.1.0"

__all__ = [
    "AppError",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "DependencyUnavailableError",
    "Environment",
    "NotFoundError",
    "PermissionDeniedError",
    "PreconditionFailedError",
    "Principal",
    "RateLimitedError",
    "Role",
    "ServiceSettings",
    "ValidationFailedError",
    "__version__",
    "configure_logging",
    "get_logger",
    "new_id",
    "new_ulid",
]
