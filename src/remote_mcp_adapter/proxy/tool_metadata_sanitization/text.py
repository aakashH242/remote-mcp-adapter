"""Text normalization helpers for model-visible tool metadata."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True, slots=True)
class SanitizedText:
    """One sanitized text result."""

    value: str | None
    modified: bool
    reasons: tuple[str, ...]


def canonicalize_metadata_text(value: str | None) -> str | None:
    """Return a deterministic normalized form for metadata text.

    Args:
        value: Raw metadata text.

    Returns:
        Canonicalized text or ``None`` when the input is ``None``.
    """
    if value is None:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Cf")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    return normalized


def sanitize_metadata_text(
    value: str | None,
    *,
    normalize_unicode: bool,
    remove_invisible_characters: bool,
    max_chars: int | None,
) -> SanitizedText:
    """Normalize and cap one piece of model-visible metadata text.

    Args:
        value: Raw metadata text.
        normalize_unicode: Whether to apply Unicode NFKC normalization.
        remove_invisible_characters: Whether to remove invisible format chars.
        max_chars: Optional character limit for the forwarded text.

    Returns:
        Sanitized text result with modification reasons.
    """
    if value is None:
        return SanitizedText(value=None, modified=False, reasons=())

    text = value
    reasons: list[str] = []
    if normalize_unicode:
        normalized = unicodedata.normalize("NFKC", text)
        if normalized != text:
            text = normalized
            reasons.append("unicode_normalized")

    if remove_invisible_characters:
        stripped = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
        if stripped != text:
            text = stripped
            reasons.append("invisible_characters_removed")

    if max_chars is not None and len(text) > max_chars:
        text = truncate_text_with_ellipsis(text, max_chars=max_chars)
        reasons.append("truncated")

    return SanitizedText(
        value=text,
        modified=bool(reasons),
        reasons=tuple(reasons),
    )


def truncate_text_with_ellipsis(value: str, *, max_chars: int) -> str:
    """Return *value* truncated to *max_chars* with a stable suffix.

    Args:
        value: Original text.
        max_chars: Maximum allowed character count.

    Returns:
        Truncated string.
    """
    if max_chars <= 3:
        return value[:max_chars]
    return f"{value[: max_chars - 3]}..."
