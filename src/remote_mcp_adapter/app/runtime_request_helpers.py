"""Request/auth/config helper utilities used by app runtime and middleware."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from ..constants import ARTIFACT_PATH_PREFIX, DEFAULT_ADAPTER_AUTH_HEADER
from ..config import AdapterConfig
from ..config.load import load_config
from ..proxy.upload_helpers import build_server_upload_path


def validate_adapter_auth(request: Request, config: AdapterConfig) -> None:
    """Validate adapter-level auth header when enabled.

    Args:
        request: Incoming FastAPI request.
        config: Adapter configuration carrying auth settings.

    Raises:
        HTTPException: With 403 status if the auth token is missing or invalid.
    """
    auth_config = config.core.auth
    if not auth_config.enabled:
        return
    header_name = auth_config.header_name.strip() or DEFAULT_ADAPTER_AUTH_HEADER
    provided_token = request.headers.get(header_name)
    expected_token = auth_config.token or ""
    if provided_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("Forbidden: missing or invalid adapter auth token. " f"Please check auth token header '{header_name}'."),
        )


def apply_cors_middleware(app: FastAPI, config: AdapterConfig) -> None:
    """Apply CORS middleware when enabled in config.

    Args:
        app: FastAPI application instance.
        config: Adapter configuration carrying CORS settings.
    """
    cors = config.core.cors
    if not cors.enabled:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors.allowed_origins,
        allow_methods=cors.allowed_methods,
        allow_headers=cors.allowed_headers,
        allow_credentials=cors.allow_credentials,
    )


def resolve_config(config: AdapterConfig | None, config_path: str | None) -> AdapterConfig:
    """Resolve config object from input argument or file path.

    Args:
        config: Pre-built config object, used as-is when provided.
        config_path: Filesystem path to a YAML config file (falls back to
            ``MCP_ADAPTER_CONFIG`` env var or ``config.yaml``).

    Returns:
        Resolved ``AdapterConfig`` instance.
    """
    if config is not None:
        return config
    path = config_path or os.getenv("MCP_ADAPTER_CONFIG", "config.yaml")
    return load_config(path)


def resolve_server_id_for_path(path: str, mount_path_to_server_id: dict[str, str]) -> str | None:
    """Return the server ID whose mount path is a prefix of path, or None.

    Args:
        path: Request URL path.
        mount_path_to_server_id: Mapping of mount paths to server identifiers.

    Returns:
        First matching server ID, or None.
    """
    for mount_path, server_id in mount_path_to_server_id.items():
        if path.startswith(mount_path):
            return server_id
    return None


def upload_path_prefix(upload_path: str) -> str:
    """Strip the placeholder segment from an upload path to get the bare prefix.

    Args:
        upload_path: Full upload route template with a server-id placeholder.

    Returns:
        Upload route prefix without the trailing placeholder.
    """
    return build_server_upload_path(upload_path, "")


def is_stateful_request_path(
    *,
    path: str,
    mount_path_to_server_id: dict[str, str],
    upload_path_prefix: str,
) -> bool:
    """Return True when the path targets a mounted server, upload, or artifact.

    Args:
        path: Request URL path.
        mount_path_to_server_id: Mapping of mount paths to server identifiers.
        upload_path_prefix: Bare upload route prefix.

    Returns:
        True if the path requires stateful session handling.
    """
    if resolve_server_id_for_path(path, mount_path_to_server_id) is not None:
        return True
    if path.startswith(upload_path_prefix):
        return True
    if path.startswith(ARTIFACT_PATH_PREFIX):
        return True
    return False
