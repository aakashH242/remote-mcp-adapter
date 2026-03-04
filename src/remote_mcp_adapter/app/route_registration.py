"""HTTP route registration helpers for the adapter app."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from fastmcp.exceptions import ToolError

from ..constants import MCP_SESSION_ID_HEADER, UNKNOWN_SERVER_ID
from ..core import PersistenceUnavailableError
from ..core.storage.artifact_access import (
    ArtifactFileMissingError,
    ArtifactFilenameMismatchError,
    ArtifactNotFoundError,
    resolve_artifact_for_read,
)
from ..proxy.upload_helpers import build_artifact_download_path
from .health_policy_helpers import (
    apply_runtime_failure_policy_if_persistent_backend,
    build_healthz_payload,
)
from .http_contexts import RouteRegistrationContext
from .persistence_http_helpers import raise_persistence_backend_operation_failed_http_exception
from .runtime import collect_upstream_health_checks
from .upload_route_helpers import (
    build_upload_response_payload,
    close_uploaded_files,
    normalize_sha256_inputs,
    raise_fail_closed_if_rejecting,
    raise_unknown_server_if_missing,
    record_upload_batch_metrics,
    record_upload_failure_metrics,
    rollback_successful_uploads,
    save_one_uploaded_file,
    validate_sha256_count,
)

logger = logging.getLogger(__name__)


async def _raise_upload_http_exception_for_failure(
    *,
    error: Exception,
    records,
    context: RouteRegistrationContext,
    server_id: str,
    session_id: str,
) -> None:
    """Rollback successful uploads and raise the canonical HTTP error for one failure.

    Args:
        error: Exception that triggered the upload failure.
        records: Upload records persisted before the failure.
        context: Shared route registration dependencies.
        server_id: Identifier of the upstream server.
        session_id: Current MCP session identifier.

    Raises:
        HTTPException: With the appropriate status code for the failure type.
    """
    if records:
        await rollback_successful_uploads(
            session_store=context.session_store,
            server_id=server_id,
            session_id=session_id,
            records=records,
        )
    if isinstance(error, PersistenceUnavailableError):
        await record_upload_failure_metrics(
            telemetry=context.telemetry,
            server_id=server_id,
            reason="persistence_unavailable",
        )
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    if not isinstance(error, ToolError) and await apply_runtime_failure_policy_if_persistent_backend(
        resolved_config=context.resolved_config,
        persistence_policy=context.persistence_policy,
        runtime_ref=context.runtime_ref,
        session_store=context.session_store,
        app=context.app,
        component="upload_endpoint",
        error=str(error),
        build_memory_persistence_runtime=context.build_memory_persistence_runtime,
    ):
        await record_upload_failure_metrics(
            telemetry=context.telemetry,
            server_id=server_id,
            reason="persistence_backend_failure",
        )
        raise_persistence_backend_operation_failed_http_exception(cause=error)
    if isinstance(error, ToolError):
        reason = "tool_error"
    else:
        reason = "upload_processing_error"
    await record_upload_failure_metrics(
        telemetry=context.telemetry,
        server_id=server_id,
        reason=reason,
    )
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error


async def _record_artifact_download_metrics(
    *,
    context: RouteRegistrationContext,
    server_id: str,
    result: str,
    auth_mode: str,
    started_at: float,
    size_bytes: int = 0,
) -> None:
    """Emit artifact download telemetry when enabled.

    Args:
        context: Shared route registration dependencies.
        server_id: Server identifier.
        result: Download outcome label.
        auth_mode: Access mode label.
        started_at: ``perf_counter`` value when handling began.
        size_bytes: Served bytes for successful downloads.
    """
    telemetry = context.telemetry
    if telemetry is None or not getattr(telemetry, "enabled", False):
        return
    await telemetry.record_artifact_download(
        server_id=server_id,
        result=result,
        auth_mode=auth_mode,
        duration_seconds=max(0.0, time.perf_counter() - started_at),
        size_bytes=max(0, int(size_bytes)),
    )


async def _raise_upload_validation_http_exception(
    *,
    context: RouteRegistrationContext,
    server_id: str,
    reason: str,
    status_code: int,
    detail: object,
) -> None:
    """Record upload validation failure and raise HTTPException.

    Args:
        context: Shared route registration dependencies.
        server_id: Target server identifier.
        reason: Upload failure reason label.
        status_code: HTTP status code for rejection.
        detail: Error detail payload.

    Raises:
        HTTPException: Always raised with provided status and detail.
    """
    await record_upload_failure_metrics(
        telemetry=context.telemetry,
        server_id=server_id,
        reason=reason,
    )
    raise HTTPException(status_code=status_code, detail=detail)


async def _validate_upload_request_inputs(
    *,
    context: RouteRegistrationContext,
    server_id: str,
    request: Request,
    file: list[UploadFile],
    sha256: list[str] | None,
) -> tuple[str, list[UploadFile], list[str] | None]:
    """Validate upload request preconditions and normalize inputs.

    Args:
        context: Shared route registration dependencies.
        server_id: Target server identifier from URL.
        request: Incoming HTTP request.
        file: Uploaded multipart file parts.
        sha256: Optional SHA-256 list from multipart form.

    Returns:
        Tuple of ``(session_id, files, sha256_values)`` after validation.

    Raises:
        HTTPException: On validation failures.
    """
    try:
        raise_unknown_server_if_missing(server_id=server_id, proxy_map=context.proxy_map)
    except HTTPException as exc:
        await _raise_upload_validation_http_exception(
            context=context,
            server_id=UNKNOWN_SERVER_ID,
            reason="unknown_server",
            status_code=exc.status_code,
            detail=exc.detail,
        )

    session_id = request.headers.get(MCP_SESSION_ID_HEADER)
    if not session_id:
        await _raise_upload_validation_http_exception(
            context=context,
            server_id=server_id,
            reason="missing_session_header",
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Mcp-Session-Id header.",
        )

    try:
        raise_fail_closed_if_rejecting(persistence_policy=context.persistence_policy)
    except HTTPException as exc:
        await _raise_upload_validation_http_exception(
            context=context,
            server_id=server_id,
            reason="fail_closed",
            status_code=exc.status_code,
            detail=exc.detail,
        )

    files = [item for item in file if item is not None]
    if not files:
        await _raise_upload_validation_http_exception(
            context=context,
            server_id=server_id,
            reason="no_file_parts",
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file parts were provided.",
        )

    sha256_values = normalize_sha256_inputs(sha256)
    try:
        validate_sha256_count(files_count=len(files), sha256_values=sha256_values)
    except HTTPException as exc:
        await _raise_upload_validation_http_exception(
            context=context,
            server_id=server_id,
            reason="sha256_count_mismatch",
            status_code=exc.status_code,
            detail=exc.detail,
        )

    return session_id, files, sha256_values


async def _persist_upload_records(
    *,
    context: RouteRegistrationContext,
    server_id: str,
    session_id: str,
    files: list[UploadFile],
    sha256_values: list[str] | None,
):
    """Persist uploaded files and return created upload records.

    Args:
        context: Shared route registration dependencies.
        server_id: Target server identifier.
        session_id: MCP session id for upload ownership.
        files: Validated upload file objects.
        sha256_values: Optional SHA-256 expectations aligned to files.

    Returns:
        List of persisted upload records.

    Raises:
        HTTPException: On upload persistence and processing failures.
    """
    save_tasks: list[asyncio.Task] = []
    records = []
    try:
        await context.session_store.ensure_session(server_id, session_id)
        for index, uploaded_file in enumerate(files):
            expected_sha256 = sha256_values[index] if sha256_values else None
            save_tasks.append(
                asyncio.create_task(
                    save_one_uploaded_file(
                        persist_upload_stream=context.save_upload_stream,
                        resolved_config=context.resolved_config,
                        session_store=context.session_store,
                        server_id=server_id,
                        session_id=session_id,
                        uploaded_file=uploaded_file,
                        expected_sha256=expected_sha256,
                    )
                )
            )
        results = await asyncio.gather(*save_tasks, return_exceptions=True)
        errors = [result for result in results if isinstance(result, Exception)]
        records = [result for result in results if not isinstance(result, Exception)]
        if errors:
            await _raise_upload_http_exception_for_failure(
                error=errors[0],
                records=records,
                context=context,
                server_id=server_id,
                session_id=session_id,
            )
    except HTTPException:
        raise
    except Exception as exc:
        await _raise_upload_http_exception_for_failure(
            error=exc,
            records=records,
            context=context,
            server_id=server_id,
            session_id=session_id,
        )
    return records


async def _process_upload_endpoint(
    *,
    context: RouteRegistrationContext,
    server_id: str,
    request: Request,
    file: list[UploadFile],
    sha256: list[str] | None,
) -> dict[str, object]:
    """Handle full upload endpoint flow from validation to response payload.

    Args:
        context: Shared route registration dependencies.
        server_id: Target server identifier from URL.
        request: Incoming request.
        file: Uploaded multipart file list.
        sha256: Optional SHA-256 input list.

    Returns:
        Upload response payload with handles and metadata.
    """
    uploaded_files = [uploaded_file for uploaded_file in file if uploaded_file is not None]
    try:
        session_id, files, sha256_values = await _validate_upload_request_inputs(
            context=context,
            server_id=server_id,
            request=request,
            file=file,
            sha256=sha256,
        )
        records = await _persist_upload_records(
            context=context,
            server_id=server_id,
            session_id=session_id,
            files=files,
            sha256_values=sha256_values,
        )
        response_payload = build_upload_response_payload(
            server_id=server_id,
            session_id=session_id,
            uri_scheme=context.resolved_config.uploads.uri_scheme,
            records=records,
        )
        await record_upload_batch_metrics(telemetry=context.telemetry, server_id=server_id, records=records)
        return response_payload
    finally:
        await close_uploaded_files(uploaded_files)


def _register_health_route(*, context: RouteRegistrationContext) -> None:
    """Register /healthz route.

    Args:
        context: Shared route registration dependencies.
    """

    @context.app.get("/healthz", response_model=None)
    async def healthz() -> JSONResponse:
        """Return aggregate health of upstreams and persistence backend.

        Returns:
            JSON response with 200 (healthy) or 503 (degraded) status.
        """
        checks = await collect_upstream_health_checks(context.upstream_health)
        persistence = await context.runtime_ref["current"].health_snapshot()
        persistence["effective_type"] = context.runtime_ref["current"].backend_type
        payload, has_error = build_healthz_payload(
            app=context.app,
            resolved_config=context.resolved_config,
            checks=checks,
            persistence=persistence,
            persistence_policy=context.persistence_policy,
        )
        if has_error:
            return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
        return JSONResponse(status_code=status.HTTP_200_OK, content=payload)


def _register_upload_route(*, context: RouteRegistrationContext) -> None:
    """Register upload endpoint route.

    Args:
        context: Shared route registration dependencies.
    """

    @context.app.post(context.upload_route)
    async def upload_endpoint(
        server_id: str,
        request: Request,
        file: list[UploadFile] = File(...),
        sha256: list[str] | None = Form(default=None),
    ) -> dict[str, object]:
        """Accept one or many multipart uploads and return durable handle(s).

        Args:
            server_id: Target server identifier from the URL path.
            request: Incoming FastAPI request.
            file: Uploaded file parts.
            sha256: Optional SHA-256 digest(s) for integrity checks.

        Returns:
            Response dict with upload handles and per-file metadata.

        Raises:
            HTTPException: On validation or persistence errors.
        """
        return await _process_upload_endpoint(
            context=context,
            server_id=server_id,
            request=request,
            file=file,
            sha256=sha256,
        )


def _register_artifact_download_route(*, context: RouteRegistrationContext) -> None:
    """Register artifact download endpoint route and enable log.

    Args:
        context: Shared route registration dependencies.
    """

    @context.app.get("/artifacts/{server_id}/{session_id}/{artifact_id}/{filename:path}")
    async def download_artifact(
        server_id: str,
        session_id: str,
        artifact_id: str,
        request: Request,
        filename: str,
    ) -> FileResponse:
        """Stream a previously uploaded artifact back to the caller.

        Args:
            server_id: Target server identifier.
            session_id: Session identifier owning the artifact.
            artifact_id: Unique artifact identifier.
            request: Incoming FastAPI request.
            filename: Expected filename for validation.

        Returns:
            Streamed file response with the artifact content.

        Raises:
            HTTPException: On missing artifacts, session mismatches, or
                persistence failures.
        """
        raise_unknown_server_if_missing(server_id=server_id, proxy_map=context.proxy_map)
        started_at = time.perf_counter()

        signed_auth = bool(getattr(request.state, "artifact_download_signed_auth", False))
        auth_mode = "signed_url" if signed_auth else "session_context"
        if not signed_auth:
            request_session_id = request.headers.get(MCP_SESSION_ID_HEADER) or request.query_params.get("session_id")
            if not request_session_id or request_session_id != session_id:
                await _record_artifact_download_metrics(
                    context=context,
                    server_id=server_id,
                    result="session_mismatch",
                    auth_mode=auth_mode,
                    started_at=started_at,
                )
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session mismatch.")
        try:
            raise_fail_closed_if_rejecting(persistence_policy=context.persistence_policy)
        except HTTPException:
            await _record_artifact_download_metrics(
                context=context,
                server_id=server_id,
                result="fail_closed",
                auth_mode=auth_mode,
                started_at=started_at,
            )
            raise

        try:
            record = await resolve_artifact_for_read(
                store=context.session_store,
                server_id=server_id,
                session_id=session_id,
                artifact_id=artifact_id,
                expected_filename=filename,
            )
        except ArtifactNotFoundError as exc:
            await _record_artifact_download_metrics(
                context=context,
                server_id=server_id,
                result="not_found",
                auth_mode=auth_mode,
                started_at=started_at,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.") from exc
        except ArtifactFileMissingError as exc:
            await _record_artifact_download_metrics(
                context=context,
                server_id=server_id,
                result="file_missing",
                auth_mode=auth_mode,
                started_at=started_at,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file missing.") from exc
        except ArtifactFilenameMismatchError as exc:
            await _record_artifact_download_metrics(
                context=context,
                server_id=server_id,
                result="filename_mismatch",
                auth_mode=auth_mode,
                started_at=started_at,
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact filename mismatch.") from exc
        except Exception as exc:
            if await apply_runtime_failure_policy_if_persistent_backend(
                resolved_config=context.resolved_config,
                persistence_policy=context.persistence_policy,
                runtime_ref=context.runtime_ref,
                session_store=context.session_store,
                app=context.app,
                component="download_artifact",
                error=str(exc),
                build_memory_persistence_runtime=context.build_memory_persistence_runtime,
            ):
                await _record_artifact_download_metrics(
                    context=context,
                    server_id=server_id,
                    result="persistence_unavailable",
                    auth_mode=auth_mode,
                    started_at=started_at,
                )
                raise_persistence_backend_operation_failed_http_exception(cause=exc)
            await _record_artifact_download_metrics(
                context=context,
                server_id=server_id,
                result="error",
                auth_mode=auth_mode,
                started_at=started_at,
            )
            raise
        await _record_artifact_download_metrics(
            context=context,
            server_id=server_id,
            result="success",
            auth_mode=auth_mode,
            started_at=started_at,
            size_bytes=record.size_bytes,
        )
        return FileResponse(path=record.abs_path, media_type=record.mime_type, filename=record.filename)

    logger.info(
        "Artifact download route enabled",
        extra={"path": build_artifact_download_path("{server_id}", "{session_id}", "{artifact_id}", "{filename}")},
    )


def register_route_stack(*, context: RouteRegistrationContext) -> None:
    """Register adapter HTTP route handlers.

    Args:
        context: Shared route registration dependencies.
    """
    _register_health_route(context=context)
    _register_upload_route(context=context)
    if context.resolved_config.core.allow_artifacts_download:
        _register_artifact_download_route(context=context)
