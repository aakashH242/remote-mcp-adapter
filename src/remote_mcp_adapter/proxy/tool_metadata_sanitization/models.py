"""Resolved policy models for tool metadata sanitization."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolMetadataSanitizationPolicy:
    """Resolved metadata-sanitization policy for one server."""

    mode: str
    normalize_unicode: bool
    remove_invisible_characters: bool
    max_tool_title_chars: int | None
    max_tool_description_chars: int | None
    max_schema_text_chars: int | None

    @property
    def enabled(self) -> bool:
        """Return whether metadata sanitization is active."""
        return self.mode != "off"

    @property
    def blocks_on_change(self) -> bool:
        """Return whether tools should be hidden instead of sanitized."""
        return self.mode == "block"


def resolve_tool_metadata_sanitization_policy(*, config=None, server=None) -> ToolMetadataSanitizationPolicy:
    """Resolve effective metadata-sanitization settings for one server.

    Args:
        config: Adapter config or compatible test double.
        server: Server config or compatible test double.

    Returns:
        Resolved policy with server override precedence.
    """
    core = getattr(getattr(config, "core", None), "tool_metadata_sanitization", None)
    server_policy = getattr(server, "tool_metadata_sanitization", None)

    mode = getattr(core, "mode", "off")
    normalize_unicode = bool(getattr(core, "normalize_unicode", True))
    remove_invisible = bool(getattr(core, "remove_invisible_characters", True))
    max_tool_title_chars = getattr(core, "max_tool_title_chars", 256)
    max_tool_description_chars = getattr(core, "max_tool_description_chars", 2000)
    max_schema_text_chars = getattr(core, "max_schema_text_chars", 1000)

    if server_policy is not None:
        if getattr(server_policy, "mode", None) is not None:
            mode = server_policy.mode
        if getattr(server_policy, "normalize_unicode", None) is not None:
            normalize_unicode = bool(server_policy.normalize_unicode)
        if getattr(server_policy, "remove_invisible_characters", None) is not None:
            remove_invisible = bool(server_policy.remove_invisible_characters)
        if getattr(server_policy, "max_tool_title_chars", None) is not None:
            max_tool_title_chars = int(server_policy.max_tool_title_chars)
        if getattr(server_policy, "max_tool_description_chars", None) is not None:
            max_tool_description_chars = int(server_policy.max_tool_description_chars)
        if getattr(server_policy, "max_schema_text_chars", None) is not None:
            max_schema_text_chars = int(server_policy.max_schema_text_chars)

    return ToolMetadataSanitizationPolicy(
        mode=str(mode),
        normalize_unicode=normalize_unicode,
        remove_invisible_characters=remove_invisible,
        max_tool_title_chars=max_tool_title_chars,
        max_tool_description_chars=max_tool_description_chars,
        max_schema_text_chars=max_schema_text_chars,
    )
