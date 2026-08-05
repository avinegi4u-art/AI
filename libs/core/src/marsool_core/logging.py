"""Structured logging.

All services emit single-line JSON logs enriched with the ambient correlation ID, so
that a request can be followed across the gateway and every downstream service with
one log query.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from marsool_core.context import current_context

# Third-party loggers that are noisy at INFO and duplicate our own access logs.
_QUIET_LOGGERS = ("uvicorn.access", "aiokafka.consumer.group_coordinator", "httpx")

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "otp",
        "otp_code",
        "code",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "secret",
        "jwt_secret",
        "card_number",
        "cvv",
    }
)
_REDACTED = "[redacted]"


def _add_request_context(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Merge correlation/request/actor identifiers into every log line."""
    for key, value in current_context().as_log_fields().items():
        event_dict.setdefault(key, value)
    return event_dict


def _redact_sensitive(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Replace values of known-sensitive keys so secrets never reach the log sink."""
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS and event_dict[key] is not None:
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(
    *,
    service_name: str,
    level: str = "INFO",
    log_format: str = "json",
) -> None:
    """Configure structlog and route stdlib logging through it.

    Idempotent: calling it twice (app startup plus a worker entrypoint) is safe.
    """
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_request_context,
        _redact_sensitive,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.add_logger_name,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for logger_name in _QUIET_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    structlog.contextvars.bind_contextvars(service=service_name)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    return structlog.stdlib.get_logger(name)
