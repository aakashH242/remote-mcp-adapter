from __future__ import annotations

from types import SimpleNamespace

from fastmcp.exceptions import FastMCPError, ToolError
from fastmcp.tools.tool import Tool
import pytest

from remote_mcp_adapter.proxy.tool_definition_pinning import canonical as canon
from remote_mcp_adapter.proxy.tool_definition_pinning import diff as drift_diff
from remote_mcp_adapter.proxy.tool_definition_pinning.models import ToolDefinitionPinningPolicy
from remote_mcp_adapter.proxy.tool_definition_pinning.transform import ToolDefinitionPinningTransform
from remote_mcp_adapter.proxy.tool_definition_pinning.warnings import apply_catalog_warnings


class _Store:
    def __init__(self) -> None:
        self.baselines: dict[tuple[str, str], object] = {}
        self.summaries: dict[tuple[str, str], object] = {}
        self.terminal_reasons: dict[tuple[str, str], str] = {}

    async def get_tool_definition_baseline(self, server_id: str, session_id: str):
        return self.baselines.get((server_id, session_id))

    async def set_tool_definition_baseline(self, server_id: str, session_id: str, baseline) -> None:
        self.baselines[(server_id, session_id)] = baseline

    async def get_tool_definition_drift_summary(self, server_id: str, session_id: str):
        return self.summaries.get((server_id, session_id))

    async def set_tool_definition_drift_summary(self, server_id: str, session_id: str, summary) -> None:
        self.summaries[(server_id, session_id)] = summary

    async def clear_tool_definition_drift_summary(self, server_id: str, session_id: str) -> None:
        self.summaries.pop((server_id, session_id), None)

    async def get_terminal_session_reason(self, server_id: str, session_id: str):
        return self.terminal_reasons.get((server_id, session_id))

    async def invalidate_session(self, *, server_id: str, session_id: str, reason: str) -> None:
        self.terminal_reasons[(server_id, session_id)] = reason


def _tool(
    *,
    name: str = "tool_a",
    description: str = "Original description.",
    parameters: dict | None = None,
) -> Tool:
    async def _handler(path: str) -> str:
        return "ok"

    tool = Tool.from_function(fn=_handler, name=name, description=description)
    return tool.model_copy(
        update={
            "parameters": parameters
            or {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            }
        }
    )


def _session_context(session_id: str = "sess-1") -> SimpleNamespace:
    return SimpleNamespace(session_id=session_id)


def test_canonicalize_tool_ignores_warning_prefixes_and_key_order():
    warned_tool = _tool(
        description=(
            "WARNING: This tool definition changed after the session baseline was pinned.\n\n"
            "WARNING: Tool definitions changed during this adapter session. Drift detected: changed=tool_a[description]\n\n"
            "Original description."
        ),
        parameters={
            "required": ["path"],
            "properties": {"path": {"type": "string"}, "mode": {"type": "string"}},
            "type": "object",
        },
    )
    reordered_tool = _tool(
        description="Original description.",
        parameters={
            "type": "object",
            "properties": {"mode": {"type": "string"}, "path": {"type": "string"}},
            "required": ["path"],
        },
    )

    warned_snapshot = canon.canonicalize_tool(warned_tool)
    reordered_snapshot = canon.canonicalize_tool(reordered_tool)

    assert warned_snapshot.canonical_hash == reordered_snapshot.canonical_hash


def test_compare_tool_catalogs_reports_changed_new_and_removed_tools():
    baseline = SimpleNamespace(
        tools=canon.canonicalize_tools(
            (
                _tool(name="tool_a", description="A"),
                _tool(name="tool_b", description="B"),
            )
        )
    )
    current = canon.canonicalize_tools(
        (
            _tool(name="tool_a", description="A changed"),
            _tool(name="tool_c", description="C"),
        )
    )

    drift = drift_diff.compare_tool_catalogs(baseline=baseline, current=current)

    assert drift.changed_tools == ("tool_a",)
    assert drift.new_tools == ("tool_c",)
    assert drift.removed_tools == ("tool_b",)
    assert drift.changed_fields["tool_a"] == ("description",)


def test_apply_catalog_warnings_is_non_duplicating():
    tool = _tool()
    drift = SimpleNamespace(
        changed_tools=("tool_a",),
        new_tools=(),
        removed_tools=(),
        preview="changed=tool_a[description]",
    )

    once = apply_catalog_warnings(tools=[tool], drift=drift)
    twice = apply_catalog_warnings(tools=once, drift=drift)

    assert twice[0].description.count("WARNING: Tool definitions changed during this adapter session.") == 1
    assert twice[0].description.count(
        "WARNING: This tool definition changed after the session baseline was pinned."
    ) == 1


def test_build_session_warning_banner_without_preview_and_prepend_warning_edges():
    assert canon is not None
    assert drift_diff.build_drift_preview(changed_tools=(), new_tools=(), removed_tools=(), changed_fields={}) is None
    from remote_mcp_adapter.proxy.tool_definition_pinning.warnings import build_session_warning_banner, prepend_warning

    assert build_session_warning_banner(None).startswith("WARNING: Tool definitions changed during this adapter session.")
    assert prepend_warning(None, "WARNING") == "WARNING"
    assert prepend_warning("WARNING\n\nBody", "WARNING") == "WARNING\n\nBody"


def test_differing_top_level_fields_and_drift_preview_include_new_removed():
    baseline = canon.canonicalize_tool(_tool(name="tool_a", description="A"))
    current = canon.canonicalize_tool(
        _tool(
            name="tool_a",
            description="B",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "extra": {"type": "string"}},
                "required": ["path", "extra"],
            },
        )
    )

    changed_fields = drift_diff.differing_top_level_fields(baseline=baseline, current=current)
    preview = drift_diff.build_drift_preview(
        changed_tools=("tool_a",),
        new_tools=("tool_b",),
        removed_tools=("tool_c",),
        changed_fields={"tool_a": changed_fields},
    )

    assert "description" in changed_fields
    assert "inputSchema" in changed_fields
    assert preview is not None
    assert "new=tool_b" in preview
    assert "removed=tool_c" in preview


@pytest.mark.asyncio
async def test_transform_pins_first_visible_catalog(monkeypatch):
    store = _Store()
    transform = ToolDefinitionPinningTransform(
        server_id="playwright",
        session_store=store,
        policy=ToolDefinitionPinningPolicy(mode="warn", block_strategy="error"),
    )
    monkeypatch.setattr(
        "remote_mcp_adapter.proxy.tool_definition_pinning.transform.get_context",
        lambda: _session_context(),
    )

    tools = [_tool(name="tool_a"), _tool(name="tool_b")]
    result = await transform.transform_tools(tools)

    assert result == tools
    baseline = await store.get_tool_definition_baseline("playwright", "sess-1")
    assert set(baseline.tools) == {"tool_a", "tool_b"}


@pytest.mark.asyncio
async def test_transform_waits_for_wiring_readiness_before_pinning(monkeypatch):
    store = _Store()
    ready = False
    transform = ToolDefinitionPinningTransform(
        server_id="playwright",
        session_store=store,
        policy=ToolDefinitionPinningPolicy(mode="warn", block_strategy="error"),
        catalog_ready=lambda: ready,
    )
    monkeypatch.setattr(
        "remote_mcp_adapter.proxy.tool_definition_pinning.transform.get_context",
        lambda: _session_context(),
    )

    tools = [_tool(name="tool_a"), _tool(name="tool_b")]
    result_before_ready = await transform.transform_tools(tools)

    assert result_before_ready == tools
    assert await store.get_tool_definition_baseline("playwright", "sess-1") is None

    ready = True
    result_after_ready = await transform.transform_tools(tools)

    assert result_after_ready == tools
    baseline = await store.get_tool_definition_baseline("playwright", "sess-1")
    assert baseline is not None
    assert set(baseline.tools) == {"tool_a", "tool_b"}


@pytest.mark.asyncio
async def test_transform_warns_on_description_drift(monkeypatch):
    store = _Store()
    transform = ToolDefinitionPinningTransform(
        server_id="playwright",
        session_store=store,
        policy=ToolDefinitionPinningPolicy(mode="warn", block_strategy="error"),
    )
    monkeypatch.setattr(
        "remote_mcp_adapter.proxy.tool_definition_pinning.transform.get_context",
        lambda: _session_context(),
    )

    await transform.transform_tools([_tool(name="tool_a", description="Original")])
    warned = await transform.transform_tools([_tool(name="tool_a", description="Changed")])

    assert warned[0].description is not None
    assert "WARNING: Tool definitions changed during this adapter session." in warned[0].description
    assert "WARNING: This tool definition changed after the session baseline was pinned." in warned[0].description
    summary = await store.get_tool_definition_drift_summary("playwright", "sess-1")
    assert summary.changed_tools == ["tool_a"]


@pytest.mark.asyncio
async def test_transform_blocks_catalog_when_schema_changes(monkeypatch):
    store = _Store()
    transform = ToolDefinitionPinningTransform(
        server_id="playwright",
        session_store=store,
        policy=ToolDefinitionPinningPolicy(mode="block", block_strategy="error"),
    )
    monkeypatch.setattr(
        "remote_mcp_adapter.proxy.tool_definition_pinning.transform.get_context",
        lambda: _session_context(),
    )

    await transform.transform_tools([_tool(name="tool_a")])
    changed_tool = _tool(
        name="tool_a",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "mode": {"type": "string"}},
            "required": ["path", "mode"],
        },
    )

    with pytest.raises(FastMCPError, match="Tool definition drift detected"):
        await transform.transform_tools([changed_tool])
    assert store.terminal_reasons["playwright", "sess-1"].startswith(
        "Upstream tool catalog changed after this adapter session pinned its baseline."
    )


@pytest.mark.asyncio
async def test_transform_returns_baseline_subset_when_configured(monkeypatch):
    store = _Store()
    transform = ToolDefinitionPinningTransform(
        server_id="playwright",
        session_store=store,
        policy=ToolDefinitionPinningPolicy(mode="block", block_strategy="baseline_subset"),
    )
    monkeypatch.setattr(
        "remote_mcp_adapter.proxy.tool_definition_pinning.transform.get_context",
        lambda: _session_context(),
    )

    await transform.transform_tools([_tool(name="tool_a"), _tool(name="tool_b")])
    subset = await transform.transform_tools(
        [
            _tool(name="tool_a", description="changed"),
            _tool(name="tool_b"),
            _tool(name="tool_c"),
        ]
    )

    assert [tool.name for tool in subset] == ["tool_b"]


@pytest.mark.asyncio
async def test_get_tool_blocks_changed_tool_when_policy_is_error(monkeypatch):
    store = _Store()
    transform = ToolDefinitionPinningTransform(
        server_id="playwright",
        session_store=store,
        policy=ToolDefinitionPinningPolicy(mode="block", block_strategy="error"),
    )
    monkeypatch.setattr(
        "remote_mcp_adapter.proxy.tool_definition_pinning.transform.get_context",
        lambda: _session_context(),
    )

    await transform.transform_tools([_tool(name="tool_a", description="Original")])
    blocked = await transform.get_tool(
        "tool_a",
        lambda name, version=None: _return_changed_tool(name),
    )

    assert blocked is not None
    with pytest.raises(ToolError, match="current session was invalidated"):
        await blocked._run({"path": "x"})
    assert store.terminal_reasons["playwright", "sess-1"].startswith(
        "Upstream tool catalog changed after this adapter session pinned its baseline."
    )


@pytest.mark.asyncio
async def test_get_tool_hides_new_tool_in_baseline_subset_mode(monkeypatch):
    store = _Store()
    transform = ToolDefinitionPinningTransform(
        server_id="playwright",
        session_store=store,
        policy=ToolDefinitionPinningPolicy(mode="block", block_strategy="baseline_subset"),
    )
    monkeypatch.setattr(
        "remote_mcp_adapter.proxy.tool_definition_pinning.transform.get_context",
        lambda: _session_context(),
    )

    await transform.transform_tools([_tool(name="tool_a")])
    hidden = await transform.get_tool(
        "tool_b",
        _return_new_tool,
    )

    assert hidden is None


@pytest.mark.asyncio
async def test_transform_blocks_terminally_invalidated_session(monkeypatch):
    store = _Store()
    store.terminal_reasons["playwright", "sess-1"] = "Upstream tool catalog changed."
    transform = ToolDefinitionPinningTransform(
        server_id="playwright",
        session_store=store,
        policy=ToolDefinitionPinningPolicy(mode="block", block_strategy="error"),
    )
    monkeypatch.setattr(
        "remote_mcp_adapter.proxy.tool_definition_pinning.transform.get_context",
        lambda: _session_context(),
    )

    with pytest.raises(FastMCPError, match="Start a new Mcp-Session-Id"):
        await transform.transform_tools([_tool(name="tool_a")])


async def _return_changed_tool(name: str) -> Tool:
    return _tool(name=name, description="Changed")


async def _return_new_tool(name: str, version=None) -> Tool:
    return _tool(name=name)


# ---------------------------------------------------------------------------
# canonical.py gap coverage — _normalize_description non-warning lines,
# _normalize_meta non-fastmcp branch, _normalize_annotations dict path,
# _normalize_json list/tuple/set/scalar branches
# ---------------------------------------------------------------------------


def test_canonicalize_tool_keeps_non_warning_description_lines():
    tool = _tool(
        description=(
            "WARNING: Tool definitions changed during this adapter session. Drift: changed=tool_a\n\n"
            "Actual useful description."
        )
    )
    snapshot = canon.canonicalize_tool(tool)
    assert "Actual useful description" in (snapshot.payload.get("description") or "")


def test_canonicalize_tool_meta_non_fastmcp_key_is_normalized():
    tool = _tool()
    tool = tool.model_copy(update={"meta": {"custom_key": "some_value"}})
    snapshot = canon.canonicalize_tool(tool)
    assert snapshot.payload["meta"]["custom_key"] == "some_value"


def test_canonicalize_tool_annotations_title_normalized():
    from mcp.types import ToolAnnotations

    tool = _tool()
    tool = tool.model_copy(update={"annotations": ToolAnnotations(title="My\u200bTool")})
    snapshot = canon.canonicalize_tool(tool)
    annotations = snapshot.payload.get("annotations") or {}
    assert "My" in (annotations.get("title") or "")


def test_canonicalize_tool_annotations_non_title_key_normalized():
    from mcp.types import ToolAnnotations

    tool = _tool()
    tool = tool.model_copy(update={"annotations": ToolAnnotations(readOnlyHint=True)})
    snapshot = canon.canonicalize_tool(tool)
    assert snapshot.payload.get("annotations") is not None


def test_normalize_json_list_branch():
    from remote_mcp_adapter.proxy.tool_definition_pinning.canonical import _normalize_json

    result = _normalize_json(["a", "b"])
    assert result == ["a", "b"]


def test_normalize_json_tuple_branch():
    from remote_mcp_adapter.proxy.tool_definition_pinning.canonical import _normalize_json

    result = _normalize_json(("a", "b"))
    assert result == ["a", "b"]


def test_normalize_json_set_branch():
    from remote_mcp_adapter.proxy.tool_definition_pinning.canonical import _normalize_json

    result = _normalize_json({"b", "a"})
    assert sorted(result) == ["a", "b"]


def test_normalize_json_scalar_passthrough():
    from remote_mcp_adapter.proxy.tool_definition_pinning.canonical import _normalize_json

    assert _normalize_json(42) == 42
    assert _normalize_json("hello") == "hello"
    assert _normalize_json(None) is None
