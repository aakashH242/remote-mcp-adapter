"""Canonicalization helpers for pinned tool-definition snapshots."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastmcp.tools.tool import Tool

from ...core.repo.records import ToolDefinitionSnapshot
from ..tool_metadata_sanitization.schema import canonicalize_schema_metadata
from ..tool_metadata_sanitization.text import canonicalize_metadata_text

_SESSION_WARNING_PREFIX = "WARNING: Tool definitions changed during this adapter session."
_TOOL_WARNING_PREFIXES = (
    "WARNING: This tool definition changed after the session baseline was pinned.",
    "WARNING: This tool was not present when the session baseline was pinned.",
)


def canonicalize_tool(tool: Tool) -> ToolDefinitionSnapshot:
    """Build a deterministic client-visible snapshot for one tool.

    Args:
        tool: Tool to canonicalize.

    Returns:
        Pinned snapshot containing the normalized payload and SHA-256 hash.
    """
    payload = {
        "name": tool.name,
        "title": canonicalize_metadata_text(tool.title),
        "description": _normalize_description(tool.description),
        "inputSchema": canonicalize_schema_metadata(tool.parameters),
        "outputSchema": canonicalize_schema_metadata(tool.output_schema),
        "icons": _normalize_json(tool.icons),
        "annotations": _normalize_annotations(tool.annotations),
        "execution": _normalize_json(tool.execution),
        "meta": _normalize_meta(tool.get_meta()),
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return ToolDefinitionSnapshot(
        name=tool.name,
        canonical_hash=hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
        payload=payload,
    )


def canonicalize_tools(tools: list[Tool] | tuple[Tool, ...]) -> dict[str, ToolDefinitionSnapshot]:
    """Canonicalize a full tool sequence into name-keyed snapshots.

    Args:
        tools: Sequence of tools to canonicalize.

    Returns:
        Mapping of tool name to pinned snapshot.
    """
    return {tool.name: canonicalize_tool(tool) for tool in tools}


def _normalize_description(description: str | None) -> str | None:
    """Normalize descriptions while excluding adapter-added drift warnings.

    Args:
        description: Raw tool description.

    Returns:
        Normalized description without drift warning prefixes.
    """
    if description is None:
        return None
    kept_lines: list[str] = []
    for raw_line in description.splitlines():
        line = raw_line.strip()
        if line.startswith(_SESSION_WARNING_PREFIX):
            continue
        if any(line.startswith(prefix) for prefix in _TOOL_WARNING_PREFIXES):
            continue
        kept_lines.append(raw_line.rstrip())
    normalized = "\n".join(line for line in kept_lines if line).strip()
    return canonicalize_metadata_text(normalized or None)


def _normalize_meta(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize client-visible meta values and strip private FastMCP keys.

    Args:
        meta: Tool meta dictionary.

    Returns:
        Normalized meta or ``None`` when empty.
    """
    if not meta:
        return None
    normalized_meta: dict[str, Any] = {}
    for key, value in meta.items():
        normalized_key = str(key)
        if normalized_key in {"fastmcp", "_fastmcp"} and isinstance(value, dict):
            normalized_meta[normalized_key] = {
                sub_key: _normalize_json(sub_value)
                for sub_key, sub_value in sorted(value.items())
                if not str(sub_key).startswith("_")
            }
            continue
        normalized_meta[normalized_key] = _normalize_json(value)
    return normalized_meta or None


def _normalize_annotations(annotations: Any) -> dict[str, Any] | None:
    """Normalize client-visible annotation fields deterministically.

    Args:
        annotations: Tool annotations object or dict.

    Returns:
        Normalized annotations payload or ``None`` when absent.
    """
    if annotations is None:
        return None
    if hasattr(annotations, "model_dump"):
        annotations = annotations.model_dump(by_alias=True, exclude_none=True)
    if not isinstance(annotations, dict):
        return _normalize_json(annotations)

    normalized: dict[str, Any] = {}
    for key, value in annotations.items():
        normalized_key = str(key)
        if normalized_key == "title" and isinstance(value, str):
            normalized[normalized_key] = canonicalize_metadata_text(value)
            continue
        normalized[normalized_key] = _normalize_json(value)
    return normalized or None


def _normalize_json(value: Any) -> Any:
    """Recursively sort JSON-like values into a deterministic representation.

    Args:
        value: Raw JSON-like value.

    Returns:
        Deterministically ordered JSON-friendly value.
    """
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        return {str(key): _normalize_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_json(item) for item in value]
    if isinstance(value, set):
        return [_normalize_json(item) for item in sorted(value, key=str)]
    return value
