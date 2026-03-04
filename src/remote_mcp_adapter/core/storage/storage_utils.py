"""Filesystem/path helpers used by session store."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re

FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
UPLOAD_HANDLE_RE = re.compile(r"^upload://sessions/([^/]+)/([^/]+)(?:/[^/]+)?$")
ARTIFACT_URI_RE = re.compile(r"^artifact://sessions/([^/]+)/([^/]+)(?:/[^/]+)?$")


def sanitize_filename(name: str | None, *, default_name: str, default_ext: str | None = None) -> str:
    """Return a filesystem-safe filename preserving extension when possible.

    Args:
        name: Raw filename input.
        default_name: Fallback name when input is empty.
        default_ext: Default extension to append when missing.
    """
    raw = Path(name or default_name).name
    sanitized = FILENAME_SAFE_RE.sub("_", raw).strip("._")
    if not sanitized:
        sanitized = default_name
    if default_ext and not Path(sanitized).suffix:
        ext = default_ext if default_ext.startswith(".") else f".{default_ext}"
        sanitized = f"{sanitized}{ext}"
    return sanitized


def ensure_within_base(path: Path, base: Path) -> Path:
    """Resolve ``path`` and assert it remains within ``base``.

    Args:
        path: Path to resolve.
        base: Base directory that must contain the path.

    Raises:
        ValueError: If the resolved path escapes the base.
    """
    resolved_base = base.resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_base or resolved_base in resolved_path.parents:
        return resolved_path
    raise ValueError(f"Path escapes configured storage root: {resolved_path}")


def sha256_file(path: Path) -> str:
    """Compute SHA256 hex digest for file at ``path``.

    Args:
        path: Path to the file.
    """
    hasher = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def parse_session_scoped_uri(uri: str, scheme: str) -> tuple[str, str]:
    """Parse ``<scheme>sessions/<session>/<id>`` URI and return ``(session, id)``.

    Args:
        uri: Full URI string to parse.
        scheme: Expected URI scheme prefix.

    Raises:
        ValueError: If the URI is malformed or does not match the scheme.
    """
    normalized_scheme = scheme.strip().lower()
    if not normalized_scheme.endswith("://"):
        raise ValueError("URI scheme must end with '://'.")
    if not uri.startswith(normalized_scheme):
        raise ValueError(f"URI must start with {normalized_scheme}")
    remainder = uri[len(normalized_scheme) :]
    parts = remainder.split("/")
    if len(parts) < 3 or parts[0] != "sessions":
        raise ValueError("Invalid session-scoped URI format.")
    return parts[1], parts[2]
