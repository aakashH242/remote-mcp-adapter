from __future__ import annotations

from types import SimpleNamespace

import pytest

from remote_mcp_adapter import server


def _config():
    return SimpleNamespace(
        core=SimpleNamespace(upload_path="/upload/{server_id}"),
        state_persistence=SimpleNamespace(type="disk", unavailable_policy="fail_closed"),
    )


def test_create_app_happy_and_fallback(monkeypatch):
    cfg = _config()

    monkeypatch.setattr(server, "resolve_config", lambda config, config_path: cfg)
    monkeypatch.setattr(server, "install_log_redaction_filter", lambda config: None)
    monkeypatch.setattr(server, "AdapterTelemetry", SimpleNamespace(from_config=lambda c: SimpleNamespace()))
    monkeypatch.setattr(
        server,
        "PersistencePolicyController",
        lambda **kwargs: SimpleNamespace(
            handle_startup_failure=lambda **k: "continue_fail_closed"
        ),
    )

    runtime = SimpleNamespace(state_repository=object(), lock_provider=object(), backend_type="disk")
    monkeypatch.setattr(server, "build_persistence_runtime", lambda c: runtime)
    monkeypatch.setattr(server, "build_memory_persistence_runtime", lambda: runtime)
    monkeypatch.setattr(server, "resolve_storage_lock_mode", lambda c: "process")

    monkeypatch.setattr(server, "SessionStore", lambda *args, **kwargs: SimpleNamespace())
    proxy_map = {
        "s1": SimpleNamespace(
            server=SimpleNamespace(mount_path="/mcp/s1"),
            proxy=SimpleNamespace(
                http_app=lambda **k: SimpleNamespace(routes=["r"])
            ),
        )
    }
    monkeypatch.setattr(server, "build_proxy_map", lambda cfg, session_store: proxy_map)
    monkeypatch.setattr(server, "build_upload_nonce_store", lambda **kwargs: object())
    monkeypatch.setattr(server, "UploadCredentialManager", SimpleNamespace(from_config=lambda *a, **k: object()))
    monkeypatch.setattr(server, "ArtifactDownloadCredentialManager", SimpleNamespace(from_config=lambda c: object()))
    monkeypatch.setattr(server, "build_upstream_health_monitors", lambda *a, **k: {"s1": object()})
    monkeypatch.setattr(server, "build_lifespan", lambda **kwargs: "lifespan")
    monkeypatch.setattr(server, "apply_cors_middleware", lambda app, config: None)
    monkeypatch.setattr(server, "CancellationObserver", lambda: object())
    monkeypatch.setattr(server, "build_server_upload_path", lambda base, sid: "/upload/{server_id}")
    monkeypatch.setattr(server, "upload_path_prefix", lambda path: "/upload")

    middleware_calls = []
    routes_calls = []
    monkeypatch.setattr(server, "register_middlewares", lambda **kwargs: middleware_calls.append(kwargs))
    monkeypatch.setattr(server, "register_routes", lambda **kwargs: routes_calls.append(kwargs))
    monkeypatch.setattr(server, "save_upload_stream", object())

    app = server.create_app(cfg)
    assert app.state.adapter_config is cfg
    assert middleware_calls and routes_calls

    def raise_build(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "build_persistence_runtime", raise_build)
    monkeypatch.setattr(server, "build_memory_persistence_runtime", lambda: runtime)
    app2 = server.create_app(cfg)
    assert app2.state.adapter_config is cfg


def test_create_app_re_raises_when_persistence_policy_requests_exit(monkeypatch):
    cfg = _config()

    monkeypatch.setattr(server, "resolve_config", lambda config, config_path: cfg)
    monkeypatch.setattr(server, "install_log_redaction_filter", lambda config: None)
    monkeypatch.setattr(server, "AdapterTelemetry", SimpleNamespace(from_config=lambda c: SimpleNamespace()))
    monkeypatch.setattr(
        server,
        "PersistencePolicyController",
        lambda **kwargs: SimpleNamespace(handle_startup_failure=lambda **k: "exit"),
    )

    def raise_build(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(server, "build_persistence_runtime", raise_build)

    with pytest.raises(RuntimeError, match="boom"):
        server.create_app(cfg)
