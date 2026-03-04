"""Auth and request-route helper utilities for HTTP middleware."""

from __future__ import annotations

from ..constants import ARTIFACT_PATH_PREFIX, GLOBAL_SERVER_ID

_OAUTH_DISCOVERY_PATH_SUFFIXES = (
    "/.well-known/oauth-authorization-server",
    "/.well-known/openid-configuration",
)

_PUBLIC_UNPROTECTED_PATHS = (
    "/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def upload_prefix_parts(upload_path_prefix: str) -> tuple[str, str]:
    """Return normalized upload prefix and its segment-safe match prefix.

    Args:
        upload_path_prefix: Configured upload prefix, with or without trailing slash.

    Returns:
        Tuple ``(normalized_prefix, match_prefix)`` where:
        - ``normalized_prefix`` has no trailing slash (except root ``/``).
        - ``match_prefix`` enforces segment boundaries for ``startswith`` checks.
    """
    normalized_prefix = upload_path_prefix.rstrip("/") or "/"
    if normalized_prefix == "/":
        return normalized_prefix, normalized_prefix
    return normalized_prefix, f"{normalized_prefix}/"


def is_public_unprotected_path(path: str) -> bool:
    """Return True when path is always public and bypasses auth/session headers.

    Args:
        path: Request URL path.

    Returns:
        True if *path* matches a known public endpoint.
    """
    return any(path == public_path or path.startswith(f"{public_path}/") for public_path in _PUBLIC_UNPROTECTED_PATHS)


def parse_artifact_download_path(path: str) -> tuple[str, str, str, str] | None:
    """Parse /artifacts/{server}/{session}/{artifact}/{filename:path} path segments.

    Args:
        path: Request URL path to parse.

    Returns:
        Tuple of ``(server_id, session_id, artifact_id, filename)`` if the
        path matches the expected layout, or None otherwise.
    """
    if not path.startswith(ARTIFACT_PATH_PREFIX):
        return None
    parts = path.split("/", 5)
    if len(parts) < 6:
        return None
    server_id = parts[2]
    session_id = parts[3]
    artifact_id = parts[4]
    filename = parts[5]
    if not server_id or not session_id or not artifact_id or not filename:
        return None
    return server_id, session_id, artifact_id, filename


def route_group_for_metrics(path: str, *, upload_path_prefix: str) -> str:
    """Reduce high-cardinality paths into stable metric route groups.

    Args:
        path: Request URL path.
        upload_path_prefix: Upload path prefix used to match upload routes.

    Returns:
        Templatized route group string suitable for metric labels.
    """
    if path.startswith(ARTIFACT_PATH_PREFIX):
        return "/artifacts/{server_id}/{session_id}/{artifact_id}/{filename}"
    normalized_upload_prefix, upload_match_prefix = upload_prefix_parts(upload_path_prefix)
    if path.startswith(upload_match_prefix):
        if normalized_upload_prefix == "/":
            return "/{server_id}"
        return f"{normalized_upload_prefix}/{{server_id}}"
    if is_public_unprotected_path(path):
        return path
    if path.endswith(_OAUTH_DISCOVERY_PATH_SUFFIXES):
        return "/.well-known/*"
    return path


def is_oauth_discovery_path(path: str) -> bool:
    """Return True when path matches known OAuth/OpenID discovery endpoints.

    Args:
        path: Request URL path.

    Returns:
        True if *path* ends with a recognized discovery suffix.
    """
    return path.endswith(_OAUTH_DISCOVERY_PATH_SUFFIXES)


async def record_auth_rejection(
    *,
    telemetry,
    route_group: str,
    reason: str,
    server_id: str = GLOBAL_SERVER_ID,
) -> None:
    """Emit auth rejection telemetry when enabled.

    Args:
        telemetry: Optional telemetry recorder (no-op when None or disabled).
        route_group: Metric route group label.
        reason: Short rejection reason tag.
    """
    if telemetry is None or not getattr(telemetry, "enabled", False):
        return
    await telemetry.record_auth_rejection(
        reason=reason,
        route_group=route_group,
        server_id=server_id,
    )
