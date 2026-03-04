"""Helpers for upload endpoint path and URL construction."""

from __future__ import annotations

from urllib.parse import quote

from fastmcp import Context

from ..constants import ARTIFACT_PATH_PREFIX
from ..config import AdapterConfig


def build_server_upload_path(upload_path: str, server_id: str) -> str:
    """Build server-scoped upload path from the configured upload base path.

    Args:
        upload_path: Configured upload base path.
        server_id: Server identifier.

    Returns:
        Absolute URL path for the server's upload endpoint.
    """
    base = upload_path if upload_path.startswith("/") else f"/{upload_path}"
    if base == "/":
        return f"/{server_id}"
    return f"{base.rstrip('/')}/{server_id}"


def build_artifact_download_path(
    server_id: str,
    session_id: str,
    artifact_id: str,
    filename: str,
) -> str:
    """Build HTTP download path for one stored artifact.

    Args:
        server_id: Server identifier.
        session_id: Session identifier.
        artifact_id: Artifact identifier.
        filename: Artifact filename (URL-encoded in the path).

    Returns:
        URL path string for downloading the artifact.
    """
    base_path = f"{ARTIFACT_PATH_PREFIX}{server_id}/{session_id}/{artifact_id}"
    return f"{base_path}/{quote(filename, safe='')}"


def derive_public_base_url(config: AdapterConfig, context: Context | None = None) -> str:
    """Resolve public base URL from config first, then current HTTP request, then host/port.

    Args:
        config: Full adapter configuration.
        context: Optional MCP request context for header inspection.

    Returns:
        Base URL string without trailing slash.
    """
    if config.core.public_base_url:
        return config.core.public_base_url.rstrip("/")

    request = None
    if context is not None and context.request_context is not None:
        request = context.request_context.request

    if request is not None:
        forwarded_scheme = request.headers.get("x-forwarded-proto")
        forwarded_host = request.headers.get("x-forwarded-host")
        if forwarded_scheme and forwarded_host:
            return f"{forwarded_scheme}://{forwarded_host}".rstrip("/")
        return str(request.base_url).rstrip("/")

    host = config.core.host
    if host in ("", "0.0.0.0"):
        host = "127.0.0.1"
    return f"http://{host}:{config.core.port}".rstrip("/")
