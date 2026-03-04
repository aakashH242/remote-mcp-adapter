from __future__ import annotations

from types import SimpleNamespace

from remote_mcp_adapter.proxy import overrides


def _tool_defaults(*, timeout=None, allow_raw_output=None):
    return SimpleNamespace(tool_call_timeout_seconds=timeout, allow_raw_output=allow_raw_output)


def test_first_defined_returns_first_non_none_or_none():
    assert overrides._first_defined(None, None, "x", "y") == "x"
    assert overrides._first_defined(None, None) is None


def test_resolve_tool_timeout_seconds_precedence_adapter_server_core():
    core = _tool_defaults(timeout=10)
    server = _tool_defaults(timeout=20)
    adapter = _tool_defaults(timeout=30)
    assert overrides.resolve_tool_timeout_seconds(core_defaults=core, server_defaults=server, adapter_overrides=adapter) == 30



def test_resolve_tool_timeout_seconds_falls_back_when_values_missing():
    core = _tool_defaults(timeout=10)
    server = _tool_defaults(timeout=None)
    adapter = _tool_defaults(timeout=None)
    assert overrides.resolve_tool_timeout_seconds(core_defaults=core, server_defaults=server, adapter_overrides=adapter) == 10



def test_resolve_allow_raw_output_uses_explicit_adapter_flag_first():
    core = _tool_defaults(allow_raw_output=False)
    server = _tool_defaults(allow_raw_output=False)
    adapter = _tool_defaults(allow_raw_output=False)
    assert (
        overrides.resolve_allow_raw_output(
            core_defaults=core,
            server_defaults=server,
            adapter_overrides=adapter,
            adapter_allow_raw_output=True,
        )
        is True
    )



def test_resolve_allow_raw_output_fallback_chain_and_default_false():
    core_true = _tool_defaults(allow_raw_output=True)
    core_none = _tool_defaults(allow_raw_output=None)
    server_true = _tool_defaults(allow_raw_output=True)
    server_none = _tool_defaults(allow_raw_output=None)
    adapter_true = _tool_defaults(allow_raw_output=True)
    adapter_none = _tool_defaults(allow_raw_output=None)

    assert (
        overrides.resolve_allow_raw_output(
            core_defaults=core_none,
            server_defaults=server_none,
            adapter_overrides=adapter_true,
            adapter_allow_raw_output=None,
        )
        is True
    )
    assert (
        overrides.resolve_allow_raw_output(
            core_defaults=core_none,
            server_defaults=server_true,
            adapter_overrides=adapter_none,
            adapter_allow_raw_output=None,
        )
        is True
    )
    assert (
        overrides.resolve_allow_raw_output(
            core_defaults=core_true,
            server_defaults=server_none,
            adapter_overrides=adapter_none,
            adapter_allow_raw_output=None,
        )
        is True
    )
    assert (
        overrides.resolve_allow_raw_output(
            core_defaults=core_none,
            server_defaults=server_none,
            adapter_overrides=adapter_none,
            adapter_allow_raw_output=None,
        )
        is False
    )
