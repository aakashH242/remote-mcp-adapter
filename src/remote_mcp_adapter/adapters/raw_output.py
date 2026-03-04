"""Helpers for embedding raw artifact data into tool output content."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from mcp.types import BlobResourceContents, EmbeddedResource, ImageContent, TextResourceContents


def _base64_ascii(data: bytes) -> str:
    """Encode bytes as a base64 ASCII string.

    Args:
        data: Raw bytes to encode.

    Returns:
        Base64-encoded ASCII string.
    """
    return base64.b64encode(data).decode("ascii")


async def build_raw_artifact_content_block(*, artifact_uri: str, artifact_path: Path, mime_type: str):
    """Create an MCP content block representing the raw artifact payload.

    Reads the artifact file from disk and returns the appropriate MCP content
    type (``ImageContent``, text ``EmbeddedResource``, or blob
    ``EmbeddedResource``) based on the MIME type.

    Args:
        artifact_uri: Canonical URI identifying the artifact.
        artifact_path: Absolute filesystem path to the artifact file.
        mime_type: MIME type of the artifact (determines content block type).

    Returns:
        An ``ImageContent`` for images, a text ``EmbeddedResource`` for text
        types, or a blob ``EmbeddedResource`` for all other MIME types.
    """
    data = await asyncio.to_thread(artifact_path.read_bytes)
    if mime_type.startswith("image/"):
        return ImageContent(
            type="image",
            data=await asyncio.to_thread(_base64_ascii, data),
            mimeType=mime_type,
            _meta={"size_bytes": len(data)},
        )
    if mime_type.startswith("text/"):
        return EmbeddedResource(
            type="resource",
            resource=TextResourceContents(
                uri=artifact_uri,
                text=data.decode("utf-8", errors="replace"),
                mimeType=mime_type,
                _meta={"size_bytes": len(data)},
            ),
        )
    return EmbeddedResource(
        type="resource",
        resource=BlobResourceContents(
            uri=artifact_uri,
            blob=await asyncio.to_thread(_base64_ascii, data),
            mimeType=mime_type,
            _meta={"size_bytes": len(data)},
        ),
    )
