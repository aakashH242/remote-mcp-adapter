"""Helpers for forwarded tool-description shaping."""

from __future__ import annotations

from dataclasses import dataclass

from ..tool_metadata_sanitization.text import truncate_text_with_ellipsis


@dataclass(frozen=True, slots=True)
class DescriptionTextResult:
    """One description-shaping result."""

    value: str | None
    modified: bool
    reasons: tuple[str, ...]


def apply_description_policy(
    value: str | None,
    *,
    mode: str,
    max_chars: int | None,
) -> DescriptionTextResult:
    """Apply the configured description policy to one text field.

    Args:
        value: Original text value.
        mode: Effective handling mode.
        max_chars: Optional cap for truncate mode.

    Returns:
        Description result with modification reasons.
    """
    if value is None or mode == "preserve":
        return DescriptionTextResult(value=value, modified=False, reasons=())
    if mode == "strip":
        return DescriptionTextResult(
            value=None,
            modified=True,
            reasons=("stripped",),
        )
    if mode == "truncate" and max_chars is not None and len(value) > max_chars:
        return DescriptionTextResult(
            value=truncate_text_with_ellipsis(value, max_chars=max_chars),
            modified=True,
            reasons=("truncated",),
        )
    return DescriptionTextResult(value=value, modified=False, reasons=())
