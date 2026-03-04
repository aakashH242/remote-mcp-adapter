"""Request/auth/config helper utilities used by app runtime and middleware."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware

from ..constants import ARTIFACT_PATH_PREFIX, DEFAULT_ADAPTER_AUTH_HEADER
from ..config import AdapterConfig
from ..config.load import load_config


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
    """Return the best matching server ID for path, or None.

    Args:
        path: Request URL path.
        mount_path_to_server_id: Mapping of mount paths to server identifiers.

    Returns:
        Server ID for the most specific matching mount path, or None.
    """
    best_match: tuple[int, str] | None = None
    for mount_path, server_id in mount_path_to_server_id.items():
        is_exact_match = path == mount_path
        is_child_path_match = path.startswith(f"{mount_path}/")
        if not (is_exact_match or is_child_path_match):
            continue
        candidate = (len(mount_path), server_id)
        if best_match is None or candidate[0] > best_match[0]:
            best_match = candidate
    if best_match is None:
        return None
    return best_match[1]


def upload_path_prefix(upload_path: str) -> str:
    """Return normalized upload route prefix for server-scoped upload endpoints.

    Args:
        upload_path: Configured upload base path.

    Returns:
        Upload route prefix ending with ``/`` for unambiguous prefix matching.
    """
    base = upload_path if upload_path.startswith("/") else f"/{upload_path}"
    if base == "/":
        return "/"
    return f"{base.rstrip('/')}/"


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
