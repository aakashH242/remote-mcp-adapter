"""Tests for proxy/tool_metadata_sanitization — text, schema, models, transform."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp.tools.tool import Tool
from mcp.types import ToolAnnotations

from remote_mcp_adapter.proxy.tool_metadata_sanitization.models import (
    ToolMetadataSanitizationPolicy,
    resolve_tool_metadata_sanitization_policy,
)
from remote_mcp_adapter.proxy.tool_metadata_sanitization.schema import (
    canonicalize_schema_metadata,
    sanitize_schema_metadata,
)
from remote_mcp_adapter.proxy.tool_metadata_sanitization.text import (
    canonicalize_metadata_text,
    sanitize_metadata_text,
    truncate_text_with_ellipsis,
)
from remote_mcp_adapter.proxy.tool_metadata_sanitization.transform import (
    ToolMetadataSanitizationTransform,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _policy(
    mode: str = "sanitize",
    normalize_unicode: bool = False,
    remove_invisible_characters: bool = False,
    max_tool_title_chars: int | None = None,
    max_tool_description_chars: int | None = None,
    max_schema_text_chars: int | None = None,
) -> ToolMetadataSanitizationPolicy:
    return ToolMetadataSanitizationPolicy(
        mode=mode,
        normalize_unicode=normalize_unicode,
        remove_invisible_characters=remove_invisible_characters,
        max_tool_title_chars=max_tool_title_chars,
        max_tool_description_chars=max_tool_description_chars,
        max_schema_text_chars=max_schema_text_chars,
    )


def _tool(description: str = "Normal description.", title: str | None = None) -> Tool:
    async def fn(x: str) -> str:
        return x

    t = Tool.from_function(fn=fn, name="my_tool", description=description)
    if title is not None:
        t = t.model_copy(update={"title": title})
    return t


# ---------------------------------------------------------------------------
# text.py — sanitize_metadata_text
# ---------------------------------------------------------------------------


def test_sanitize_metadata_text_normalizes_unicode():
    # \ufb01 is the fi ligature; NFKC decomposes it to "fi"
    result = sanitize_metadata_text(
        "\ufb01le",
        normalize_unicode=True,
        remove_invisible_characters=False,
        max_chars=None,
    )
    assert result.modified is True
    assert "unicode_normalized" in result.reasons
    assert result.value == "file"


def test_sanitize_metadata_text_removes_invisible_characters():
    # U+200B is zero-width space (category Cf)
    result = sanitize_metadata_text(
        "hello\u200bworld",
        normalize_unicode=False,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.modified is True
    assert "invisible_characters_removed" in result.reasons
    assert result.value == "helloworld"


def test_sanitize_metadata_text_truncates_long_text():
    result = sanitize_metadata_text(
        "Hello, world!",
        normalize_unicode=False,
        remove_invisible_characters=False,
        max_chars=8,
    )
    assert result.modified is True
    assert "truncated" in result.reasons
    assert len(result.value) == 8


def test_sanitize_metadata_text_unchanged_when_below_max():
    result = sanitize_metadata_text(
        "Short",
        normalize_unicode=True,
        remove_invisible_characters=True,
        max_chars=100,
    )
    assert result.modified is False
    assert result.value == "Short"


def test_sanitize_metadata_text_none_unchanged():
    result = sanitize_metadata_text(
        None,
        normalize_unicode=True,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.modified is False
    assert result.value is None


def test_sanitize_metadata_text_multiple_reasons():
    result = sanitize_metadata_text(
        "\ufb01le\u200b",  # needs unicode normalization AND invisible removal
        normalize_unicode=True,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.modified is True
    assert "unicode_normalized" in result.reasons
    assert "invisible_characters_removed" in result.reasons


def test_truncate_text_with_ellipsis_normal():
    result = truncate_text_with_ellipsis("Hello world!", max_chars=8)
    assert result == "Hello..."
    assert len(result) == 8


def test_truncate_text_with_ellipsis_max_chars_3():
    result = truncate_text_with_ellipsis("Hello", max_chars=3)
    assert result == "Hel"
    assert len(result) == 3


def test_truncate_text_with_ellipsis_max_chars_2():
    result = truncate_text_with_ellipsis("Hello", max_chars=2)
    assert result == "He"


def test_canonicalize_metadata_text_normalizes_unicode_and_strips_invisible():
    result = canonicalize_metadata_text("hello\u200bworld\ufb01")
    assert result is not None
    assert "\u200b" not in result


def test_canonicalize_metadata_text_none():
    assert canonicalize_metadata_text(None) is None


# ---------------------------------------------------------------------------
# schema.py — sanitize_schema_metadata
# ---------------------------------------------------------------------------


def test_sanitize_schema_metadata_title_invisible_removed():
    schema = {"title": "hello\u200bworld"}
    result = sanitize_schema_metadata(
        schema,
        normalize_unicode=False,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.modified is True
    assert result.value["title"] == "helloworld"


def test_sanitize_schema_metadata_description_invisible_removed():
    schema = {"description": "desc\u200b"}
    result = sanitize_schema_metadata(
        schema,
        normalize_unicode=False,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.modified is True
    assert result.value["description"] == "desc"


def test_sanitize_schema_metadata_nested_properties():
    schema = {
        "type": "object",
        "properties": {"x": {"title": "The\u200bX", "type": "string"}},
    }
    result = sanitize_schema_metadata(
        schema,
        normalize_unicode=False,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.modified is True
    assert result.value["properties"]["x"]["title"] == "TheX"


def test_sanitize_schema_metadata_list_branch():
    schema = [{"title": "item\u200b"}]
    result = sanitize_schema_metadata(
        schema,
        normalize_unicode=False,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.modified is True
    assert result.value[0]["title"] == "item"


def test_sanitize_schema_metadata_tuple_branch():
    schema = ({"title": "item\u200b"},)
    result = sanitize_schema_metadata(
        schema,
        normalize_unicode=False,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.modified is True


def test_sanitize_schema_metadata_set_branch():
    result = sanitize_schema_metadata(
        {"a", "b"},
        normalize_unicode=False,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.modified is False


def test_sanitize_schema_metadata_none():
    result = sanitize_schema_metadata(
        None,
        normalize_unicode=True,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.value is None
    assert result.modified is False


def test_sanitize_schema_metadata_scalar_passthrough():
    result = sanitize_schema_metadata(
        42,
        normalize_unicode=True,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.value == 42
    assert result.modified is False


def test_sanitize_schema_metadata_non_textual_key_unchanged():
    schema = {"type": "object", "required": ["x"]}
    result = sanitize_schema_metadata(
        schema,
        normalize_unicode=False,
        remove_invisible_characters=True,
        max_chars=None,
    )
    assert result.modified is False


def test_canonicalize_schema_metadata_sorts_keys_and_normalizes():
    schema = {"z": 1, "a": 2, "title": "hello\u200bworld"}
    result = canonicalize_schema_metadata(schema)
    assert list(result.keys()) == ["a", "title", "z"]
    assert result["title"] == "helloworld"


def test_canonicalize_schema_metadata_list():
    result = canonicalize_schema_metadata([{"title": "a\u200b"}])
    assert result[0]["title"] == "a"


def test_canonicalize_schema_metadata_tuple():
    result = canonicalize_schema_metadata(("val",))
    assert result == ["val"]


def test_canonicalize_schema_metadata_set():
    result = canonicalize_schema_metadata({"x", "y"})
    assert sorted(result) == ["x", "y"]


def test_canonicalize_schema_metadata_none():
    assert canonicalize_schema_metadata(None) is None


def test_canonicalize_schema_metadata_scalar():
    assert canonicalize_schema_metadata(42) == 42


# ---------------------------------------------------------------------------
# models.py — ToolMetadataSanitizationPolicy + resolver
# ---------------------------------------------------------------------------


def test_policy_blocks_on_change_true_for_block_mode():
    assert _policy(mode="block").blocks_on_change is True


def test_policy_blocks_on_change_false_for_sanitize_mode():
    assert _policy(mode="sanitize").blocks_on_change is False


def test_policy_enabled_false_when_off():
    assert _policy(mode="off").enabled is False


def test_policy_enabled_true_for_sanitize():
    assert _policy(mode="sanitize").enabled is True


def test_resolve_tool_metadata_sanitization_policy_server_override():
    config = SimpleNamespace(
        core=SimpleNamespace(
            tool_metadata_sanitization=SimpleNamespace(
                mode="off",
                normalize_unicode=False,
                remove_invisible_characters=False,
                max_tool_title_chars=256,
                max_tool_description_chars=2000,
                max_schema_text_chars=1000,
            )
        )
    )
    server = SimpleNamespace(
        tool_metadata_sanitization=SimpleNamespace(
            mode="block",
            normalize_unicode=True,
            remove_invisible_characters=True,
            max_tool_title_chars=64,
            max_tool_description_chars=200,
            max_schema_text_chars=100,
        )
    )
    policy = resolve_tool_metadata_sanitization_policy(config=config, server=server)
    assert policy.mode == "block"
    assert policy.normalize_unicode is True
    assert policy.remove_invisible_characters is True
    assert policy.max_tool_title_chars == 64
    assert policy.max_tool_description_chars == 200
    assert policy.max_schema_text_chars == 100


# ---------------------------------------------------------------------------
# transform.py — ToolMetadataSanitizationTransform
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_tools_unchanged_when_disabled():
    transform = ToolMetadataSanitizationTransform(server_id="s1", policy=_policy(mode="off"))
    tool = _tool()
    result = await transform.transform_tools([tool])
    assert result == [tool]


@pytest.mark.asyncio
async def test_transform_tools_sanitizes_description():
    transform = ToolMetadataSanitizationTransform(
        server_id="s1",
        policy=_policy(mode="sanitize", remove_invisible_characters=True),
    )
    tool = _tool(description="desc\u200btext")
    result = await transform.transform_tools([tool])
    assert result[0].description == "desctext"


@pytest.mark.asyncio
async def test_transform_tools_blocks_when_mode_is_block():
    transform = ToolMetadataSanitizationTransform(
        server_id="s1",
        policy=_policy(mode="block", remove_invisible_characters=True),
    )
    tool = _tool(description="desc\u200btext")
    result = await transform.transform_tools([tool])
    assert result == []


@pytest.mark.asyncio
async def test_transform_tools_passes_clean_tool_through():
    transform = ToolMetadataSanitizationTransform(
        server_id="s1",
        policy=_policy(mode="sanitize"),
    )
    tool = _tool(description="Clean text")
    result = await transform.transform_tools([tool])
    assert len(result) == 1
    assert result[0].description == "Clean text"


@pytest.mark.asyncio
async def test_get_tool_sanitizes_description():
    transform = ToolMetadataSanitizationTransform(
        server_id="s1",
        policy=_policy(mode="sanitize", remove_invisible_characters=True),
    )
    tool = _tool(description="desc\u200b")

    async def call_next(name, *, version=None):
        return tool

    result = await transform.get_tool("my_tool", call_next)
    assert result is not None
    assert result.description == "desc"


@pytest.mark.asyncio
async def test_get_tool_blocks_modified_tool():
    transform = ToolMetadataSanitizationTransform(
        server_id="s1",
        policy=_policy(mode="block", remove_invisible_characters=True),
    )
    tool = _tool(description="desc\u200b")

    async def call_next(name, *, version=None):
        return tool

    result = await transform.get_tool("my_tool", call_next)
    assert result is None


@pytest.mark.asyncio
async def test_get_tool_returns_none_when_upstream_returns_none():
    transform = ToolMetadataSanitizationTransform(
        server_id="s1",
        policy=_policy(mode="sanitize"),
    )

    async def call_next(name, *, version=None):
        return None

    result = await transform.get_tool("missing", call_next)
    assert result is None


@pytest.mark.asyncio
async def test_get_tool_returns_unchanged_when_disabled():
    transform = ToolMetadataSanitizationTransform(server_id="s1", policy=_policy(mode="off"))
    tool = _tool()

    async def call_next(name, *, version=None):
        return tool

    result = await transform.get_tool("my_tool", call_next)
    assert result is tool


@pytest.mark.asyncio
async def test_transform_tools_sanitizes_title():
    transform = ToolMetadataSanitizationTransform(
        server_id="s1",
        policy=_policy(mode="sanitize", remove_invisible_characters=True),
    )
    tool = _tool(title="title\u200btext")
    result = await transform.transform_tools([tool])
    assert result[0].title == "titletext"


@pytest.mark.asyncio
async def test_transform_tools_sanitizes_annotations_title():
    transform = ToolMetadataSanitizationTransform(
        server_id="s1",
        policy=_policy(mode="sanitize", remove_invisible_characters=True),
    )
    tool = _tool()
    tool = tool.model_copy(update={"annotations": ToolAnnotations(title="title\u200b")})
    result = await transform.transform_tools([tool])
    assert result[0].annotations.title == "title"


@pytest.mark.asyncio
async def test_transform_tools_clean_tool_returns_same_object():
    transform = ToolMetadataSanitizationTransform(
        server_id="s1",
        policy=_policy(mode="sanitize"),
    )
    tool = _tool(description="Clean")
    result = await transform.transform_tools([tool])
    assert result[0] is tool


@pytest.mark.asyncio
async def test_transform_tools_sanitizes_schema_text():
    transform = ToolMetadataSanitizationTransform(
        server_id="s1",
        policy=_policy(mode="sanitize", remove_invisible_characters=True),
    )
    tool = _tool()
    tool = tool.model_copy(
        update={
            "parameters": {
                "type": "object",
                "properties": {"x": {"type": "string", "title": "The\u200bX"}},
            }
        }
    )
    result = await transform.transform_tools([tool])
    assert result[0].parameters["properties"]["x"]["title"] == "TheX"
