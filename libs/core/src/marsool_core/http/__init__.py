"""Shared HTTP plumbing: app factory, middleware, error handling, dependencies."""

from marsool_core.http.app import ServiceApp, create_app
from marsool_core.http.deps import (
    AuthGuards,
    Pagination,
    PaginationParams,
    build_principal_dependency,
    pagination_params,
)
from marsool_core.http.pagination import decode_cursor, encode_cursor
from marsool_core.http.schemas import (
    ErrorDetail,
    ErrorEnvelope,
    HealthStatus,
    Page,
    PageMeta,
)

__all__ = [
    "AuthGuards",
    "ErrorDetail",
    "ErrorEnvelope",
    "HealthStatus",
    "Page",
    "PageMeta",
    "Pagination",
    "PaginationParams",
    "ServiceApp",
    "build_principal_dependency",
    "create_app",
    "decode_cursor",
    "encode_cursor",
    "pagination_params",
]
