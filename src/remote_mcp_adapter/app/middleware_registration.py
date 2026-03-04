"""HTTP middleware registration helpers for the adapter app."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastmcp.exceptions import ToolError

from ..constants import GLOBAL_SERVER_ID, MCP_SESSION_ID_HEADER, UNKNOWN_SERVER_ID
from ..core import PersistenceUnavailableError
from ..proxy.cancellation import ProxySessionContext, parse_mcp_envelope
from .auth_helpers import (
    is_oauth_discovery_path,
    is_public_unprotected_path,
    parse_artifact_download_path,
    record_auth_rejection,
    route_group_for_metrics,
)
from .health_policy_helpers import apply_runtime_failure_policy_if_persistent_backend
from .http_contexts import MiddlewareRegistrationContext
from .persistence_http_helpers import persistence_backend_operation_failed_response
from .runtime_request_helpers import is_stateful_request_path, resolve_server_id_for_path, validate_adapter_auth

logger = logging.getLogger(__name__)

CallNext = Callable[[Request], Awaitable[Any]]


def _telemetry_server_id_for_path(
    *,
    path: str,
    mount_path_to_server_id: dict[str, str],
    upload_path_prefix: str,
    known_server_ids: set[str],
) -> str:
    """Resolve telemetry server id from path with global fallback.

    Args:
        path: Request URL path.
        mount_path_to_server_id: Reverse map from mount path to server id.
        upload_path_prefix: Upload route prefix.
        known_server_ids: Set of configured/known upstream server identifiers.

    Returns:
        Server id for the route scope, or ``global`` when not server-scoped.
    """
    mount_server_id = resolve_server_id_for_path(path, mount_path_to_server_id)
    if mount_server_id:
        return mount_server_id if mount_server_id in known_server_ids else UNKNOWN_SERVER_ID

    artifact_path = parse_artifact_download_path(path)
    if artifact_path is not None:
        artifact_server_id = artifact_path[0]
        return artifact_server_id if artifact_server_id in known_server_ids else UNKNOWN_SERVER_ID

    if path.startswith(upload_path_prefix):
        relative_path = path[len(upload_path_prefix) :].lstrip("/")
        upload_server_id = relative_path.split("/", 1)[0] if relative_path else ""
        if upload_server_id:
            return upload_server_id if upload_server_id in known_server_ids else UNKNOWN_SERVER_ID
    return GLOBAL_SERVER_ID


def _known_server_ids(context: MiddlewareRegistrationContext) -> set[str]:
    """Return known upstream server identifiers for telemetry normalization.

    Args:
        context: Shared middleware registration dependencies.

    Returns:
        Set of configured server identifiers.
    """
    return set(context.mount_path_to_server_id.values()) | set(context.upstream_health.keys())


async def _record_request_rejection_metrics(
    *,
    context: MiddlewareRegistrationContext,
    request: Request,
    reason: str,
    status_code: int,
    server_id: str | None = None,
) -> None:
    """Emit non-auth rejection telemetry when enabled.

    Args:
        context: Shared middleware registration dependencies.
        request: Incoming request.
        reason: Rejection reason label.
        status_code: Returned HTTP status code.
        server_id: Optional explicit server id.
    """
    telemetry = context.telemetry
    if telemetry is None or not getattr(telemetry, "enabled", False):
        return
    resolved_server_id = server_id or _telemetry_server_id_for_path(
        path=request.url.path,
        mount_path_to_server_id=context.mount_path_to_server_id,
        upload_path_prefix=context.upload_path_prefix,
        known_server_ids=_known_server_ids(context),
    )
    route_group = route_group_for_metrics(request.url.path, upload_path_prefix=context.upload_path_prefix)
    await telemetry.record_request_rejection(
        server_id=resolved_server_id,
        route_group=route_group,
        reason=reason,
        status_code=status_code,
    )


async def _rejection_json_response(
    *,
    context: MiddlewareRegistrationContext,
    request: Request,
    reason: str,
    status_code: int,
    content: dict[str, Any],
    server_id: str | None = None,
) -> JSONResponse:
    """Record rejection metrics and build an HTTP JSON response.

    Args:
        context: Shared middleware registration dependencies.
        request: Incoming request.
        reason: Rejection reason label.
        status_code: Returned HTTP status code.
        content: JSON response body.
        server_id: Optional explicit server id.

    Returns:
        JSON response for rejected request.
    """
    await _record_request_rejection_metrics(
        context=context,
        request=request,
        reason=reason,
        status_code=status_code,
        server_id=server_id,
    )
    return JSONResponse(status_code=status_code, content=content)


async def _auth_rejection_json_response(
    *,
    context: MiddlewareRegistrationContext,
    route_group: str,
    reason: str,
    server_id: str,
    status_code: int,
    detail: Any,
) -> JSONResponse:
    """Record auth-rejection telemetry and return response.

    Args:
        context: Shared middleware registration dependencies.
        route_group: Route-group telemetry label.
        reason: Rejection reason label.
        server_id: Telemetry server id.
        status_code: Returned HTTP status code.
        detail: Response detail payload.

    Returns:
        Auth rejection response object.
    """
    await record_auth_rejection(
        telemetry=context.telemetry,
        route_group=route_group,
        reason=reason,
        server_id=server_id,
    )
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _authorize_signed_artifact_download(*, context: MiddlewareRegistrationContext, request: Request) -> bool:
    """Validate signed artifact download credential when configured.

    Args:
        context: Shared middleware registration dependencies.
        request: Incoming request.

    Returns:
        True when signed artifact credential is valid and accepted.
    """
    artifact_path = parse_artifact_download_path(request.url.path)
    if artifact_path is None or request.method.upper() != "GET":
        return False
    if not context.resolved_config.core.auth.enabled:
        return False
    if context.artifact_download_credentials is None or not context.artifact_download_credentials.enabled:
        return False

    server_id, session_id, artifact_id, filename = artifact_path
    credential_valid = context.artifact_download_credentials.validate(
        server_id=server_id,
        session_id=session_id,
        artifact_id=artifact_id,
        filename=filename,
        query_params=request.query_params,
    )
    if not credential_valid:
        return False
    request.state.artifact_download_signed_auth = True
    return True


async def _evaluate_signed_upload_auth(
    *,
    context: MiddlewareRegistrationContext,
    request: Request,
    route_group: str,
    telemetry_server_id: str,
) -> tuple[bool, JSONResponse | None]:
    """Evaluate signed-upload auth path and return authorization outcome.

    Args:
        context: Shared middleware registration dependencies.
        request: Incoming request.
        route_group: Route-group telemetry label.
        telemetry_server_id: Telemetry server id.

    Returns:
        Tuple ``(authorized, rejection_response)``:
        - ``(True, None)`` when signed credential is valid.
        - ``(False, response)`` when upload path requires signed auth but is rejected.
        - ``(False, None)`` when signed-upload auth does not apply for this request.
    """
    is_upload_path = request.url.path.startswith(context.upload_path_prefix)
    if not is_upload_path:
        return False, None
    if not context.resolved_config.core.auth.enabled:
        return False, None
    if context.upload_credentials is None or not context.upload_credentials.enabled:
        return False, None

    relative_path = request.url.path[len(context.upload_path_prefix) :].lstrip("/")
    upload_server_id = relative_path.split("/", 1)[0] if relative_path else ""
    session_id = request.headers.get(MCP_SESSION_ID_HEADER)
    credential_valid = False

    if upload_server_id and session_id:
        try:
            credential_valid = await context.upload_credentials.validate_and_consume(
                server_id=upload_server_id,
                session_id=session_id,
                query_params=request.query_params,
            )
        except Exception:
            logger.exception(
                "Signed upload credential validation failed",
                extra={"server_id": upload_server_id, "session_id": session_id},
            )
            response = await _auth_rejection_json_response(
                context=context,
                route_group=route_group,
                reason="upload_credential_validation_backend_unavailable",
                server_id=telemetry_server_id,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Upload credential validation backend unavailable.",
            )
            return False, response

    if upload_server_id and session_id and credential_valid:
        return True, None

    response = await _auth_rejection_json_response(
        context=context,
        route_group=route_group,
        reason="missing_or_invalid_signed_upload_credential",
        server_id=telemetry_server_id,
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: missing or invalid signed upload credential.",
    )
    return False, response


async def _reject_in_flight_for_fail_closed(
    *,
    context: MiddlewareRegistrationContext,
    request: Request,
    matched_server_id: str | None,
) -> JSONResponse | None:
    """Reject mounted-route requests under fail-closed policy.

    Args:
        context: Shared middleware registration dependencies.
        request: Incoming request.
        matched_server_id: Server id resolved from mounted path, if any.

    Returns:
        Rejection response when blocked, else None.
    """
    if not context.persistence_policy.should_reject_stateful_requests():
        return None
    if matched_server_id is None:
        return None
    return await _rejection_json_response(
        context=context,
        request=request,
        reason="persistence_fail_closed",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "Persistence backend unavailable under fail-closed policy."},
        server_id=matched_server_id,
    )


async def _reject_in_flight_for_breaker(
    *,
    context: MiddlewareRegistrationContext,
    request: Request,
    matched_server_id: str | None,
) -> JSONResponse | None:
    """Reject mounted-route requests blocked by upstream breaker.

    Args:
        context: Shared middleware registration dependencies.
        request: Incoming request.
        matched_server_id: Server id resolved from mounted path, if any.

    Returns:
        Rejection response when blocked, else None.
    """
    if matched_server_id is None:
        return None
    monitor = context.upstream_health.get(matched_server_id)
    if monitor is None:
        return None
    allowed, detail = await monitor.allow_proxy_request()
    if allowed:
        return None
    return await _rejection_json_response(
        context=context,
        request=request,
        reason="upstream_blocked_by_breaker",
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": detail, "server_id": matched_server_id},
        server_id=matched_server_id,
    )


async def _begin_in_flight_or_reject(
    *,
    context: MiddlewareRegistrationContext,
    request: Request,
    matched_server_id: str,
    session_id: str,
) -> JSONResponse | None:
    """Start in-flight tracking or return rejection response on failure.

    Args:
        context: Shared middleware registration dependencies.
        request: Incoming request.
        matched_server_id: Server id resolved from mounted path.
        session_id: MCP session id from request header.

    Returns:
        Rejection response on failure, otherwise None when tracking started.

    Raises:
        Exception: Re-raises unexpected errors not handled by runtime policy.
    """
    try:
        await context.session_store.begin_in_flight(matched_server_id, session_id)
    except PersistenceUnavailableError as exc:
        return await _rejection_json_response(
            context=context,
            request=request,
            reason="in_flight_persistence_unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)},
            server_id=matched_server_id,
        )
    except ToolError as exc:
        return await _rejection_json_response(
            context=context,
            request=request,
            reason="in_flight_limit_exceeded",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": str(exc)},
            server_id=matched_server_id,
        )
    except Exception as exc:
        runtime_switched = await apply_runtime_failure_policy_if_persistent_backend(
            resolved_config=context.resolved_config,
            persistence_policy=context.persistence_policy,
            runtime_ref=context.runtime_ref,
            session_store=context.session_store,
            app=context.app,
            component="track_in_flight",
            error=str(exc),
            build_memory_persistence_runtime=context.build_memory_persistence_runtime,
        )
        if runtime_switched:
            await _record_request_rejection_metrics(
                context=context,
                request=request,
                reason="in_flight_runtime_failure_policy",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                server_id=matched_server_id,
            )
            return persistence_backend_operation_failed_response()
        raise
    return None


async def _end_in_flight_best_effort(
    *,
    context: MiddlewareRegistrationContext,
    matched_server_id: str,
    session_id: str,
) -> None:
    """End in-flight tracking with logging-only failure handling.

    Args:
        context: Shared middleware registration dependencies.
        matched_server_id: Server id resolved from mounted path.
        session_id: MCP session id.
    """
    try:
        await context.session_store.end_in_flight(matched_server_id, session_id)
    except Exception:
        logger.exception(
            "Failed to decrement in-flight counter",
            extra={"server_id": matched_server_id, "session_id": session_id},
        )


async def _track_in_flight_request(
    *,
    context: MiddlewareRegistrationContext,
    request: Request,
    call_next: CallNext,
) -> Any:
    """Process in-flight middleware logic for one request.

    Args:
        context: Shared middleware registration dependencies.
        request: Incoming request.
        call_next: ASGI continuation callable.

    Returns:
        Next middleware response or an early rejection response.
    """
    session_id = request.headers.get(MCP_SESSION_ID_HEADER)
    matched_server_id = resolve_server_id_for_path(request.url.path, context.mount_path_to_server_id)

    fail_closed_response = await _reject_in_flight_for_fail_closed(
        context=context,
        request=request,
        matched_server_id=matched_server_id,
    )
    if fail_closed_response is not None:
        return fail_closed_response

    breaker_response = await _reject_in_flight_for_breaker(
        context=context,
        request=request,
        matched_server_id=matched_server_id,
    )
    if breaker_response is not None:
        return breaker_response

    if not session_id or matched_server_id is None:
        return await call_next(request)

    begin_failure_response = await _begin_in_flight_or_reject(
        context=context,
        request=request,
        matched_server_id=matched_server_id,
        session_id=session_id,
    )
    if begin_failure_response is not None:
        return begin_failure_response

    try:
        return await call_next(request)
    finally:
        await _end_in_flight_best_effort(
            context=context,
            matched_server_id=matched_server_id,
            session_id=session_id,
        )


async def _process_auth_request(
    *,
    context: MiddlewareRegistrationContext,
    request: Request,
    call_next: CallNext,
) -> Any:
    """Process auth middleware logic for one request.

    Args:
        context: Shared middleware registration dependencies.
        request: Incoming request.
        call_next: ASGI continuation callable.

    Returns:
        Next middleware response or an early auth rejection response.
    """
    route_group = route_group_for_metrics(request.url.path, upload_path_prefix=context.upload_path_prefix)
    telemetry_server_id = _telemetry_server_id_for_path(
        path=request.url.path,
        mount_path_to_server_id=context.mount_path_to_server_id,
        upload_path_prefix=context.upload_path_prefix,
        known_server_ids=_known_server_ids(context),
    )

    if is_public_unprotected_path(request.url.path):
        return await call_next(request)
    if is_oauth_discovery_path(request.url.path):
        return await call_next(request)

    if _authorize_signed_artifact_download(context=context, request=request):
        return await call_next(request)

    signed_upload_authorized, signed_upload_rejection = await _evaluate_signed_upload_auth(
        context=context,
        request=request,
        route_group=route_group,
        telemetry_server_id=telemetry_server_id,
    )
    if signed_upload_authorized:
        return await call_next(request)
    if signed_upload_rejection is not None:
        return signed_upload_rejection

    try:
        validate_adapter_auth(request, context.resolved_config)
    except HTTPException as exc:
        return await _auth_rejection_json_response(
            context=context,
            route_group=route_group,
            reason="invalid_adapter_auth_token",
            server_id=telemetry_server_id,
            status_code=exc.status_code,
            detail=exc.detail,
        )
    return await call_next(request)


def _register_request_telemetry_middleware(*, context: MiddlewareRegistrationContext) -> None:
    """Register request latency/count telemetry middleware.

    Args:
        context: Shared middleware registration dependencies.
    """

    @context.app.middleware("http")
    async def request_telemetry(request: Request, call_next):
        """Capture adapter HTTP request count and latency metrics.

        Args:
            request: Incoming FastAPI request.
            call_next: ASGI call chain continuation.
        """
        if context.telemetry is None or not getattr(context.telemetry, "enabled", False):
            return await call_next(request)

        started = time.perf_counter()
        server_id = _telemetry_server_id_for_path(
            path=request.url.path,
            mount_path_to_server_id=context.mount_path_to_server_id,
            upload_path_prefix=context.upload_path_prefix,
            known_server_ids=_known_server_ids(context),
        )
        route_group = route_group_for_metrics(request.url.path, upload_path_prefix=context.upload_path_prefix)
        try:
            response = await call_next(request)
        except Exception:
            duration_seconds = time.perf_counter() - started
            await context.telemetry.record_http_request(
                method=request.method,
                route_group=route_group,
                status_code=500,
                duration_seconds=duration_seconds,
                server_id=server_id,
            )
            raise

        duration_seconds = time.perf_counter() - started
        await context.telemetry.record_http_request(
            method=request.method,
            route_group=route_group,
            status_code=response.status_code,
            duration_seconds=duration_seconds,
            server_id=server_id,
        )
        return response


def _register_cancellation_middleware(*, context: MiddlewareRegistrationContext) -> None:
    """Register in-band cancellation observer middleware.

    Args:
        context: Shared middleware registration dependencies.
    """

    @context.app.middleware("http")
    async def observe_cancellation_notifications(request: Request, call_next):
        """Route in-band cancellation JSON bodies to the cancellation bus.

        Args:
            request: Incoming FastAPI request.
            call_next: ASGI call chain continuation.
        """
        server_id = resolve_server_id_for_path(request.url.path, context.mount_path_to_server_id)
        if request.method.upper() != "POST" or server_id is None:
            return await call_next(request)

        envelope = parse_mcp_envelope(await request.body())
        session_id = request.headers.get(MCP_SESSION_ID_HEADER)
        if not session_id:
            if envelope.cancellations:
                logger.warning(
                    "Cancellation notification ignored without Mcp-Session-Id header",
                    extra={"server_id": server_id},
                )
            return await call_next(request)

        session_context = ProxySessionContext(server_id=server_id, session_id=session_id)
        await context.cancellation_observer.register_requests(session_context, envelope.requests)
        await context.cancellation_observer.observe_cancellations(session_context, envelope.cancellations)
        try:
            return await call_next(request)
        finally:
            await context.cancellation_observer.complete_requests(session_context, envelope.requests)


def _register_persistence_fail_closed_middleware(*, context: MiddlewareRegistrationContext) -> None:
    """Register fail-closed guard middleware for stateful paths.

    Args:
        context: Shared middleware registration dependencies.
    """

    @context.app.middleware("http")
    async def persistence_fail_closed_guard(request: Request, call_next):
        """Block stateful requests when persistence is unavailable.

        Args:
            request: Incoming FastAPI request.
            call_next: ASGI call chain continuation.
        """
        if not context.persistence_policy.should_reject_stateful_requests():
            return await call_next(request)
        if not is_stateful_request_path(
            path=request.url.path,
            mount_path_to_server_id=context.mount_path_to_server_id,
            upload_path_prefix=context.upload_path_prefix,
        ):
            return await call_next(request)
        return await _rejection_json_response(
            context=context,
            request=request,
            reason="persistence_fail_closed",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": (
                    "Persistence backend is unavailable and unavailable_policy='fail_closed' is active. "
                    "Stateful operations are temporarily disabled."
                )
            },
        )


def _register_in_flight_middleware(*, context: MiddlewareRegistrationContext) -> None:
    """Register in-flight request tracking middleware.

    Args:
        context: Shared middleware registration dependencies.
    """

    @context.app.middleware("http")
    async def track_in_flight(request: Request, call_next):
        """Increment and decrement in-flight counter around each request.

        Args:
            request: Incoming FastAPI request.
            call_next: ASGI call chain continuation.
        """
        return await _track_in_flight_request(context=context, request=request, call_next=call_next)


def _register_auth_middleware(*, context: MiddlewareRegistrationContext) -> None:
    """Register adapter auth enforcement middleware.

    Args:
        context: Shared middleware registration dependencies.
    """

    @context.app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        """Enforce adapter-level auth before forwarding request.

        Args:
            request: Incoming FastAPI request.
            call_next: ASGI call chain continuation.
        """
        return await _process_auth_request(context=context, request=request, call_next=call_next)


def register_middleware_stack(*, context: MiddlewareRegistrationContext) -> None:
    """Register all middleware layers in deterministic order.

    Args:
        context: Shared middleware registration dependencies.
    """
    _register_request_telemetry_middleware(context=context)
    _register_cancellation_middleware(context=context)
    _register_persistence_fail_closed_middleware(context=context)
    _register_in_flight_middleware(context=context)
    _register_auth_middleware(context=context)
