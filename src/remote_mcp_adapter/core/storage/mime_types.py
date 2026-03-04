"""MIME type detection helpers for persisted files."""

from __future__ import annotations

import mimetypes
from pathlib import Path


def _looks_like_text(data: bytes) -> bool:
    """Return True when data is non-null, valid UTF-8, and contains no null bytes.

    Args:
        data: File content bytes to inspect.
    """
    if not data:
        return True
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _mime_from_magic(data: bytes) -> str | None:
    """Return MIME type inferred from a known file magic signature, or None.

    Args:
        data: File content bytes to inspect.
    """
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def detect_mime_type(path: Path, fallback: str | None = None) -> str:
    """Infer MIME type from file magic, extension, then text sniffing.

    Args:
        path: Path to the file.
        fallback: MIME type to return when detection fails.
    """
    header = b""
    try:
        with path.open("rb") as handle:
            header = handle.read(512)
    except OSError:
        return fallback or "application/octet-stream"

    magic_mime = _mime_from_magic(header)
    if magic_mime is not None:
        return magic_mime

    guessed_mime = mimetypes.guess_type(path.name)[0]
    if guessed_mime:
        return guessed_mime

    if _looks_like_text(header):
        return "text/plain"

    return fallback or "application/octet-stream"
