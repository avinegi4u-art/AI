"""Request-scoped ambient context.

Correlation identifiers and the authenticated principal are carried in context
variables so that logging, event publishing and outbound HTTP calls can pick them up
without threading them through every function signature.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

CORRELATION_ID_HEADER = "X-Correlation-Id"
REQUEST_ID_HEADER = "X-Request-Id"

_correlation_id: ContextVar[str | None] = ContextVar("marsool_correlation_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("marsool_request_id", default=None)
_actor_id: ContextVar[str | None] = ContextVar("marsool_actor_id", default=None)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Snapshot of the ambient request context."""

    correlation_id: str | None
    request_id: str | None
    actor_id: str | None

    def as_log_fields(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("correlation_id", self.correlation_id),
                ("request_id", self.request_id),
                ("actor_id", self.actor_id),
            )
            if value is not None
        }


def current_context() -> RequestContext:
    """Return the ambient request context."""
    return RequestContext(
        correlation_id=_correlation_id.get(),
        request_id=_request_id.get(),
        actor_id=_actor_id.get(),
    )


def get_correlation_id() -> str | None:
    return _correlation_id.get()


@contextmanager
def bind_context(
    *,
    correlation_id: str | None = None,
    request_id: str | None = None,
    actor_id: str | None = None,
) -> Iterator[RequestContext]:
    """Bind context values for the duration of the block, restoring them on exit."""
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    for variable, value in (
        (_correlation_id, correlation_id),
        (_request_id, request_id),
        (_actor_id, actor_id),
    ):
        if value is not None:
            tokens.append((variable, variable.set(value)))
    try:
        yield current_context()
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def set_actor_id(actor_id: str | None) -> None:
    """Attach the authenticated principal to the ambient context."""
    _actor_id.set(actor_id)
