"""Shared HTTP responses/exceptions for persistence-related failures."""

from __future__ import annotations

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

_PERSISTENCE_BACKEND_OPERATION_FAILED_DETAIL = "Persistence backend operation failed."


def persistence_backend_operation_failed_response() -> JSONResponse:
    """Return canonical JSON response for persistence backend operation failure.

    Returns:
        ``JSONResponse`` with HTTP 503 status and a descriptive detail body.
    """
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": _PERSISTENCE_BACKEND_OPERATION_FAILED_DETAIL},
    )


def raise_persistence_backend_operation_failed_http_exception(*, cause: Exception) -> None:
    """Raise canonical HTTPException for persistence backend operation failure.

    Args:
        cause: Original exception to chain.

    Raises:
        HTTPException: Always raised with HTTP 503 status.
    """
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=_PERSISTENCE_BACKEND_OPERATION_FAILED_DETAIL,
    ) from cause
