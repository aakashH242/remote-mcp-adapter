"""Catalog transform for model-visible tool metadata sanitization."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
from typing import Any

from fastmcp.server.transforms.catalog import CatalogTransform
from fastmcp.server.transforms import GetToolNext
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec
from mcp.types import ToolAnnotations

from .models import ToolMetadataSanitizationPolicy
from .schema import sanitize_schema_metadata
from .text import sanitize_metadata_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SanitizedTool:
    """One tool-sanitization result."""

    tool: Tool | None
    modified_fields: tuple[str, ...]
    blocked: bool

    @property
    def modified(self) -> bool:
        """Return whether the tool metadata changed."""
        return bool(self.modified_fields)


class ToolMetadataSanitizationTransform(CatalogTransform):
    """Sanitize model-visible tool metadata before forwarding it to clients."""

    def __init__(self, *, server_id: str, policy: ToolMetadataSanitizationPolicy) -> None:
        """Initialize the sanitization transform.

        Args:
            server_id: Configured upstream server identifier.
            policy: Effective sanitization policy for this server.
        """
        super().__init__()
        self._server_id = server_id
        self._policy = policy

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Sanitize tool metadata in the visible tool catalog.

        Args:
            tools: Tool catalog before later transforms run.

        Returns:
            Sanitized tool catalog with blocked tools removed when required.
        """
        if not self._policy.enabled:
            return tools

        sanitized_tools: list[Tool] = []
        for tool in tools:
            result = self._sanitize_tool(tool)
            self._log_tool_result(tool_name=tool.name, result=result, source="list_tools")
            if result.tool is not None:
                sanitized_tools.append(result.tool)
        return sanitized_tools

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        """Sanitize metadata on direct tool lookups too.

        Args:
            name: Requested tool name.
            call_next: Downstream lookup callable.
            version: Optional version filter.

        Returns:
            Sanitized tool or ``None`` when blocked or not found.
        """
        tool = await call_next(name, version=version)
        if not self._policy.enabled or tool is None:
            return tool
        result = self._sanitize_tool(tool)
        self._log_tool_result(tool_name=name, result=result, source="get_tool")
        return result.tool

    def _sanitize_tool(self, tool: Tool) -> SanitizedTool:
        """Sanitize one tool's client-visible metadata.

        Args:
            tool: Tool to sanitize.

        Returns:
            Sanitized tool result.
        """
        update: dict[str, Any] = {}
        modified_fields: list[str] = []

        title_result = sanitize_metadata_text(
            tool.title,
            normalize_unicode=self._policy.normalize_unicode,
            remove_invisible_characters=self._policy.remove_invisible_characters,
            max_chars=self._policy.max_tool_title_chars,
        )
        if title_result.modified:
            update["title"] = title_result.value
            modified_fields.extend(f"title:{reason}" for reason in title_result.reasons)

        description_result = sanitize_metadata_text(
            tool.description,
            normalize_unicode=self._policy.normalize_unicode,
            remove_invisible_characters=self._policy.remove_invisible_characters,
            max_chars=self._policy.max_tool_description_chars,
        )
        if description_result.modified:
            update["description"] = description_result.value
            modified_fields.extend(f"description:{reason}" for reason in description_result.reasons)

        annotations_result, annotation_fields = self._sanitize_annotations(tool.annotations)
        if annotations_result is not None:
            update["annotations"] = annotations_result
            modified_fields.extend(annotation_fields)

        parameters_result = sanitize_schema_metadata(
            tool.parameters,
            normalize_unicode=self._policy.normalize_unicode,
            remove_invisible_characters=self._policy.remove_invisible_characters,
            max_chars=self._policy.max_schema_text_chars,
        )
        if parameters_result.modified:
            update["parameters"] = parameters_result.value
            modified_fields.extend(parameters_result.modified_fields)

        output_schema_result = sanitize_schema_metadata(
            tool.output_schema,
            normalize_unicode=self._policy.normalize_unicode,
            remove_invisible_characters=self._policy.remove_invisible_characters,
            max_chars=self._policy.max_schema_text_chars,
        )
        if output_schema_result.modified:
            update["output_schema"] = output_schema_result.value
            modified_fields.extend(output_schema_result.modified_fields)

        if not modified_fields:
            return SanitizedTool(tool=tool, modified_fields=(), blocked=False)
        if self._policy.blocks_on_change:
            return SanitizedTool(tool=None, modified_fields=tuple(modified_fields), blocked=True)
        return SanitizedTool(
            tool=tool.model_copy(update=update),
            modified_fields=tuple(modified_fields),
            blocked=False,
        )

    def _log_tool_result(self, *, tool_name: str, result: SanitizedTool, source: str) -> None:
        """Emit a bounded structured log when sanitization changed a tool.

        Args:
            tool_name: Tool name being processed.
            result: Sanitization result.
            source: Call site such as ``list_tools`` or ``get_tool``.
        """
        if not result.modified:
            return
        level = logging.WARNING if result.blocked else logging.INFO
        logger.log(
            level,
            "Tool metadata sanitization modified visible metadata",
            extra={
                "server_id": self._server_id,
                "tool_name": tool_name,
                "mode": self._policy.mode,
                "source": source,
                "blocked": result.blocked,
                "modified_fields": result.modified_fields,
            },
        )

    def _sanitize_annotations(
        self,
        annotations: ToolAnnotations | None,
    ) -> tuple[ToolAnnotations | None, tuple[str, ...]]:
        """Sanitize model-visible annotation fields.

        Args:
            annotations: Tool annotations before forwarding.

        Returns:
            Tuple of updated annotations and changed field markers.
        """
        if annotations is None:
            return None, ()
        title_result = sanitize_metadata_text(
            annotations.title,
            normalize_unicode=self._policy.normalize_unicode,
            remove_invisible_characters=self._policy.remove_invisible_characters,
            max_chars=self._policy.max_tool_title_chars,
        )
        if not title_result.modified:
            return None, ()
        return (
            annotations.model_copy(update={"title": title_result.value}),
            tuple(f"annotations.title:{reason}" for reason in title_result.reasons),
        )
