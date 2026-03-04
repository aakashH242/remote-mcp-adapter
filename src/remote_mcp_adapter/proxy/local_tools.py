"""Local helper tools that are registered on each server proxy."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from mcp.types import ToolAnnotations

from fastmcp import Context
from fastmcp.server.dependencies import get_context

from ..config import AdapterConfig
from .factory import ProxyMount
from .upload_credentials import UploadCredentialManager
from .upload_helpers import build_server_upload_path, derive_public_base_url

_TOOL_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_]+")


def get_upload_url_tool_name(server_id: str) -> str:
    """Return deterministic server-prefixed helper tool name.

    Args:
        server_id: Server identifier.
    """
    normalized = _TOOL_NAME_SAFE_RE.sub("_", server_id.strip()).strip("_")
    if not normalized:
        normalized = "server"
    return f"{normalized}_get_upload_url"


def _is_sha256_required(config: AdapterConfig) -> bool:
    """Return True when upload endpoint requires client-provided SHA-256 values.

    Args:
        config: Adapter configuration.
    """
    uploads = getattr(config, "uploads", None)
    return bool(getattr(uploads, "require_sha256", False))


def _build_upload_examples(*, upload_url: str, session_id: str, sha256_required: bool) -> tuple[str, str]:
    """Build single-file and multi-file curl examples for upload staging.

    Args:
        upload_url: Fully resolved upload endpoint URL.
        session_id: Session id that must be sent in `Mcp-Session-Id`.
        sha256_required: Whether each file requires one matching sha256 form field.

    Returns:
        Tuple of ``(single_file_example, multi_file_example)`` command strings.
    """
    single_file_parts = [
        f'curl -X POST "{upload_url}"',
        f'-H "Mcp-Session-Id: {session_id}"',
        '-F "file=@/path/to/file"',
    ]
    multiple_file_parts = [
        f'curl -X POST "{upload_url}"',
        f'-H "Mcp-Session-Id: {session_id}"',
        '-F "file=@/path/to/file1"',
        '-F "file=@/path/to/file2"',
    ]
    if sha256_required:
        single_file_parts.append('-F "sha256=<sha256_for_file>"')
        multiple_file_parts.extend(
            [
                '-F "sha256=<sha256_for_file1>"',
                '-F "sha256=<sha256_for_file2>"',
            ]
        )
    return " ".join(single_file_parts), " ".join(multiple_file_parts)


def register_get_upload_url_tool(
    *,
    mount: ProxyMount,
    config: AdapterConfig,
    upload_credentials: UploadCredentialManager | None = None,
) -> None:
    """Register the upload endpoint discovery helper tool on one proxy.

    Args:
        mount: Proxy mount to register the tool on.
        config: Full adapter configuration.
        upload_credentials: Optional credential manager for signed URLs.
    """
    upload_path = build_server_upload_path(config.core.upload_path, mount.server.id)
    helper_tool_name = get_upload_url_tool_name(mount.server.id)

    @mount.proxy.tool(
        name=helper_tool_name,
        title="Construct Upload Endpoint",
        description=(
            "Pre-requisite tool for any tool that needs to upload files or read files from local storage: "
            "returns a pre-signed upload URL and example usage. "
            "Use the URL to stage files for upload and then pass the returned "
            "upload:// handles to the tool that need to upload files or read files from local storage."
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
    )
    async def get_upload_url(ctx: Context | None = None) -> dict[str, Any]:
        """Build instructions for uploading files and obtaining session-scoped upload handles.

        Args:
            ctx: Optional MCP context; resolved from dependency injection when None.
        """
        context = ctx or get_context()
        session_id = context.session_id
        base_url = derive_public_base_url(config, context)
        query_params: dict[str, str] = {}
        if upload_credentials is not None:
            query_params = await upload_credentials.issue(
                server_id=mount.server.id,
                session_id=session_id,
            )
        query_string = urlencode(query_params)
        upload_url = f"{base_url}{upload_path}"
        if query_string:
            upload_url = f"{upload_url}?{query_string}"
        sha256_required = _is_sha256_required(config)
        single_file_example, multiple_file_example = _build_upload_examples(
            upload_url=upload_url,
            session_id=session_id,
            sha256_required=sha256_required,
        )
        integrity_note = (
            "Provide one `sha256` multipart form field per uploaded file in the same order as `file` fields."
            if sha256_required
            else "Optional: include `sha256` multipart form fields for end-to-end integrity checks."
        )
        return {
            "server_id": mount.server.id,
            "tool_name": helper_tool_name,
            "upload_url": upload_url,
            "method": "POST",
            "headers": {"Mcp-Session-Id": session_id},
            "auth_mode": "signed_upload_credentials" if query_params else "header_token_or_none",
            "credential_ttl_seconds": upload_credentials.ttl_seconds if query_params else None,
            "field_name": "file",
            "supports_multiple_files": True,
            "sha256_required": sha256_required,
            "sha256_field_name": "sha256",
            "sha256_per_file": True,
            "session_id": session_id,
            "example_curl": single_file_example,
            "example_curl_multiple": multiple_file_example,
            "note": (
                f"Use `{helper_tool_name}` for this server, upload file(s) first using this endpoint, "
                "then pass returned upload:// handles (response.upload_handles) to the configured upload_consumer tools."
            ),
            "integrity_note": integrity_note,
        }
