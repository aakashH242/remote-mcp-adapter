"""Catalog transform for forwarded tool-description handling."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
from typing import Any

from fastmcp.server.transforms import GetToolNext
from fastmcp.server.transforms.catalog import CatalogTransform
from fastmcp.tools.tool import Tool
from fastmcp.utilities.versions import VersionSpec

from .models import ToolDescriptionPolicy
from .schema import apply_schema_description_policy
from .text import apply_description_policy

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DescribedTool:
    """One description-policy result."""

    tool: Tool
    modified_fields: tuple[str, ...]

    @property
    def modified(self) -> bool:
        """Return whether any visible description field changed."""
        return bool(self.modified_fields)


class ToolDescriptionPolicyTransform(CatalogTransform):
    """Shape forwarded tool descriptions before they reach the client."""

    def __init__(self, *, server_id: str, policy: ToolDescriptionPolicy) -> None:
        """Initialize the description-policy transform.

        Args:
            server_id: Configured upstream server identifier.
            policy: Effective description policy for this server.
        """
        super().__init__()
        self._server_id = server_id
        self._policy = policy

    async def transform_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        """Apply the description policy to the visible tool catalog.

        Args:
            tools: Tool catalog before later transforms run.

        Returns:
            Tool catalog after description shaping.
        """
        if not self._policy.enabled:
            return tools

        transformed_tools: list[Tool] = []
        for tool in tools:
            result = self._shape_tool(tool)
            self._log_tool_result(tool_name=tool.name, result=result, source="list_tools")
            transformed_tools.append(result.tool)
        return transformed_tools

    async def get_tool(
        self,
        name: str,
        call_next: GetToolNext,
        *,
        version: VersionSpec | None = None,
    ) -> Tool | None:
        """Apply the description policy on direct tool lookups too.

        Args:
            name: Requested tool name.
            call_next: Downstream lookup callable.
            version: Optional version filter.

        Returns:
            Tool after description shaping, or ``None`` when missing.
        """
        tool = await call_next(name, version=version)
        if not self._policy.enabled or tool is None:
            return tool
        result = self._shape_tool(tool)
        self._log_tool_result(tool_name=name, result=result, source="get_tool")
        return result.tool

    def _shape_tool(self, tool: Tool) -> DescribedTool:
        """Apply the description policy to one tool.

        Args:
            tool: Tool to update.

        Returns:
            Description-policy result for the tool.
        """
        update: dict[str, Any] = {}
        modified_fields: list[str] = []

        description_result = apply_description_policy(
            tool.description,
            mode=self._policy.mode,
            max_chars=self._policy.max_tool_description_chars,
        )
        if description_result.modified:
            update["description"] = description_result.value
            modified_fields.extend(f"description:{reason}" for reason in description_result.reasons)

        parameters_result = apply_schema_description_policy(
            tool.parameters,
            mode=self._policy.mode,
            max_chars=self._policy.max_schema_description_chars,
        )
        if parameters_result.modified:
            update["parameters"] = parameters_result.value
            modified_fields.extend(parameters_result.modified_fields)

        output_schema_result = apply_schema_description_policy(
            tool.output_schema,
            mode=self._policy.mode,
            max_chars=self._policy.max_schema_description_chars,
        )
        if output_schema_result.modified:
            update["output_schema"] = output_schema_result.value
            modified_fields.extend(output_schema_result.modified_fields)

        if not modified_fields:
            return DescribedTool(tool=tool, modified_fields=())
        return DescribedTool(
            tool=tool.model_copy(update=update),
            modified_fields=tuple(modified_fields),
        )

    def _log_tool_result(self, *, tool_name: str, result: DescribedTool, source: str) -> None:
        """Emit a bounded structured log when description shaping changed a tool.

        Args:
            tool_name: Tool name being processed.
            result: Description-policy result.
            source: Call site such as ``list_tools`` or ``get_tool``.
        """
        if not result.modified:
            return
        logger.info(
            "Tool description policy modified forwarded descriptions",
            extra={
                "server_id": self._server_id,
                "tool_name": tool_name,
                "mode": self._policy.mode,
                "source": source,
                "modified_fields": result.modified_fields,
            },
        )
