"""Local helper resources registered on each server proxy."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastmcp.resources import Resource

from .factory import ProxyMount

_UPLOAD_TOOL_PLACEHOLDER = "{{UPLOAD_TOOL_NAME}}"
_LEGACY_UPLOAD_TOOL_PLACEHOLDER = "`get_upload_url`"


def _upload_workflow_doc_path() -> Path:
    """Return the absolute path to the bundled upload_workflow.md guidance file."""
    return Path(__file__).resolve().parent.parent / "resources" / "upload_workflow.md"


def _default_upload_workflow_text(upload_endpoint_tool_name: str) -> str:
    """Generate built-in fallback guidance when the bundled markdown file is not readable.

    Args:
        upload_endpoint_tool_name: Server-specific upload tool name for template substitution.
    """
    return (
        "# Upload workflow for upload_consumer tools\n\n"
        f"1. Call `{upload_endpoint_tool_name}` to get the session-scoped multipart URL and headers.\n"
        "2. POST multipart/form-data to that URL with `file` fields.\n"
        "3. The response returns `upload://sessions/<session>/<upload_id>` handles.\n"
        "4. Call the upload_consumer tool and pass those `upload://...` handles in its configured path argument.\n\n"
        "Important:\n"
        "- Do not pass local filesystem paths directly to upload_consumer tools.\n"
        "- Upload handles are session-scoped and must match `Mcp-Session-Id`."
    )


def _render_upload_workflow_template(*, template: str, upload_endpoint_tool_name: str) -> str:
    """Render upload workflow template with the server-specific helper tool name.

    Prefers explicit ``{{UPLOAD_TOOL_NAME}}`` placeholder replacement.
    Falls back to replacing the exact legacy token `` `get_upload_url` ``.

    Args:
        template: Source markdown template text.
        upload_endpoint_tool_name: Server-specific helper tool name.

    Returns:
        Rendered markdown text.
    """
    if _UPLOAD_TOOL_PLACEHOLDER in template:
        return template.replace(_UPLOAD_TOOL_PLACEHOLDER, upload_endpoint_tool_name)
    if _LEGACY_UPLOAD_TOOL_PLACEHOLDER in template:
        return template.replace(_LEGACY_UPLOAD_TOOL_PLACEHOLDER, f"`{upload_endpoint_tool_name}`")
    return template


def _load_upload_workflow_text(upload_endpoint_tool_name: str) -> str:
    """Load and patch the upload workflow markdown, falling back to the built-in text on error.

    Args:
        upload_endpoint_tool_name: Tool name to substitute into the template.
    """
    doc_path = _upload_workflow_doc_path()
    try:
        content = doc_path.read_text(encoding="utf-8")
        return _render_upload_workflow_template(
            template=content,
            upload_endpoint_tool_name=upload_endpoint_tool_name,
        )
    except Exception:
        return _default_upload_workflow_text(upload_endpoint_tool_name)


_UPLOAD_WORKFLOW_DOC_CACHE: dict[str, str] = {}


async def _get_upload_workflow_text(upload_endpoint_tool_name: str) -> str:
    """Return the cached upload workflow text, loading it from disk once per tool name.

    Args:
        upload_endpoint_tool_name: Tool name to substitute into the template.
    """
    cached = _UPLOAD_WORKFLOW_DOC_CACHE.get(upload_endpoint_tool_name)
    if cached is not None:
        return cached
    loaded = await asyncio.to_thread(_load_upload_workflow_text, upload_endpoint_tool_name)
    _UPLOAD_WORKFLOW_DOC_CACHE[upload_endpoint_tool_name] = loaded
    return loaded


def register_upload_workflow_resource(*, mount: ProxyMount, upload_endpoint_tool_name: str) -> None:
    """Register static upload guidance as an MCP resource.

    Args:
        mount: Proxy mount to register the resource on.
        upload_endpoint_tool_name: Server-specific upload tool name.
    """

    async def _read_upload_workflow_resource() -> str:
        """Serve the upload workflow guidance text for this server's tool name."""
        return await _get_upload_workflow_text(upload_endpoint_tool_name)

    resource = Resource.from_function(
        fn=_read_upload_workflow_resource,
        uri="doc://upload_workflow.md",
        name="Guide for upload_consumer upload flow",
        description="How to stage files and pass upload:// handles into upload_consumer tools.",
        mime_type="text/markdown",
    )
    mount.proxy.add_resource(resource)
