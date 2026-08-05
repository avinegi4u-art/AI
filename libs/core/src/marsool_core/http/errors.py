"""Exception handlers that render every failure as an :class:`ErrorEnvelope`."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from marsool_core.context import CORRELATION_ID_HEADER, get_correlation_id
from marsool_core.errors import AppError, RateLimitedError
from marsool_core.http.schemas import ErrorDetail, ErrorEnvelope
from marsool_core.logging import get_logger

logger = get_logger(__name__)

# Status codes reused for well-known Starlette HTTPExceptions so clients see the same
# vocabulary regardless of which layer rejected the request.
_HTTP_STATUS_CODES = {
    status.HTTP_401_UNAUTHORIZED: "unauthenticated",
    status.HTTP_403_FORBIDDEN: "permission_denied",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
}


def _envelope(
    *,
    code: str,
    message: str,
    details: dict[str, object] | None = None,
    field_errors: list[ErrorDetail] | None = None,
) -> dict[str, object]:
    return ErrorEnvelope(
        error=ErrorEnvelope.Error(
            code=code,
            message=message,
            details=dict(details or {}),
            field_errors=field_errors or [],
            correlation_id=get_correlation_id(),
        )
    ).model_dump()


def _response(
    status_code: int,
    body: dict[str, object],
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    merged = dict(headers or {})
    correlation_id = get_correlation_id()
    if correlation_id:
        merged[CORRELATION_ID_HEADER] = correlation_id
    return JSONResponse(status_code=status_code, content=body, headers=merged)


def _field_errors_from_validation(
    exc: RequestValidationError | ValidationError,
) -> list[ErrorDetail]:
    details: list[ErrorDetail] = []
    for error in exc.errors():
        # Drop the leading location segment ("body"/"query"/"path") for readability.
        location = [str(part) for part in error["loc"]]
        if location and location[0] in {"body", "query", "path", "header", "cookie"}:
            location = location[1:]
        details.append(
            ErrorDetail(
                field=".".join(location) or "__root__",
                message=error["msg"],
                code=error["type"],
            )
        )
    return details


def install_exception_handlers(app: FastAPI, *, include_debug_detail: bool = False) -> None:
    """Register the platform's exception handlers on ``app``.

    Args:
        app: The FastAPI application.
        include_debug_detail: When True (non-production), unexpected exception messages
            are included in the response to speed up local debugging.
    """

    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimitedError) and exc.retry_after_seconds is not None:
            headers["Retry-After"] = str(exc.retry_after_seconds)
        if exc.status_code >= 500:
            logger.error("app_error", code=exc.code, message=exc.message, details=exc.details)
        else:
            logger.info("app_error", code=exc.code, message=exc.message, details=exc.details)
        return _response(
            exc.status_code,
            _envelope(code=exc.code, message=exc.message, details=exc.details),
            headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            _envelope(
                code="validation_failed",
                message="Request validation failed",
                field_errors=_field_errors_from_validation(exc),
            ),
        )

    @app.exception_handler(ValidationError)
    async def _handle_pydantic_validation(_request: Request, exc: ValidationError) -> JSONResponse:
        # A ValidationError escaping a handler means we built an invalid response model.
        logger.error("response_validation_failed", errors=exc.errors())
        return _response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            _envelope(code="internal_error", message="Failed to serialise response"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _HTTP_STATUS_CODES.get(exc.status_code, "http_error")
        return _response(
            exc.status_code,
            _envelope(code=code, message=str(exc.detail)),
            dict(exc.headers or {}),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", error_type=type(exc).__name__)
        message = (
            f"{type(exc).__name__}: {exc}"
            if include_debug_detail
            else "An unexpected error occurred"
        )
        return _response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            _envelope(code="internal_error", message=message),
        )
