"""Schema description-handling helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .text import apply_description_policy


@dataclass(frozen=True, slots=True)
class DescriptionSchemaResult:
    """One schema description-shaping result."""

    value: Any
    modified_fields: tuple[str, ...]

    @property
    def modified(self) -> bool:
        """Return whether any schema description field changed."""
        return bool(self.modified_fields)


def apply_schema_description_policy(
    value: Any,
    *,
    mode: str,
    max_chars: int | None,
) -> DescriptionSchemaResult:
    """Apply description shaping to schema description fields recursively.

    Args:
        value: Raw schema-like structure.
        mode: Effective handling mode.
        max_chars: Optional truncate cap for schema descriptions.

    Returns:
        Updated schema and changed field markers.
    """
    modified_fields: list[str] = []
    shaped = _apply_schema_value(
        value,
        path="$",
        mode=mode,
        max_chars=max_chars,
        modified_fields=modified_fields,
    )
    return DescriptionSchemaResult(value=shaped, modified_fields=tuple(modified_fields))


def _apply_schema_value(
    value: Any,
    *,
    path: str,
    mode: str,
    max_chars: int | None,
    modified_fields: list[str],
) -> Any:
    """Recursively shape description text in one schema-like value.

    Args:
        value: Raw schema-like value.
        path: Current JSON-style path.
        mode: Effective handling mode.
        max_chars: Optional truncate cap.
        modified_fields: Collector for changed paths.

    Returns:
        Updated value.
    """
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(by_alias=True, exclude_none=True)
    if isinstance(value, dict):
        shaped: dict[str, Any] = {}
        for key, child in value.items():
            normalized_key = str(key)
            child_path = f"{path}.{normalized_key}"
            if normalized_key == "description" and isinstance(child, str):
                result = apply_description_policy(
                    child,
                    mode=mode,
                    max_chars=max_chars,
                )
                if result.modified:
                    modified_fields.extend(f"{child_path}:{reason}" for reason in result.reasons)
                if result.value is not None:
                    shaped[normalized_key] = result.value
                continue
            shaped[normalized_key] = _apply_schema_value(
                child,
                path=child_path,
                mode=mode,
                max_chars=max_chars,
                modified_fields=modified_fields,
            )
        return shaped
    if isinstance(value, list):
        return [
            _apply_schema_value(
                child,
                path=f"{path}[{index}]",
                mode=mode,
                max_chars=max_chars,
                modified_fields=modified_fields,
            )
            for index, child in enumerate(value)
        ]
    if isinstance(value, tuple):
        return [
            _apply_schema_value(
                child,
                path=f"{path}[{index}]",
                mode=mode,
                max_chars=max_chars,
                modified_fields=modified_fields,
            )
            for index, child in enumerate(value)
        ]
    if isinstance(value, set):
        return [
            _apply_schema_value(
                child,
                path=f"{path}[{index}]",
                mode=mode,
                max_chars=max_chars,
                modified_fields=modified_fields,
            )
            for index, child in enumerate(sorted(value, key=str))
        ]
    return value
