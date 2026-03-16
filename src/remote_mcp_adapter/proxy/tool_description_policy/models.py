"""Resolved policy models for forwarded tool-description handling."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolDescriptionPolicy:
    """Resolved description-handling policy for one server."""

    mode: str
    max_tool_description_chars: int | None
    max_schema_description_chars: int | None

    @property
    def enabled(self) -> bool:
        """Return whether description shaping is active."""
        return self.mode != "preserve"

    @property
    def strips(self) -> bool:
        """Return whether descriptions should be removed entirely."""
        return self.mode == "strip"


def resolve_tool_description_policy(*, config=None, server=None) -> ToolDescriptionPolicy:
    """Resolve effective description-policy settings for one server.

    Args:
        config: Adapter config or compatible test double.
        server: Server config or compatible test double.

    Returns:
        Resolved policy with server override precedence.
    """
    core = getattr(getattr(config, "core", None), "tool_description_policy", None)
    server_policy = getattr(server, "tool_description_policy", None)

    mode = getattr(core, "mode", "preserve")
    max_tool_description_chars = getattr(core, "max_tool_description_chars", 280)
    max_schema_description_chars = getattr(core, "max_schema_description_chars", 280)

    if server_policy is not None:
        if getattr(server_policy, "mode", None) is not None:
            mode = server_policy.mode
        if getattr(server_policy, "max_tool_description_chars", None) is not None:
            max_tool_description_chars = int(server_policy.max_tool_description_chars)
        if getattr(server_policy, "max_schema_description_chars", None) is not None:
            max_schema_description_chars = int(server_policy.max_schema_description_chars)

    return ToolDescriptionPolicy(
        mode=str(mode),
        max_tool_description_chars=max_tool_description_chars,
        max_schema_description_chars=max_schema_description_chars,
    )
