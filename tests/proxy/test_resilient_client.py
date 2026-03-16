from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from fastmcp.exceptions import ToolError
from remote_mcp_adapter.proxy import resilient_client as rc


def _make_client(*, default_timeout=9, retries=1, cache_ttl=30):
    client = object.__new__(rc.ResilientClient)
    client._default_timeout = default_timeout
    client._session_termination_retries = retries
    client._reconnect_lock = asyncio.Lock()
    client._metadata_cache_ttl_seconds = cache_ttl
    client._bypass_list_tools_cache = False
    client._metadata_cache = {}
    client._metadata_cache_lock = asyncio.Lock()
    client._metadata_fetch_locks = {}
    client._metadata_fetch_locks_lock = asyncio.Lock()
    client._session_state = SimpleNamespace(nesting_counter=1)

    async def close_transport():
        return None

    client.transport = SimpleNamespace(get_session_id=lambda: "sid-1", close=close_transport)

    client._disconnect_calls = []
    client._connect_calls = 0

    async def _disconnect(force=False):
        client._disconnect_calls.append(force)

    async def _connect():
        client._connect_calls += 1

    client._disconnect = _disconnect
    client._connect = _connect
    return client


def test_init_sets_fields_and_clone_helpers(monkeypatch):
    def fake_client_init(self, *args, **kwargs):
        self.transport = SimpleNamespace()
        self._session_state = SimpleNamespace(nesting_counter=0)

    monkeypatch.setattr(rc.Client, "__init__", fake_client_init)

    client = rc.ResilientClient(
        transport=SimpleNamespace(),
        default_timeout=4,
        session_termination_retries=-1,
        metadata_cache_ttl_seconds=-2,
        bypass_list_tools_cache=True,
    )

    assert client._default_timeout == 4
    assert client._session_termination_retries == 0
    assert client._metadata_cache_ttl_seconds == 0
    assert client._bypass_list_tools_cache is True
    assert rc.ResilientClient._clone_cached_value([1]) == [1]
    assert rc.ResilientClient._clone_cached_value("x") == "x"


def test_message_text_and_exception_chain_helpers():
    assert rc._message_contains_session_termination_signal(None) is False
    assert rc._message_contains_session_termination_signal(" session terminated ") is True

    result = SimpleNamespace(content=[SimpleNamespace(text="hello")])
    assert rc._first_content_text(result) == "hello"
    assert rc._first_content_text(SimpleNamespace(content=[])) is None
    assert rc._first_content_text(SimpleNamespace(content=[SimpleNamespace(text=1)])) is None

    e1 = RuntimeError("a")
    e2 = RuntimeError("b")
    e1.__cause__ = e2
    e2.__cause__ = e1
    chain = list(rc._iter_exception_chain(e1))
    assert chain == [e1, e2]


@pytest.mark.asyncio
async def test_cache_helpers_and_fetch_locks(monkeypatch):
    client = _make_client(cache_ttl=10)
    monkeypatch.setattr(rc.time, "monotonic", lambda: 100.0)

    await client._set_metadata_cache("k", [1, 2])
    cached = await client._get_metadata_cache("k")
    assert cached == [1, 2]
    cached.append(3)
    cached_again = await client._get_metadata_cache("k")
    assert cached_again == [1, 2]

    monkeypatch.setattr(rc.time, "monotonic", lambda: 200.0)
    assert await client._get_metadata_cache("k") is None

    lock1 = await client._metadata_fetch_lock("k")
    lock2 = await client._metadata_fetch_lock("k")
    assert lock1 is lock2

    await client._clear_metadata_cache()
    assert client._metadata_cache == {}


@pytest.mark.asyncio
async def test_cache_disabled_and_expiry_deadline_branches(monkeypatch):
    client = _make_client(cache_ttl=0)
    await client._set_metadata_cache("k", [1])
    assert await client._get_metadata_cache("k") is None

    client = _make_client(cache_ttl=5)
    monkeypatch.setattr(rc.time, "monotonic", lambda: 50.0)
    assert client._cache_expiry_deadline() == 45.0


@pytest.mark.asyncio
async def test_call_with_cached_retry_uses_cache_then_factory(monkeypatch):
    client = _make_client(cache_ttl=30)
    monkeypatch.setattr(rc.time, "monotonic", lambda: 50.0)

    async def call_factory():
        return ["v1"]

    result1 = await client._call_with_cached_session_termination_retry(
        cache_key="k",
        operation_name="op",
        call_factory=call_factory,
    )
    result2 = await client._call_with_cached_session_termination_retry(
        cache_key="k",
        operation_name="op",
        call_factory=lambda: (_ for _ in ()).throw(RuntimeError("should not run")),
    )

    assert result1 == ["v1"]
    assert result2 == ["v1"]


@pytest.mark.asyncio
async def test_call_with_cached_retry_uses_second_cache_check_inside_lock(monkeypatch):
    client = _make_client(cache_ttl=30)
    states = {"count": 0}

    async def fake_get(cache_key):
        states["count"] += 1
        if states["count"] == 1:
            return None
        return ["from-second-check"]

    async def fake_factory():
        raise RuntimeError("factory should not run")

    monkeypatch.setattr(client, "_get_metadata_cache", fake_get)

    result = await client._call_with_cached_session_termination_retry(
        cache_key="k",
        operation_name="op",
        call_factory=fake_factory,
    )

    assert result == ["from-second-check"]


def test_upstream_session_id_and_resolve_timeout_paths():
    client = _make_client(default_timeout=8)
    assert client._upstream_session_id() == "sid-1"

    client.transport = SimpleNamespace(get_session_id=lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert client._upstream_session_id() is None

    client.transport = SimpleNamespace()
    assert client._upstream_session_id() is None

    assert client._resolve_timeout(3) == 3
    assert client._resolve_timeout(None) == 8

    client._default_timeout = None
    assert client._resolve_timeout(None) is None


@pytest.mark.asyncio
async def test_reconnect_preserving_nesting_clears_cache_and_reconnects(monkeypatch):
    client = _make_client()
    client._session_state.nesting_counter = 2
    client._metadata_cache = {"k": (1.0, [1])}

    infos: list[str] = []
    monkeypatch.setattr(rc.logger, "info", lambda message, *, extra: infos.append(message))

    await client._reconnect_preserving_nesting()

    assert client._metadata_cache == {}
    assert client._disconnect_calls == [True]
    assert client._connect_calls == 2
    assert any("Reconnecting upstream client session" in msg for msg in infos)
    assert any("session reconnected" in msg for msg in infos)


def test_is_session_terminated_result_and_timeout_detection():
    err_result = SimpleNamespace(isError=True, content=[SimpleNamespace(text="session not found")])
    ok_result = SimpleNamespace(isError=False, content=[SimpleNamespace(text="session not found")])

    assert rc.ResilientClient._is_session_terminated_result(err_result) is True
    assert rc.ResilientClient._is_session_terminated_result(ok_result) is False

    assert rc.ResilientClient._is_timeout_exception(asyncio.TimeoutError()) is True
    assert rc.ResilientClient._is_timeout_exception(TimeoutError()) is True
    assert rc.ResilientClient._is_timeout_exception(httpx.TimeoutException("x")) is True
    assert rc.ResilientClient._is_timeout_exception(RuntimeError("x")) is False


def test_is_session_terminated_exception_paths(monkeypatch):
    request = httpx.Request("GET", "http://example")
    response = httpx.Response(404, request=request)
    http_exc = httpx.HTTPStatusError("not found", request=request, response=response)
    assert rc.ResilientClient._is_session_terminated_exception(http_exc) is True

    class FakeMcpError(Exception):
        def __init__(self, error):
            super().__init__("server not initialized")
            self.error = error

    monkeypatch.setattr(rc, "McpError", FakeMcpError)
    mcp_exc = FakeMcpError(SimpleNamespace(code=32600, message="server not initialized"))
    assert rc.ResilientClient._is_session_terminated_exception(mcp_exc) is True

    mcp_message_only = FakeMcpError(SimpleNamespace(code=12345, message="unknown session"))
    assert rc.ResilientClient._is_session_terminated_exception(mcp_message_only) is True

    tool_exc = ToolError("unknown session")
    assert rc.ResilientClient._is_session_terminated_exception(tool_exc) is True

    generic = RuntimeError("session gone")
    assert rc.ResilientClient._is_session_terminated_exception(generic) is True
    assert rc.ResilientClient._is_session_terminated_exception(RuntimeError("other")) is False


@pytest.mark.asyncio
async def test_call_with_session_termination_retry_behaviors(monkeypatch):
    client = _make_client(retries=1)
    reconnect_calls = {"count": 0}

    async def reconnect():
        reconnect_calls["count"] += 1

    monkeypatch.setattr(client, "_reconnect_preserving_nesting", reconnect)

    async def ok_factory():
        return "ok"

    assert await client._call_with_session_termination_retry(operation_name="x", call_factory=ok_factory) == "ok"

    monkeypatch.setattr(client, "_is_session_terminated_exception", lambda exc: False)

    async def fail_factory():
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        await client._call_with_session_termination_retry(operation_name="x", call_factory=fail_factory)

    attempts = {"n": 0}
    monkeypatch.setattr(client, "_is_session_terminated_exception", lambda exc: True)

    async def retry_factory():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("session terminated")
        return "ok-retry"

    assert await client._call_with_session_termination_retry(operation_name="x", call_factory=retry_factory) == "ok-retry"
    assert reconnect_calls["count"] == 1


@pytest.mark.asyncio
async def test_list_wrappers_delegate_to_cached_retry(monkeypatch):
    client = _make_client()
    calls: list[tuple[str, str]] = []

    async def fake_cached(**kwargs):
        calls.append((kwargs["cache_key"], kwargs["operation_name"]))
        return [kwargs["operation_name"]]

    monkeypatch.setattr(client, "_call_with_cached_session_termination_retry", fake_cached)

    assert await client.list_tools() == ["list_tools"]
    assert await client.list_resources() == ["list_resources"]
    assert await client.list_resource_templates() == ["list_resource_templates"]
    assert await client.list_prompts() == ["list_prompts"]

    assert calls == [
        ("list_tools", "list_tools"),
        ("list_resources", "list_resources"),
        ("list_resource_templates", "list_resource_templates"),
        ("list_prompts", "list_prompts"),
    ]


@pytest.mark.asyncio
async def test_list_tools_bypasses_cache_when_configured(monkeypatch):
    client = _make_client()
    client._bypass_list_tools_cache = True
    calls: list[str] = []

    async def fake_uncached():
        calls.append("uncached")
        return ["list_tools"]

    monkeypatch.setattr(client, "list_tools_uncached", fake_uncached)

    assert await client.list_tools() == ["list_tools"]
    assert calls == ["uncached"]


@pytest.mark.asyncio
async def test_read_resource_and_get_prompt_wrappers(monkeypatch):
    client = _make_client()

    async def fake_retry(**kwargs):
        return kwargs["operation_name"]

    monkeypatch.setattr(client, "_call_with_session_termination_retry", fake_retry)

    assert await client.read_resource("doc://x") == "read_resource"
    assert await client.get_prompt("p", arguments={"a": 1}) == "get_prompt"


@pytest.mark.asyncio
async def test_reconnect_on_timeout_wraps_or_rethrows(monkeypatch):
    client = _make_client()
    called = {"n": 0}

    async def reconnect():
        called["n"] += 1

    monkeypatch.setattr(client, "_reconnect_preserving_nesting", reconnect)

    with pytest.raises(ToolError, match="timed out"):
        await client._reconnect_on_timeout(tool_name="t", exc=TimeoutError("x"), wrap_tool_error=True)

    with pytest.raises(TimeoutError):
        await client._reconnect_on_timeout(tool_name="t", exc=TimeoutError("x"), wrap_tool_error=False)

    assert called["n"] == 2


@pytest.mark.asyncio
async def test_call_tool_mcp_retry_and_timeout_paths(monkeypatch):
    client = _make_client(default_timeout=7, retries=2)

    calls = {"mcp": 0, "reconnect": 0, "timeout": 0}

    async def fake_super_call_tool_mcp(self, **kwargs):
        calls["mcp"] += 1
        if calls["mcp"] == 1:
            raise RuntimeError("session terminated")
        if calls["mcp"] == 2:
            return SimpleNamespace(isError=True, content=[SimpleNamespace(text="session terminated")])
        return SimpleNamespace(isError=False, content=[])

    monkeypatch.setattr(rc.Client, "call_tool_mcp", fake_super_call_tool_mcp)
    monkeypatch.setattr(client, "_is_session_terminated_exception", lambda exc: True)

    async def reconnect():
        calls["reconnect"] += 1

    async def timeout_reconnect(**kwargs):
        calls["timeout"] += 1
        raise asyncio.TimeoutError("t")

    monkeypatch.setattr(client, "_reconnect_preserving_nesting", reconnect)

    result = await client.call_tool_mcp(name="tool", arguments={})
    assert result.isError is False
    assert calls["reconnect"] == 2

    # exhausted-result path returns error result when retries are exhausted
    exhausted_client = _make_client(default_timeout=7, retries=1)
    exhausted_calls = {"mcp": 0, "reconnect": 0}

    async def super_mcp_exhausted(self, **kwargs):
        exhausted_calls["mcp"] += 1
        if exhausted_calls["mcp"] == 1:
            raise RuntimeError("session terminated")
        return SimpleNamespace(isError=True, content=[SimpleNamespace(text="session terminated")])

    async def exhausted_reconnect():
        exhausted_calls["reconnect"] += 1

    monkeypatch.setattr(rc.Client, "call_tool_mcp", super_mcp_exhausted)
    monkeypatch.setattr(exhausted_client, "_is_session_terminated_exception", lambda exc: True)
    monkeypatch.setattr(exhausted_client, "_reconnect_preserving_nesting", exhausted_reconnect)
    exhausted_result = await exhausted_client.call_tool_mcp(name="tool", arguments={})
    assert exhausted_result.isError is True

    # timeout path
    calls["mcp"] = 0

    async def timeout_then_never(self, **kwargs):
        raise asyncio.TimeoutError("t")

    monkeypatch.setattr(rc.Client, "call_tool_mcp", timeout_then_never)
    monkeypatch.setattr(client, "_reconnect_on_timeout", timeout_reconnect)

    with pytest.raises(asyncio.TimeoutError):
        await client.call_tool_mcp(name="tool", arguments={})

    # non-terminated exception path raises immediately
    direct_fail = _make_client(default_timeout=7, retries=1)

    async def plain_error(self, **kwargs):
        raise RuntimeError("other")

    monkeypatch.setattr(rc.Client, "call_tool_mcp", plain_error)
    monkeypatch.setattr(direct_fail, "_is_session_terminated_exception", lambda exc: False)
    with pytest.raises(RuntimeError, match="other"):
        await direct_fail.call_tool_mcp(name="tool", arguments={})


@pytest.mark.asyncio
async def test_call_tool_retry_and_timeout_paths(monkeypatch):
    client = _make_client(default_timeout=7, retries=1)

    calls = {"tool": 0, "reconnect": 0, "timeout": 0}

    async def fake_super_call_tool(self, **kwargs):
        calls["tool"] += 1
        if calls["tool"] == 1:
            raise RuntimeError("session terminated")
        return "ok"

    monkeypatch.setattr(rc.Client, "call_tool", fake_super_call_tool)
    monkeypatch.setattr(client, "_is_session_terminated_exception", lambda exc: True)

    async def reconnect():
        calls["reconnect"] += 1

    async def timeout_reconnect(**kwargs):
        calls["timeout"] += 1
        raise ToolError("wrapped")

    monkeypatch.setattr(client, "_reconnect_preserving_nesting", reconnect)

    assert await client.call_tool(name="tool", arguments={}) == "ok"
    assert calls["reconnect"] == 1

    async def timeout_then_never(self, **kwargs):
        raise asyncio.TimeoutError("t")

    monkeypatch.setattr(rc.Client, "call_tool", timeout_then_never)
    monkeypatch.setattr(client, "_reconnect_on_timeout", timeout_reconnect)

    with pytest.raises(ToolError, match="wrapped"):
        await client.call_tool(name="tool", arguments={})

    # non-terminated exception path raises immediately
    direct_fail = _make_client(default_timeout=7, retries=1)

    async def plain_error(self, **kwargs):
        raise RuntimeError("other")

    monkeypatch.setattr(rc.Client, "call_tool", plain_error)
    monkeypatch.setattr(direct_fail, "_is_session_terminated_exception", lambda exc: False)
    with pytest.raises(RuntimeError, match="other"):
        await direct_fail.call_tool(name="tool", arguments={})
