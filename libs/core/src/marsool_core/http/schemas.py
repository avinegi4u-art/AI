"""Response schemas shared by every service.

One error shape and one pagination shape across the platform means client SDKs, the
gateway and the AI agents all parse a single contract.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

ItemT = TypeVar("ItemT")


class ErrorDetail(BaseModel):
    """A single field-level validation problem."""

    field: str = Field(description="Dotted path to the offending field, e.g. 'items.0.quantity'")
    message: str = Field(description="Human-readable explanation")
    code: str | None = Field(default=None, description="Machine-readable validation code")


class ErrorEnvelope(BaseModel):
    """The platform-wide error response body.

    Example:
        ```json
        {
          "error": {
            "code": "merchant_closed",
            "message": "Merchant is not accepting orders right now",
            "details": {"opens_at": "11:00"},
            "field_errors": [],
            "correlation_id": "01J9Z..."
          }
        }
        ```
    """

    model_config = ConfigDict(json_schema_extra={"examples": [
        {
            "error": {
                "code": "not_found",
                "message": "Merchant not found",
                "details": {},
                "field_errors": [],
                "correlation_id": "01J9Z8XQF3K7M2P4R6T8V0W1Y3",
            }
        }
    ]})

    class Error(BaseModel):
        code: str = Field(description="Stable machine-readable error identifier")
        message: str = Field(description="Human-readable description")
        details: dict[str, Any] = Field(default_factory=dict, description="Structured context")
        field_errors: list[ErrorDetail] = Field(default_factory=list)
        correlation_id: str | None = Field(
            default=None, description="Echoes X-Correlation-Id for support and tracing"
        )

    error: Error


class PageMeta(BaseModel):
    """Cursor pagination metadata.

    Cursor-based rather than offset-based: offsets drift when rows are inserted
    concurrently, and ``OFFSET n`` degrades linearly on large tables.
    """

    limit: int = Field(description="Maximum number of items requested")
    next_cursor: str | None = Field(
        default=None, description="Opaque cursor for the next page; null when exhausted"
    )
    has_more: bool = Field(description="Whether more items exist after this page")
    total: int | None = Field(
        default=None, description="Total matching items, when cheap to compute"
    )


class Page(BaseModel, Generic[ItemT]):
    """A page of results."""

    items: list[ItemT]
    meta: PageMeta

    @classmethod
    def of(
        cls,
        items: list[ItemT],
        *,
        limit: int,
        next_cursor: str | None = None,
        total: int | None = None,
    ) -> Page[ItemT]:
        return cls(
            items=items,
            meta=PageMeta(
                limit=limit,
                next_cursor=next_cursor,
                has_more=next_cursor is not None,
                total=total,
            ),
        )


class HealthStatus(BaseModel):
    """Health and readiness probe response."""

    status: str = Field(description="'ok' or 'degraded'")
    service: str
    version: str
    environment: str
    checks: dict[str, str] = Field(
        default_factory=dict, description="Per-dependency status, e.g. {'database': 'ok'}"
    )
