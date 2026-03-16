"""Schema metadata sanitization helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .text import SanitizedText, canonicalize_metadata_text, sanitize_metadata_text

_TEXTUAL_SCHEMA_KEYS = frozenset({"title", "description"})


@dataclass(frozen=True, slots=True)
class SanitizedSchema:
    """One sanitized schema result."""

    value: Any
    modified_fields: tuple[str, ...]

    @property
    def modified(self) -> bool:
        """Return whether any schema text field changed."""
        return bool(self.modified_fields)


def sanitize_schema_metadata(
    value: Any,
    *,
    normalize_unicode: bool,
    remove_invisible_characters: bool,
    max_chars: int | None,
) -> SanitizedSchema:
    """Sanitize model-visible schema text fields recursively.

    Args:
        value: Raw schema-like structure.
        normalize_unicode: Whether to apply Unicode normalization.
        remove_invisible_characters: Whether to strip invisible format chars.
        max_chars: Optional max length for textual schema fields.

    Returns:
        Sanitized schema value and changed field paths.
    """
    modified_fields: list[str] = []
    sanitized = _sanitize_schema_value(
        value,
        path="$",
        normalize_unicode=normalize_unicode,
        remove_invisible_characters=remove_invisible_characters,
        max_chars=max_chars,
        modified_fields=modified_fields,
    )
    return SanitizedSchema(value=sanitized, modified_fields=tuple(modified_fields))


def canonicalize_schema_metadata(value: Any) -> Any:
    """Canonicalize schema title/description text while sorting JSON keys.

    Args:
        value: Raw schema-like value.

    Returns:
        Deterministically ordered JSON-friendly structure.
    """
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        canonical: dict[str, Any] = {}
        for key in sorted(value):
            normalized_key = str(key)
            child = value[key]
            if normalized_key in _TEXTUAL_SCHEMA_KEYS and isinstance(child, str):
                canonical[normalized_key] = canonicalize_metadata_text(child)
                continue
            canonical[normalized_key] = canonicalize_schema_metadata(child)
        return canonical
    if isinstance(value, list):
        return [canonicalize_schema_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [canonicalize_schema_metadata(item) for item in value]
    if isinstance(value, set):
        return [canonicalize_schema_metadata(item) for item in sorted(value, key=str)]
    return value


def _sanitize_schema_value(
    value: Any,
    *,
    path: str,
    normalize_unicode: bool,
    remove_invisible_characters: bool,
    max_chars: int | None,
    modified_fields: list[str],
) -> Any:
    """Recursively sanitize one schema-like value.

    Args:
        value: Raw schema-like value.
        path: Current JSON-style path.
        normalize_unicode: Whether to normalize Unicode.
        remove_invisible_characters: Whether to strip invisible chars.
        max_chars: Optional per-field max length.
        modified_fields: Collector for changed field paths.

    Returns:
        Sanitized value.
    """
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, child in value.items():
            normalized_key = str(key)
            child_path = f"{path}.{normalized_key}"
            if normalized_key in _TEXTUAL_SCHEMA_KEYS and isinstance(child, str):
                text_result = sanitize_metadata_text(
                    child,
                    normalize_unicode=normalize_unicode,
                    remove_invisible_characters=remove_invisible_characters,
                    max_chars=max_chars,
                )
                sanitized[normalized_key] = text_result.value
                _record_schema_modification(
                    text_result=text_result,
                    field_path=child_path,
                    modified_fields=modified_fields,
                )
                continue
            sanitized[normalized_key] = _sanitize_schema_value(
                child,
                path=child_path,
                normalize_unicode=normalize_unicode,
                remove_invisible_characters=remove_invisible_characters,
                max_chars=max_chars,
                modified_fields=modified_fields,
            )
        return sanitized
    if isinstance(value, list):
        return [
            _sanitize_schema_value(
                child,
                path=f"{path}[{index}]",
                normalize_unicode=normalize_unicode,
                remove_invisible_characters=remove_invisible_characters,
                max_chars=max_chars,
                modified_fields=modified_fields,
            )
            for index, child in enumerate(value)
        ]
    if isinstance(value, tuple):
        return [
            _sanitize_schema_value(
                child,
                path=f"{path}[{index}]",
                normalize_unicode=normalize_unicode,
                remove_invisible_characters=remove_invisible_characters,
                max_chars=max_chars,
                modified_fields=modified_fields,
            )
            for index, child in enumerate(value)
        ]
    if isinstance(value, set):
        return [
            _sanitize_schema_value(
                child,
                path=f"{path}[{index}]",
                normalize_unicode=normalize_unicode,
                remove_invisible_characters=remove_invisible_characters,
                max_chars=max_chars,
                modified_fields=modified_fields,
            )
            for index, child in enumerate(sorted(value, key=str))
        ]
    return value


def _record_schema_modification(
    *,
    text_result: SanitizedText,
    field_path: str,
    modified_fields: list[str],
) -> None:
    """Record one changed schema field path when sanitization modified it.

    Args:
        text_result: Sanitized text result for the field.
        field_path: JSON-style field path.
        modified_fields: Collector for changed paths.
    """
    if text_result.modified:
        modified_fields.append(field_path)
