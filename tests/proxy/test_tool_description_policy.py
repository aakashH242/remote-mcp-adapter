"""Tests for proxy/tool_description_policy — text, schema, models, transform."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp.tools.tool import Tool

from remote_mcp_adapter.proxy.tool_description_policy.models import (
    ToolDescriptionPolicy,
    resolve_tool_description_policy,
)
from remote_mcp_adapter.proxy.tool_description_policy.schema import apply_schema_description_policy
from remote_mcp_adapter.proxy.tool_description_policy.text import apply_description_policy
from remote_mcp_adapter.proxy.tool_description_policy.transform import ToolDescriptionPolicyTransform


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy(
    mode: str = "preserve",
    max_tool_description_chars: int | None = None,
    max_schema_description_chars: int | None = None,
) -> ToolDescriptionPolicy:
    return ToolDescriptionPolicy(
        mode=mode,
        max_tool_description_chars=max_tool_description_chars,
        max_schema_description_chars=max_schema_description_chars,
    )


def _tool(description: str = "A handy tool.", parameters: dict | None = None) -> Tool:
    async def fn(x: str) -> str:
        return x

    t = Tool.from_function(fn=fn, name="my_tool", description=description)
    if parameters is not None:
        t = t.model_copy(update={"parameters": parameters})
    return t


# ---------------------------------------------------------------------------
# text.py — apply_description_policy
# ---------------------------------------------------------------------------


def test_apply_description_policy_strip_mode_removes_text():
    result = apply_description_policy("Some text", mode="strip", max_chars=None)
    assert result.value is None
    assert result.modified is True
    assert "stripped" in result.reasons


def test_apply_description_policy_truncate_when_over_limit():
    result = apply_description_policy("Hello world!", mode="truncate", max_chars=8)
    assert result.modified is True
    assert "truncated" in result.reasons
    assert len(result.value) == 8


def test_apply_description_policy_truncate_when_under_limit_unchanged():
    result = apply_description_policy("Hi", mode="truncate", max_chars=100)
    assert result.modified is False
    assert result.value == "Hi"


def test_apply_description_policy_truncate_no_max_chars_unchanged():
    result = apply_description_policy("Some text", mode="truncate", max_chars=None)
    assert result.modified is False
    assert result.value == "Some text"


def test_apply_description_policy_preserve_returns_original():
    result = apply_description_policy("Some text", mode="preserve", max_chars=5)
    assert result.modified is False
    assert result.value == "Some text"


def test_apply_description_policy_none_value_is_unchanged_for_strip():
    result = apply_description_policy(None, mode="strip", max_chars=None)
    assert result.modified is False
    assert result.value is None


# ---------------------------------------------------------------------------
# models.py — ToolDescriptionPolicy properties + resolver
# ---------------------------------------------------------------------------


def test_tool_description_policy_strips_property_true_for_strip():
    assert _policy(mode="strip").strips is True


def test_tool_description_policy_strips_property_false_for_truncate():
    assert _policy(mode="truncate").strips is False


def test_tool_description_policy_not_enabled_for_preserve():
    assert _policy(mode="preserve").enabled is False


def test_tool_description_policy_enabled_for_strip():
    assert _policy(mode="strip").enabled is True


def test_resolve_tool_description_policy_server_override_mode():
    config = SimpleNamespace(
        core=SimpleNamespace(
            tool_description_policy=SimpleNamespace(
                mode="preserve",
                max_tool_description_chars=280,
                max_schema_description_chars=280,
            )
        )
    )
    server = SimpleNamespace(
        tool_description_policy=SimpleNamespace(
            mode="strip",
            max_tool_description_chars=None,
            max_schema_description_chars=None,
        )
    )
    policy = resolve_tool_description_policy(config=config, server=server)
    assert policy.mode == "strip"


def test_resolve_tool_description_policy_server_override_max_chars():
    config = SimpleNamespace(
        core=SimpleNamespace(
            tool_description_policy=SimpleNamespace(
                mode="truncate",
                max_tool_description_chars=500,
                max_schema_description_chars=500,
            )
        )
    )
    server = SimpleNamespace(
        tool_description_policy=SimpleNamespace(
            mode=None,
            max_tool_description_chars=100,
            max_schema_description_chars=50,
        )
    )
    policy = resolve_tool_description_policy(config=config, server=server)
    assert policy.max_tool_description_chars == 100
    assert policy.max_schema_description_chars == 50


# ---------------------------------------------------------------------------
# schema.py — apply_schema_description_policy
# ---------------------------------------------------------------------------


def test_apply_schema_description_policy_strips_nested_description():
    schema = {"type": "object", "description": "Some desc", "properties": {}}
    result = apply_schema_description_policy(schema, mode="strip", max_chars=None)
    assert result.modified is True
    assert "description" not in result.value


def test_apply_schema_description_policy_truncates_nested_description():
    schema = {"description": "A very long description that needs truncating here"}
    result = apply_schema_description_policy(schema, mode="truncate", max_chars=10)
    assert result.modified is True
    assert len(result.value["description"]) == 10


def test_apply_schema_description_policy_preserves_non_description_key():
    schema = {"type": "object", "title": "My Tool"}
    result = apply_schema_description_policy(schema, mode="strip", max_chars=None)
    assert result.modified is False
    assert result.value["title"] == "My Tool"


def test_apply_schema_description_policy_none_value():
    result = apply_schema_description_policy(None, mode="strip", max_chars=None)
    assert result.modified is False
    assert result.value is None


def test_apply_schema_description_policy_non_string_description_unchanged():
    schema = {"description": 99}
    result = apply_schema_description_policy(schema, mode="strip", max_chars=None)
    assert result.modified is False


def test_apply_schema_description_policy_list_of_dicts():
    schema = [{"description": "Item desc"}, {"type": "string"}]
    result = apply_schema_description_policy(schema, mode="strip", max_chars=None)
    assert result.modified is True
    assert "description" not in result.value[0]
    assert result.value[1] == {"type": "string"}


def test_apply_schema_description_policy_tuple_of_dicts():
    schema = ({"description": "Tuple desc"},)
    result = apply_schema_description_policy(schema, mode="strip", max_chars=None)
    assert result.modified is True
    assert "description" not in result.value[0]


def test_apply_schema_description_policy_set_of_scalars():
    result = apply_schema_description_policy({"a", "b"}, mode="strip", max_chars=None)
    assert result.modified is False


def test_apply_schema_description_policy_nested_properties():
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string", "description": "The x param"}},
    }
    result = apply_schema_description_policy(schema, mode="strip", max_chars=None)
    assert result.modified is True
    assert "description" not in result.value["properties"]["x"]


def test_apply_schema_description_policy_scalar_passthrough():
    result = apply_schema_description_policy(42, mode="strip", max_chars=None)
    assert result.value == 42
    assert result.modified is False


# ---------------------------------------------------------------------------
# transform.py — ToolDescriptionPolicyTransform
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_tools_returns_unchanged_when_disabled():
    transform = ToolDescriptionPolicyTransform(server_id="s1", policy=_policy(mode="preserve"))
    tool = _tool()
    result = await transform.transform_tools([tool])
    assert result == [tool]


@pytest.mark.asyncio
async def test_transform_tools_strips_tool_descriptions():
    transform = ToolDescriptionPolicyTransform(server_id="s1", policy=_policy(mode="strip"))
    tool = _tool(description="A tool description.")
    result = await transform.transform_tools([tool])
    assert result[0].description is None


@pytest.mark.asyncio
async def test_transform_tools_strips_schema_descriptions():
    transform = ToolDescriptionPolicyTransform(server_id="s1", policy=_policy(mode="strip"))
    tool = _tool(
        parameters={
            "type": "object",
            "properties": {"x": {"type": "string", "description": "The param"}},
        }
    )
    result = await transform.transform_tools([tool])
    assert "description" not in result[0].parameters["properties"]["x"]


@pytest.mark.asyncio
async def test_transform_tools_truncates_long_top_level_description():
    transform = ToolDescriptionPolicyTransform(
        server_id="s1",
        policy=_policy(mode="truncate", max_tool_description_chars=10),
    )
    tool = _tool(description="A very long tool description that should be truncated.")
    result = await transform.transform_tools([tool])
    assert len(result[0].description) == 10


@pytest.mark.asyncio
async def test_transform_tools_no_change_when_description_short_enough():
    transform = ToolDescriptionPolicyTransform(
        server_id="s1",
        policy=_policy(mode="truncate", max_tool_description_chars=200),
    )
    tool = _tool(description="Short description.")
    result = await transform.transform_tools([tool])
    assert result[0].description == "Short description."


@pytest.mark.asyncio
async def test_get_tool_strips_description():
    transform = ToolDescriptionPolicyTransform(server_id="s1", policy=_policy(mode="strip"))
    tool = _tool(description="Should be stripped.")

    async def call_next(name, *, version=None):
        return tool

    result = await transform.get_tool("my_tool", call_next)
    assert result is not None
    assert result.description is None


@pytest.mark.asyncio
async def test_get_tool_returns_none_when_upstream_returns_none():
    transform = ToolDescriptionPolicyTransform(server_id="s1", policy=_policy(mode="strip"))

    async def call_next(name, *, version=None):
        return None

    result = await transform.get_tool("missing", call_next)
    assert result is None


@pytest.mark.asyncio
async def test_get_tool_returns_unchanged_when_policy_disabled():
    transform = ToolDescriptionPolicyTransform(server_id="s1", policy=_policy(mode="preserve"))
    tool = _tool(description="Keep this.")

    async def call_next(name, *, version=None):
        return tool

    result = await transform.get_tool("my_tool", call_next)
    assert result is tool


@pytest.mark.asyncio
async def test_transform_tools_no_modification_returns_same_tool_object():
    transform = ToolDescriptionPolicyTransform(
        server_id="s1",
        policy=_policy(mode="truncate", max_tool_description_chars=200),
    )
    tool = _tool(description="Short.")
    result = await transform.transform_tools([tool])
    assert result[0] is tool
