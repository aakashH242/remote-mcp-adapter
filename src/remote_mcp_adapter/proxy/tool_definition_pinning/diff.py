"""Diffing helpers for pinned tool-definition baselines."""

from __future__ import annotations

from ...core.repo.records import ToolDefinitionBaseline, ToolDefinitionSnapshot
from .models import ToolDefinitionDriftResult


def compare_tool_catalogs(
    *,
    baseline: ToolDefinitionBaseline,
    current: dict[str, ToolDefinitionSnapshot],
) -> ToolDefinitionDriftResult:
    """Compare the current tool catalog to the pinned baseline.

    Args:
        baseline: Previously pinned baseline snapshots.
        current: Current canonical snapshots keyed by tool name.

    Returns:
        Drift comparison result with changed/new/removed/unchanged partitions.
    """
    baseline_names = set(baseline.tools)
    current_names = set(current)

    changed_tools: list[str] = []
    unchanged_tools: list[str] = []
    changed_fields: dict[str, tuple[str, ...]] = {}

    for tool_name in sorted(baseline_names & current_names):
        baseline_snapshot = baseline.tools[tool_name]
        current_snapshot = current[tool_name]
        if baseline_snapshot.canonical_hash == current_snapshot.canonical_hash:
            unchanged_tools.append(tool_name)
            continue
        changed_tools.append(tool_name)
        changed_fields[tool_name] = differing_top_level_fields(
            baseline=baseline_snapshot,
            current=current_snapshot,
        )

    new_tools = tuple(sorted(current_names - baseline_names))
    removed_tools = tuple(sorted(baseline_names - current_names))
    changed_tools_tuple = tuple(changed_tools)
    unchanged_tools_tuple = tuple(unchanged_tools)

    return ToolDefinitionDriftResult(
        changed_tools=changed_tools_tuple,
        new_tools=new_tools,
        removed_tools=removed_tools,
        unchanged_tools=unchanged_tools_tuple,
        changed_fields=changed_fields,
        preview=build_drift_preview(
            changed_tools=changed_tools_tuple,
            new_tools=new_tools,
            removed_tools=removed_tools,
            changed_fields=changed_fields,
        ),
    )


def differing_top_level_fields(
    *,
    baseline: ToolDefinitionSnapshot,
    current: ToolDefinitionSnapshot,
) -> tuple[str, ...]:
    """Return changed top-level canonical fields for one tool.

    Args:
        baseline: Pinned tool snapshot.
        current: Current tool snapshot.

    Returns:
        Sorted tuple of changed top-level field names.
    """
    changed = [
        field_name
        for field_name in sorted(set(baseline.payload) | set(current.payload))
        if baseline.payload.get(field_name) != current.payload.get(field_name)
    ]
    return tuple(changed)


def build_drift_preview(
    *,
    changed_tools: tuple[str, ...],
    new_tools: tuple[str, ...],
    removed_tools: tuple[str, ...],
    changed_fields: dict[str, tuple[str, ...]],
) -> str | None:
    """Build a concise human-readable drift preview string.

    Args:
        changed_tools: Names of changed tools.
        new_tools: Names of newly added tools.
        removed_tools: Names of removed tools.
        changed_fields: Changed top-level fields by tool.

    Returns:
        Concise preview string or ``None`` when there is no drift.
    """
    preview_parts: list[str] = []
    if changed_tools:
        changed_descriptions = []
        for tool_name in changed_tools:
            fields = ",".join(changed_fields.get(tool_name, ()))
            if fields:
                changed_descriptions.append(f"{tool_name}[{fields}]")
            else:
                changed_descriptions.append(tool_name)
        preview_parts.append(f"changed={', '.join(changed_descriptions)}")
    if new_tools:
        preview_parts.append(f"new={', '.join(new_tools)}")
    if removed_tools:
        preview_parts.append(f"removed={', '.join(removed_tools)}")
    if not preview_parts:
        return None
    return "; ".join(preview_parts)
