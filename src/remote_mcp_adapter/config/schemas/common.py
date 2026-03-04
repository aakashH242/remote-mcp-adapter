"""Shared schema helpers and common types."""

from __future__ import annotations

import re
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

_BYTE_SIZE_PATTERN = re.compile(r"^\s*(\d+)\s*([A-Za-z]{0,3})\s*$")
_BYTE_MULTIPLIERS = {
    "": 1,
    "b": 1,
    "k": 1000,
    "kb": 1000,
    "ki": 1024,
    "kib": 1024,
    "m": 1000**2,
    "mb": 1000**2,
    "mi": 1024**2,
    "mib": 1024**2,
    "g": 1000**3,
    "gb": 1000**3,
    "gi": 1024**3,
    "gib": 1024**3,
    "t": 1000**4,
    "tb": 1000**4,
    "ti": 1024**4,
    "tib": 1024**4,
}

StorageLockMode = Literal["none", "process", "file", "redis", "auto"]
EffectiveStorageLockMode = Literal["none", "process", "file", "redis"]
WritePolicyLockMode = Literal["none", "process", "file", "redis"]


class ToolDefaults(BaseModel):
    """Tool call behavior defaults."""

    model_config = ConfigDict(extra="forbid")

    tool_call_timeout_seconds: int | None = Field(default=None, gt=0)
    allow_raw_output: bool | None = None


def normalize_path(value: str, field_name: str) -> str:
    """Ensure *value* is a normalised absolute path with no trailing slash.

    Args:
        value: Raw path string.
        field_name: Configuration field name for error messages.
    """
    path = value.strip()
    if not path:
        raise ValueError(f"{field_name} cannot be blank")
    if not path.startswith("/"):
        path = f"/{path}"
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def parse_byte_size(value: int | str | None, field_name: str) -> int | None:
    """Parse a human-readable byte-size string or integer to a plain int.

    Args:
        value: Raw byte size value (integer, string like ``10MB``, or None).
        field_name: Configuration field name for error messages.
    """
    if value is None:
        return None
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{field_name} must be >= 0")
        return value
    match = _BYTE_SIZE_PATTERN.fullmatch(value)
    if not match:
        raise ValueError(f"Invalid byte size for {field_name}: {value}")
    magnitude = int(match.group(1))
    unit = match.group(2).lower()
    if unit not in _BYTE_MULTIPLIERS:
        raise ValueError(f"Unsupported unit for {field_name}: {unit}")
    return magnitude * _BYTE_MULTIPLIERS[unit]
