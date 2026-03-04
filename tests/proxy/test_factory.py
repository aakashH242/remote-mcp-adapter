from __future__ import annotations

from types import SimpleNamespace

import pytest

from remote_mcp_adapter.proxy import factory as f


class _FakeSessionStore:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def touch_tool_activity(self, server_id: str, session_id: str):
        self.calls.append((server_id, session_id))


class _FakeManagedClient:
    def __init__(self, *, fail_close: bool = False):
        self.entered = 0
        self.closed = 0
        self.fail_close = fail_close

    async def __aenter__(self):
        self.entered += 1
        return self

    async def close(self):
        self.closed += 1
        if self.fail_close:
            raise RuntimeError("close failed")


class _CaptureCtor:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(**kwargs)


def _server(*, server_id="srv", transport="sse", insecure_tls=False, required=None, passthrough=None, static_headers=None):
    return SimpleNamespace(
        id=server_id,
        upstream=SimpleNamespace(
            static_headers=static_headers or {"X-Static": "1"},
            client_headers=SimpleNamespace(required=required or [], passthrough=passthrough or []),
            insecure_tls=insecure_tls,
            transport=transport,
            url="http://upstream.example/mcp",
        ),
        tool_defaults=SimpleNamespace(tool_call_timeout_seconds=None),
    )


def _registry(server=None, **kwargs):
    return f.SessionClientRegistry(server=server or _server(), **kwargs)


def test_build_headers_and_required_header_validation():
    registry = _registry(server=_server(passthrough=["X-Req", "X-Other"]))

    assert registry._build_headers(None) == {"X-Static": "1"}
    headers = registry._build_headers({"x-req": "v", "x-other": "", "x-unused": "z"})
    assert headers == {"X-Static": "1", "X-Req": "v"}

    registry_no_required = _registry(server=_server(required=[]))
    registry_no_required._validate_required_headers(None)

    registry_required = _registry(server=_server(required=["X-Req", "X-Missing"]))
    with pytest.raises(RuntimeError, match="Missing required client headers"):
        registry_required._validate_required_headers({"X-Req": "v"})

    registry_required_ok = _registry(server=_server(required=["X-Req"]))
    registry_required_ok._validate_required_headers({"x-req": "v"})


def test_build_httpx_client_factory_handles_insecure_tls(monkeypatch):
    secure = _registry(server=_server(insecure_tls=False))
    assert secure._build_httpx_client_factory() is None

    captured = _CaptureCtor()
    monkeypatch.setattr(f.httpx, "AsyncClient", captured)

    insecure = _registry(server=_server(insecure_tls=True))
    factory = insecure._build_httpx_client_factory()
    assert factory is not None

    factory(timeout=1)
    factory(timeout=2, verify=True)

    assert captured.calls[0]["verify"] is False
    assert captured.calls[1]["verify"] is True


def test_build_transport_selects_sse_or_streamable_and_passes_factory(monkeypatch):
    sse_ctor = _CaptureCtor()
    stream_ctor = _CaptureCtor()
    monkeypatch.setattr(f, "SSETransport", sse_ctor)
    monkeypatch.setattr(f, "StreamableHttpTransport", stream_ctor)

    registry_sse = _registry(server=_server(transport="sse", insecure_tls=True))
    transport_a = registry_sse._build_transport({"A": "1"})
    assert transport_a.url == "http://upstream.example/mcp"
    assert transport_a.headers == {"A": "1"}
    assert "httpx_client_factory" in sse_ctor.calls[0]

    registry_stream = _registry(server=_server(transport="http", insecure_tls=False))
    transport_b = registry_stream._build_transport({"B": "2"})
    assert transport_b.url == "http://upstream.example/mcp"
    assert transport_b.headers == {"B": "2"}
    assert "httpx_client_factory" not in stream_ctor.calls[0]


def test_build_client_constructs_resilient_client(monkeypatch):
    registry = _registry(
        server=_server(),
        default_timeout_seconds=9,
        session_termination_retries=2,
        metadata_cache_ttl_seconds=15,
    )
    monkeypatch.setattr(f.SessionClientRegistry, "_build_headers", lambda self, inbound=None: {"H": "1"})
    monkeypatch.setattr(f.SessionClientRegistry, "_build_transport", lambda self, headers=None: "transport")

    captured = _CaptureCtor()
    monkeypatch.setattr(f, "ResilientClient", captured)

    client = registry._build_client({"x": "y"})
    assert client.transport == "transport"
    assert client.timeout == 9
    assert client.default_timeout == 9
    assert client.session_termination_retries == 2
    assert client.metadata_cache_ttl_seconds == 15


@pytest.mark.asyncio
async def test_get_session_client_creates_caches_and_touches_session_store(monkeypatch):
    store = _FakeSessionStore()
    registry = _registry(server=_server(), session_store=store)

    fake_client = _FakeManagedClient()
    monkeypatch.setattr(f.SessionClientRegistry, "_build_client", lambda self, inbound=None: fake_client)
    monkeypatch.setattr(
        f,
        "get_context",
        lambda: SimpleNamespace(
            session_id="sess1",
            request_context=SimpleNamespace(request=SimpleNamespace(headers={"x": "1"})),
        ),
    )

    logs: list[tuple[str, dict]] = []
    monkeypatch.setattr(f.logger, "info", lambda message, *, extra: logs.append((message, extra)))

    c1 = await registry.get_session_client()
    c2 = await registry.get_session_client()

    assert c1 is fake_client and c2 is fake_client
    assert fake_client.entered == 1
    assert store.calls == [("srv", "sess1"), ("srv", "sess1")]
    assert logs[0][0] == "Created upstream session client"


@pytest.mark.asyncio
async def test_get_session_client_without_request_context_and_header_validation_error(monkeypatch):
    registry = _registry(server=_server(required=["X-Need"]))

    monkeypatch.setattr(
        f,
        "get_context",
        lambda: SimpleNamespace(session_id="sess1", request_context=None),
    )

    with pytest.raises(RuntimeError, match="Missing required client headers"):
        await registry.get_session_client()


def test_build_probe_client_timeout_resolution(monkeypatch):
    registry = _registry(server=_server(), default_timeout_seconds=2)
    monkeypatch.setattr(f.SessionClientRegistry, "_build_headers", lambda self, inbound=None: {"H": "1"})
    monkeypatch.setattr(f.SessionClientRegistry, "_build_transport", lambda self, headers=None: "transport")

    captured = _CaptureCtor()
    monkeypatch.setattr(f, "Client", captured)

    explicit = registry.build_probe_client(timeout_seconds=11)
    defaulted = registry.build_probe_client()

    assert explicit.timeout == 11
    assert defaulted.timeout == 2

    registry_no_default = _registry(server=_server(), default_timeout_seconds=None)
    monkeypatch.setattr(f.SessionClientRegistry, "_build_headers", lambda self, inbound=None: {})
    monkeypatch.setattr(f.SessionClientRegistry, "_build_transport", lambda self, headers=None: "transport")
    fallback = registry_no_default.build_probe_client()
    assert fallback.timeout == 5


@pytest.mark.asyncio
async def test_reset_cached_clients_closes_and_logs_success_and_failure(monkeypatch):
    registry = _registry(server=_server())
    good = _FakeManagedClient()
    bad = _FakeManagedClient(fail_close=True)
    registry._clients = {"a": good, "b": bad}

    warnings: list[dict] = []
    debugs: list[dict] = []
    monkeypatch.setattr(f.logger, "warning", lambda msg, *, extra: warnings.append(extra))
    monkeypatch.setattr(f.logger, "debug", lambda msg, *, extra, exc_info: debugs.append(extra))

    count = await registry.reset_cached_clients(reason="health")

    assert count == 2
    assert good.closed == 1 and bad.closed == 1
    assert len(warnings) == 1
    assert len(debugs) == 1
    assert registry._clients == {}


@pytest.mark.asyncio
async def test_close_all_handles_close_errors(monkeypatch):
    registry = _registry(server=_server())
    good = _FakeManagedClient()
    bad = _FakeManagedClient(fail_close=True)
    registry._clients = {"a": good, "b": bad}

    debugs: list[dict] = []
    monkeypatch.setattr(f.logger, "debug", lambda msg, *, extra, exc_info: debugs.append(extra))

    await registry.close_all()

    assert good.closed == 1 and bad.closed == 1
    assert registry._clients == {}
    assert len(debugs) == 1


def test_resolve_timeout_seconds_uses_server_then_core_default():
    config = SimpleNamespace(core=SimpleNamespace(defaults=SimpleNamespace(tool_call_timeout_seconds=12)))
    server_with = SimpleNamespace(tool_defaults=SimpleNamespace(tool_call_timeout_seconds=9))
    server_without = SimpleNamespace(tool_defaults=SimpleNamespace(tool_call_timeout_seconds=None))

    assert f._resolve_timeout_seconds(config, server_with) == 9
    assert f._resolve_timeout_seconds(config, server_without) == 12


def test_build_proxy_map_constructs_mounts(monkeypatch):
    server_a = _server(server_id="a")
    server_b = _server(server_id="b")
    config = SimpleNamespace(
        servers=[server_a, server_b],
        sessions=SimpleNamespace(upstream_session_termination_retries=4),
        core=SimpleNamespace(
            upstream_metadata_cache_ttl_seconds=25,
            defaults=SimpleNamespace(tool_call_timeout_seconds=7),
        ),
    )

    proxy_ctor = _CaptureCtor()
    monkeypatch.setattr(f, "FastMCPProxy", proxy_ctor)

    proxy_map = f.build_proxy_map(config, session_store=None)

    assert set(proxy_map.keys()) == {"a", "b"}
    assert proxy_map["a"].server.id == "a"
    assert proxy_map["b"].server.id == "b"
    assert proxy_ctor.calls[0]["name"] == "MCP Proxy [a]"
    assert callable(proxy_ctor.calls[0]["client_factory"])
