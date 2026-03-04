"""Artifact producer adapter implementation."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
from pathlib import Path
import re
from typing import Any, Callable, Literal

from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent, TextContent, TextResourceContents

from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context
from fastmcp.tools.tool import ToolResult

from ..constants import ARTIFACT_PATH_PREFIX
from ..config import ArtifactProducerAdapterConfig
from ..core.storage.mime_types import detect_mime_type
from ..core.storage.store import SessionStore
from ..core.storage.write_policy import copy_file_with_policy, write_bytes_with_policy
from .raw_output import build_raw_artifact_content_block
from .upstream_call import call_upstream_tool

ClientFactory = Callable[[], Any]
logger = logging.getLogger(__name__)
DEFAULT_PATH_REGEXES = [
    r"(/(?:[^/\s]+/)*[^/\s]+)",
]
ARTIFACTS_SUFFIX_MARKER = ARTIFACT_PATH_PREFIX
UPLOADS_SUFFIX_MARKER = "/uploads/"


def _decode_base64(data: str) -> bytes:
    """Decode a base64 string to raw bytes.

    Args:
        data: Base64-encoded string.

    Returns:
        Decoded raw bytes.
    """
    return base64.b64decode(data)


async def _path_exists(path: Path) -> bool:
    """Check filesystem existence without blocking the event loop.

    Args:
        path: Filesystem path to check.

    Returns:
        True if the path exists on disk.
    """
    return await asyncio.to_thread(path.exists)


def _get_nested(data: Any, dotted_path: str | None) -> Any:
    """Walk a dotted-key path into a nested dict, returning None on any missing step.

    Args:
        data: Nested dict (or any value) to traverse.
        dotted_path: Dot-separated key path (e.g. ``"a.b.c"``), or None.

    Returns:
        The value at the final key, or None if any segment is missing.
    """
    if dotted_path is None:
        return None
    current = data
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _extract_text_payload(result: ToolResult) -> str:
    """Join all TextContent blocks from the result into a single string.

    Args:
        result: Upstream tool result containing content blocks.

    Returns:
        Newline-joined text extracted from all ``TextContent`` blocks.
    """
    text_chunks: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            text_chunks.append(block.text)
            continue
        if isinstance(block, dict):
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text_chunks.append(block["text"])
    return "\n".join(text_chunks)


def _select_sibling_variant(target_path: Path) -> Path | None:
    """Find the most recently modified sibling file in the same directory.

    Prefers siblings sharing the same stem as *target_path*. Falls back to
    any sibling file, sorted by modification time descending.

    Args:
        target_path: Expected artifact output path.

    Returns:
        Path to the best sibling candidate, or None if none found.
    """
    try:
        candidates = [entry for entry in target_path.parent.iterdir() if entry.is_file() and entry != target_path]
    except OSError:
        return None
    if not candidates:
        return None

    same_stem = [path for path in candidates if path.stem == target_path.stem]
    ranked = same_stem or candidates
    ranked.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return ranked[0]


def _select_descendant_variant(target_path: Path) -> Path | None:
    """Find the most recently modified file anywhere under the parent directory.

    Args:
        target_path: Expected artifact output path.

    Returns:
        Path to the most recent descendant file, or None if none found.
    """
    try:
        candidates = [entry for entry in target_path.parent.rglob("*") if entry.is_file() and entry != target_path]
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


async def _materialize_from_sibling_variant(
    *,
    target_path: Path,
    atomic_writes: bool,
    lock_mode: Literal["none", "process", "file", "redis"],
) -> bool:
    """Copy a sibling or descendant variant to *target_path* if it is missing.

    Args:
        target_path: Desired artifact output path.
        atomic_writes: Whether to use atomic write semantics.
        lock_mode: Concurrency lock strategy for the write.

    Returns:
        True if *target_path* exists after the operation.
    """
    if await _path_exists(target_path):
        return True
    sibling_variant = await asyncio.to_thread(_select_sibling_variant, target_path)
    if sibling_variant is None:
        sibling_variant = await asyncio.to_thread(_select_descendant_variant, target_path)
    if sibling_variant is None:
        return False
    await copy_file_with_policy(
        source_path=sibling_variant,
        target_path=target_path,
        atomic_writes=atomic_writes,
        lock_mode=lock_mode,
    )
    if sibling_variant.parent == target_path.parent:
        with contextlib.suppress(OSError):
            await asyncio.to_thread(sibling_variant.unlink, missing_ok=True)
    return await _path_exists(target_path)


def _iter_string_values(value: Any):
    """Recursively yield all string values from a nested dict/list structure.

    Args:
        value: Scalar, dict, or list to traverse.

    Yields:
        Each string leaf encountered during depth-first traversal.
    """
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, dict):
        for nested in value.values():
            yield from _iter_string_values(nested)
        return
    if isinstance(value, list):
        for nested in value:
            yield from _iter_string_values(nested)


def _looks_path_like(value: str) -> bool:
    """Return True if *value* appears to contain a filesystem path.

    Args:
        value: String to inspect.

    Returns:
        True when the stripped string contains a forward slash.
    """
    candidate = value.strip()
    if not candidate:
        return False
    return "/" in candidate


def _extract_structured_fallback_path(result: ToolResult) -> str | None:
    """Search structured result content for the first path-like string value.

    Args:
        result: Upstream tool result to inspect.

    Returns:
        First string value that looks like a filesystem path, or None.
    """
    structured = result.structured_content
    for item in _iter_string_values(structured):
        if _looks_path_like(item):
            return item
    return None


async def _extract_embedded_bytes(result: ToolResult) -> tuple[bytes, str | None] | None:
    """Return the first image or embedded blob from the result content, decoded from base64.

    Args:
        result: Upstream tool result whose content blocks are inspected.

    Returns:
        Tuple of ``(raw_bytes, mime_type)`` for the first embedded
        image/blob/text resource found, or None if no embeds exist.
    """
    for block in result.content:
        if isinstance(block, ImageContent):
            return await asyncio.to_thread(_decode_base64, block.data), "image/png"
        if isinstance(block, EmbeddedResource):
            if isinstance(block.resource, BlobResourceContents):
                return await asyncio.to_thread(_decode_base64, block.resource.blob), block.resource.mimeType
            if isinstance(block.resource, TextResourceContents):
                return block.resource.text.encode("utf-8"), block.resource.mimeType
        if isinstance(block, dict):
            block_type = block.get("type")
            if block_type == "image" and isinstance(block.get("data"), str):
                return await asyncio.to_thread(_decode_base64, block["data"]), block.get("mimeType")
            if block_type != "resource":
                continue
            resource = block.get("resource")
            if not isinstance(resource, dict):
                continue
            resource_type = resource.get("type")
            if resource_type == "blob" and isinstance(resource.get("blob"), str):
                return await asyncio.to_thread(_decode_base64, resource["blob"]), resource.get("mimeType")
            if resource_type == "text" and isinstance(resource.get("text"), str):
                return resource["text"].encode("utf-8"), resource.get("mimeType")
    return None


def _safe_name_from_argument(value: Any) -> str | None:
    """Extract the filename component from a path-like argument value, or return None.

    Args:
        value: Arbitrary argument value (only strings are processed).

    Returns:
        The trailing filename component, or None for non-string / blank values.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).name


def _extract_locator_path(result: ToolResult, adapter: ArtifactProducerAdapterConfig) -> str | None:
    """Extract the artifact file path from the tool result using the configured locator mode.

    Args:
        result: Upstream tool result to search.
        adapter: Artifact producer adapter configuration carrying locator settings.

    Returns:
        Extracted path string, or None when the locator mode yields no match.
    """
    mode = adapter.output_locator.mode
    if mode == "structured":
        value = _get_nested(result.structured_content, adapter.output_locator.output_path_key)
        return value if isinstance(value, str) else None
    if mode == "regex":
        payload = _extract_text_payload(result)
        patterns = adapter.output_locator.output_path_regexes or DEFAULT_PATH_REGEXES
        for pattern in patterns:
            match = re.search(pattern, payload)
            if match:
                if match.groups():
                    return match.group(1)
                return match.group(0)
    return None


def _normalize_locator_path(raw_path: str) -> Path:
    """Normalize tool-emitted locator paths.

    Strips surrounding whitespace, quotes, and trailing punctuation before
    converting to a ``Path``.

    Args:
        raw_path: Raw path string emitted by the upstream tool.

    Returns:
        Cleaned ``Path`` instance.
    """
    candidate = raw_path.strip().strip("\"'").rstrip(".,;:)]}>")
    return Path(candidate)


def _storage_suffix_candidate(raw_path: str, storage_root: Path, marker: str) -> Path | None:
    """Extract the portion of *raw_path* after *marker* and re-root it under *storage_root*.

    Args:
        raw_path: Raw locator path from the upstream tool.
        storage_root: Absolute storage root directory.
        marker: Path segment marker (e.g. ``/artifacts/``).

    Returns:
        Re-rooted candidate path, or None if *marker* is absent or suffix is empty.
    """
    normalized = raw_path.replace("\\", "/")
    if marker not in normalized:
        return None
    suffix = normalized.split(marker, 1)[1].lstrip("/")
    if not suffix:
        return None
    return storage_root / marker.strip("/") / Path(suffix)


def _iter_locator_candidates(raw_path: str, storage_root: Path) -> list[Path]:
    """Build a de-duplicated list of candidate paths for artifact locator resolution.

    Includes the normalized literal path plus any re-rooted variants derived
    from ``/artifacts/`` and ``/uploads/`` markers.

    Args:
        raw_path: Raw locator path from the upstream tool.
        storage_root: Absolute storage root directory.

    Returns:
        Ordered list of unique candidate ``Path`` objects.
    """
    candidates = [_normalize_locator_path(raw_path)]

    artifacts_candidate = _storage_suffix_candidate(raw_path, storage_root, ARTIFACTS_SUFFIX_MARKER)
    if artifacts_candidate is not None:
        candidates.append(artifacts_candidate)
    uploads_candidate = _storage_suffix_candidate(raw_path, storage_root, UPLOADS_SUFFIX_MARKER)
    if uploads_candidate is not None:
        candidates.append(uploads_candidate)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


async def _resolve_locator_source(
    *,
    raw_path: str,
    storage_root: Path,
    locator_policy: Literal["storage_only", "allow_configured_roots"],
    locator_allowed_roots: list[str],
) -> Path | None:
    """Resolve a raw locator path to an existing, policy-allowed source file.

    Iterates over candidate paths and returns the first that exists on disk
    and falls within the allowed storage roots.

    Args:
        raw_path: Raw locator path from the upstream tool.
        storage_root: Absolute storage root directory.
        locator_policy: Whether to restrict resolution to the storage root only.
        locator_allowed_roots: Additional allowed root directories when
            *locator_policy* is ``allow_configured_roots``.

    Returns:
        Resolved ``Path`` if a valid, existing source is found, else None.
    """
    for candidate in _iter_locator_candidates(raw_path, storage_root):
        with contextlib.suppress(ToolError):
            source_path = _ensure_within_storage(
                candidate,
                storage_root,
                locator_policy,
                locator_allowed_roots,
            )
            if await _path_exists(source_path):
                return source_path
    return None


async def _materialize_from_embedded(
    *,
    result: ToolResult,
    target_path: Path,
    atomic_writes: bool,
    lock_mode: Literal["none", "process", "file", "redis"],
) -> bool:
    """Write embedded binary content from the result to *target_path*.

    Args:
        result: Upstream tool result to extract embedded bytes from.
        target_path: Destination file path.
        atomic_writes: Whether to use atomic write semantics.
        lock_mode: Concurrency lock strategy for the write.

    Returns:
        True if *target_path* exists after the write attempt.
    """
    extracted = await _extract_embedded_bytes(result)
    if not extracted:
        return False
    await write_bytes_with_policy(
        target_path=target_path,
        data=extracted[0],
        atomic_writes=atomic_writes,
        lock_mode=lock_mode,
    )
    return await _path_exists(target_path)


def _artifact_uri(uri_scheme: str, session_id: str, artifact_id: str, filename: str) -> str:
    """Build the canonical artifact URI from its components.

    Args:
        uri_scheme: URI scheme prefix (e.g. ``"artifact://"``).
        session_id: Owning session identifier.
        artifact_id: Unique artifact identifier.
        filename: Human-readable filename.

    Returns:
        Fully-qualified artifact URI string.
    """
    return f"{uri_scheme}sessions/{session_id}/{artifact_id}/{filename}"


def _ensure_within_storage(
    path: Path,
    storage_root: Path,
    locator_policy: Literal["storage_only", "allow_configured_roots"],
    locator_allowed_roots: list[str],
) -> Path:
    """Resolve and validate that path falls within an allowed root.

    Args:
        path: Path to validate.
        storage_root: Primary allowed storage root.
        locator_policy: Whether to also allow configured external roots.
        locator_allowed_roots: Additional allowed root directories when
            *locator_policy* is ``allow_configured_roots``.

    Returns:
        The resolved absolute path.

    Raises:
        ToolError: If the resolved path escapes all allowed roots.
    """
    normalized = _normalize_locator_path(str(path))
    resolved = normalized.resolve()
    allowed_roots = [storage_root.resolve()]
    if locator_policy == "allow_configured_roots":
        allowed_roots.extend(Path(root).resolve() for root in locator_allowed_roots)

    for root in allowed_roots:
        if resolved == root or root in resolved.parents:
            return resolved

    if locator_policy == "allow_configured_roots":
        raise ToolError(f"Artifact path is outside configured locator roots: {resolved}")
    raise ToolError(f"Artifact path escapes storage root: {resolved}")


async def handle_artifact_producer_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    context: Context | None,
    server_id: str,
    adapter: ArtifactProducerAdapterConfig,
    config_artifact_uri_scheme: str,
    store: SessionStore,
    client_factory: ClientFactory,
    tool_call_timeout_seconds: int | None,
    telemetry=None,
    allow_raw_output: bool,
    locator_policy: Literal["storage_only", "allow_configured_roots"],
    locator_allowed_roots: list[str],
    atomic_writes: bool,
    lock_mode: Literal["none", "process", "file", "redis"],
) -> ToolResult:
    """Persist tool-generated outputs and enrich result metadata with artifact URI.

    Orchestrates the full artifact lifecycle: call the upstream tool, locate or
    materialize the output file using the configured locator strategy, finalize
    artifact metadata, and inject an artifact URI into the returned result.

    Args:
        tool_name: Name of the upstream tool to invoke.
        arguments: Arguments dict forwarded to the upstream tool.
        context: Optional FastMCP context (falls back to ``get_context()``).
        server_id: Identifier of the upstream server.
        adapter: Artifact producer adapter configuration.
        config_artifact_uri_scheme: URI scheme for generated artifact URIs.
        store: Session store managing artifact state.
        client_factory: Factory callable returning an upstream MCP client.
        tool_call_timeout_seconds: Optional timeout for the upstream call.
        telemetry: Optional telemetry recorder.
        allow_raw_output: Whether to append raw artifact bytes to the result.
        locator_policy: Path resolution policy for locating output files.
        locator_allowed_roots: Additional allowed root directories.
        atomic_writes: Whether to use atomic write semantics.
        lock_mode: Concurrency lock strategy for writes.

    Returns:
        Enriched ``ToolResult`` with artifact metadata in ``meta["artifact"]``.

    Raises:
        ToolError: If the artifact cannot be located or materialized.
    """
    ctx = context or get_context()
    session_id = ctx.session_id
    args = dict(arguments or {})

    allocated: tuple[str, Path, str] | None = None
    forced_filename: str | None = None
    if adapter.output_path_argument:
        arg_value = args.get(adapter.output_path_argument)
        forced_filename = _safe_name_from_argument(arg_value)
        allocated = await store.allocate_artifact_path(
            server_id=server_id,
            session_id=session_id,
            filename=forced_filename,
            tool_name=tool_name,
            expose_as_resource=adapter.expose_as_resource,
        )
        args[adapter.output_path_argument] = str(allocated[1])

    result = await call_upstream_tool(
        client_factory=client_factory,
        tool_name=tool_name,
        arguments=args,
        timeout_seconds=tool_call_timeout_seconds,
        telemetry=telemetry,
        server_id=server_id,
    )

    if not adapter.persist:
        return result

    artifact_id: str | None = None
    target_path: Path | None = None
    last_locator_path: str | None = None
    last_structured_path: str | None = None

    if allocated is not None:
        artifact_id, target_path, _ = allocated
        if not await _path_exists(target_path):
            last_locator_path = _extract_locator_path(result, adapter)
            if last_locator_path:
                source_path = await _resolve_locator_source(
                    raw_path=last_locator_path,
                    storage_root=store.storage_root,
                    locator_policy=locator_policy,
                    locator_allowed_roots=locator_allowed_roots,
                )
                if source_path is not None:
                    await copy_file_with_policy(
                        source_path=source_path,
                        target_path=target_path,
                        atomic_writes=atomic_writes,
                        lock_mode=lock_mode,
                    )
            if not await _path_exists(target_path):
                last_structured_path = _extract_structured_fallback_path(result)
                if last_structured_path:
                    source_path = await _resolve_locator_source(
                        raw_path=last_structured_path,
                        storage_root=store.storage_root,
                        locator_policy=locator_policy,
                        locator_allowed_roots=locator_allowed_roots,
                    )
                    if source_path is not None:
                        await copy_file_with_policy(
                            source_path=source_path,
                            target_path=target_path,
                            atomic_writes=atomic_writes,
                            lock_mode=lock_mode,
                        )
            if not await _path_exists(target_path):
                await _materialize_from_embedded(
                    result=result,
                    target_path=target_path,
                    atomic_writes=atomic_writes,
                    lock_mode=lock_mode,
                )
            if not await _path_exists(target_path):
                await _materialize_from_sibling_variant(
                    target_path=target_path,
                    atomic_writes=atomic_writes,
                    lock_mode=lock_mode,
                )
    else:
        last_locator_path = _extract_locator_path(result, adapter)
        if last_locator_path:
            source_path = await _resolve_locator_source(
                raw_path=last_locator_path,
                storage_root=store.storage_root,
                locator_policy=locator_policy,
                locator_allowed_roots=locator_allowed_roots,
            )
            if source_path is not None:
                artifact_id, target_path, _ = await store.allocate_artifact_path(
                    server_id=server_id,
                    session_id=session_id,
                    filename=source_path.name,
                    tool_name=tool_name,
                    expose_as_resource=adapter.expose_as_resource,
                )
                await copy_file_with_policy(
                    source_path=source_path,
                    target_path=target_path,
                    atomic_writes=atomic_writes,
                    lock_mode=lock_mode,
                )
        elif adapter.output_locator.mode == "embedded":
            extracted = await _extract_embedded_bytes(result)
            if extracted:
                artifact_id, target_path, _ = await store.allocate_artifact_path(
                    server_id=server_id,
                    session_id=session_id,
                    filename=forced_filename or "artifact.bin",
                    tool_name=tool_name,
                    expose_as_resource=adapter.expose_as_resource,
                )
                await write_bytes_with_policy(
                    target_path=target_path,
                    data=extracted[0],
                    atomic_writes=atomic_writes,
                    lock_mode=lock_mode,
                )

    if artifact_id is None or target_path is None or not await _path_exists(target_path):
        if adapter.output_locator.mode == "none":
            return result
        text_preview = _extract_text_payload(result)[:500]
        structured_type = type(result.structured_content).__name__
        logger.warning(
            "Artifact materialization failed after upstream call "
            "(tool=%s session=%s allocated=%s mode=%s target=%s structured_type=%s text_preview=%r "
            "locator_path=%r structured_path=%r)",
            tool_name,
            session_id,
            allocated is not None,
            adapter.output_locator.mode,
            str(target_path) if target_path is not None else None,
            structured_type,
            text_preview,
            last_locator_path,
            last_structured_path,
        )
        raise ToolError(f"Could not locate or materialize artifact output for tool: {tool_name}")

    resolved_mime_type = await asyncio.to_thread(
        detect_mime_type,
        target_path,
        fallback=None,
    )
    record = await store.finalize_artifact(
        server_id=server_id,
        session_id=session_id,
        artifact_id=artifact_id,
        mime_type=resolved_mime_type,
    )
    artifact_uri = _artifact_uri(config_artifact_uri_scheme, session_id, record.artifact_id, record.filename)
    content_blocks = list(result.content)
    if allow_raw_output:
        content_blocks.append(
            await build_raw_artifact_content_block(
                artifact_uri=artifact_uri,
                artifact_path=record.abs_path,
                mime_type=resolved_mime_type,
            )
        )

    meta: dict[str, Any] = {}
    if isinstance(result.meta, dict):
        meta.update(result.meta)
    meta["artifact"] = {
        "artifact_uri": artifact_uri,
        "artifact_id": record.artifact_id,
        "filename": record.filename,
        "mime_type": resolved_mime_type,
        "size_bytes": record.size_bytes,
        "expose_as_resource": record.expose_as_resource,
    }
    return ToolResult(
        content=content_blocks,
        structured_content=result.structured_content,
        meta=meta,
    )
