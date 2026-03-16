"""Request helpers for adapter-side session integrity."""

from __future__ import annotations

import hashlib

from fastapi import Request

from ..app.auth_helpers import parse_artifact_download_path, upload_prefix_parts
from ..app.runtime_request_helpers import get_adapter_auth_token, resolve_server_id_for_path
from ..config.schemas.root import AdapterConfig
from ..constants import MCP_SESSION_ID_HEADER
from .models import SessionTrustCandidate, SessionTrustContext


def build_adapter_auth_trust_candidate(
    *,
    request: Request,
    config: AdapterConfig,
    mount_path_to_server_id: dict[str, str],
    upload_path_prefix: str,
) -> SessionTrustCandidate | None:
    """Build a session trust candidate from an adapter-authenticated request.

    Args:
        request: Incoming HTTP request.
        config: Full adapter configuration.
        mount_path_to_server_id: Mapping of mount paths to server identifiers.
        upload_path_prefix: Normalized upload route prefix.

    Returns:
        Trust candidate for adapter-authenticated stateful requests, or ``None``
        when the request does not carry adapter auth or does not target a
        session-scoped adapter route.
    """
    if not config.core.auth.enabled:
        return None
    if bool(getattr(request.state, "artifact_download_signed_auth", False)):
        return None
    if bool(getattr(request.state, "upload_signed_auth", False)):
        return None

    auth_token = get_adapter_auth_token(request, config)
    if not auth_token:
        return None

    server_id, session_id = _resolve_session_target(
        request=request,
        mount_path_to_server_id=mount_path_to_server_id,
        upload_path_prefix=upload_path_prefix,
    )
    if server_id is None or session_id is None:
        return None

    return SessionTrustCandidate(
        server_id=server_id,
        session_id=session_id,
        trust_context=SessionTrustContext(
            binding_kind="adapter_auth_token",
            fingerprint=_adapter_auth_fingerprint(auth_token),
        ),
    )


def _adapter_auth_fingerprint(auth_token: str) -> str:
    """Return a stable non-secret fingerprint for an adapter auth token.

    Args:
        auth_token: Raw adapter auth token value from the request.

    Returns:
        Hex-encoded SHA-256 fingerprint.
    """
    return hashlib.sha256(auth_token.encode("utf-8")).hexdigest()


def _resolve_session_target(
    *,
    request: Request,
    mount_path_to_server_id: dict[str, str],
    upload_path_prefix: str,
) -> tuple[str | None, str | None]:
    """Resolve the adapter session targeted by the current request.

    Args:
        request: Incoming HTTP request.
        mount_path_to_server_id: Mapping of mount paths to server identifiers.
        upload_path_prefix: Normalized upload route prefix.

    Returns:
        Tuple of ``(server_id, session_id)`` when the request targets an
        adapter-managed stateful route, otherwise ``(None, None)``.
    """
    path = request.url.path
    header_session_id = request.headers.get(MCP_SESSION_ID_HEADER)

    mounted_server_id = resolve_server_id_for_path(path, mount_path_to_server_id)
    if mounted_server_id is not None:
        return mounted_server_id, header_session_id

    artifact_parts = parse_artifact_download_path(path)
    if artifact_parts is not None:
        server_id, session_id, _, _ = artifact_parts
        return server_id, session_id

    _, upload_match_prefix = upload_prefix_parts(upload_path_prefix)
    if path.startswith(upload_match_prefix):
        relative_path = path[len(upload_match_prefix) :].lstrip("/")
        upload_server_id = relative_path.split("/", 1)[0] if relative_path else ""
        if upload_server_id:
            return upload_server_id, header_session_id

    return None, None
