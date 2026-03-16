"""FastAPI app factory with multi-server FastMCP proxy mounts."""

from __future__ import annotations

from fastapi import FastAPI

from .app.http import register_middlewares, register_routes
from .app.lifespan import build_lifespan
from .app.runtime import build_upstream_health_monitors
from .app.runtime_request_helpers import apply_cors_middleware, resolve_config, upload_path_prefix
from .config import AdapterConfig, resolve_storage_lock_mode
from .core import (
    PersistencePolicyController,
    build_memory_persistence_runtime,
    build_persistence_runtime,
)
from .core.storage.store import SessionStore
from .core.storage.uploads import save_upload_stream
from .proxy.cancellation import CancellationObserver
from .proxy.factory import build_proxy_map
from .proxy.artifact_download_credentials import ArtifactDownloadCredentialManager
from .proxy.upload_credentials import UploadCredentialManager
from .proxy.upload_nonce_store import build_upload_nonce_store
from .proxy.upload_helpers import build_server_upload_path
from .telemetry import AdapterTelemetry
from .log_redaction import install_log_redaction_filter


def create_app(config: AdapterConfig | None = None, config_path: str | None = None) -> FastAPI:
    """Create the adapter FastAPI app with one MCP proxy mount per server config.

    Args:
        config: Pre-built config object, used as-is when provided.
        config_path: Filesystem path to a YAML config file (used when
            *config* is None).

    Returns:
        Fully-configured ``FastAPI`` application ready for ``uvicorn.run``.
    """
    resolved_config = resolve_config(config, config_path)
    install_log_redaction_filter(config=resolved_config)
    telemetry = AdapterTelemetry.from_config(resolved_config)
    persistence_policy = PersistencePolicyController(
        configured_backend=resolved_config.state_persistence.type,
        unavailable_policy=resolved_config.state_persistence.unavailable_policy,
        telemetry=telemetry,
    )
    try:
        persistence_runtime = build_persistence_runtime(resolved_config)
    except Exception as exc:
        startup_action = persistence_policy.handle_startup_failure(
            phase="runtime_build",
            error=str(exc),
        )
        if startup_action == "exit":
            raise
        persistence_runtime = build_memory_persistence_runtime()
    runtime_ref: dict[str, object] = {"current": persistence_runtime}
    write_policy_lock_mode = resolve_storage_lock_mode(resolved_config)
    session_store = SessionStore(
        resolved_config,
        state_repository=persistence_runtime.state_repository,
        lock_provider=persistence_runtime.lock_provider,
        telemetry=telemetry,
    )
    proxy_map = build_proxy_map(resolved_config, session_store=session_store, telemetry=telemetry)
    upload_nonce_store = build_upload_nonce_store(config=resolved_config, runtime=persistence_runtime)
    upload_credentials = UploadCredentialManager.from_config(
        resolved_config,
        nonce_store=upload_nonce_store,
        telemetry=telemetry,
    )
    artifact_download_credentials = ArtifactDownloadCredentialManager.from_config(resolved_config)
    upstream_health = build_upstream_health_monitors(resolved_config, proxy_map, telemetry=telemetry)
    mounted_http_apps = {
        server_id: mount.proxy.http_app(path=mount.server.mount_path, transport="streamable-http")
        for server_id, mount in proxy_map.items()
    }

    combined_routes = []
    for mounted in mounted_http_apps.values():
        combined_routes.extend(mounted.routes)

    lifespan = build_lifespan(
        resolved_config=resolved_config,
        runtime_ref=runtime_ref,
        session_store=session_store,
        proxy_map=proxy_map,
        upstream_health=upstream_health,
        write_policy_lock_mode=write_policy_lock_mode,
        persistence_policy=persistence_policy,
        mounted_http_apps=mounted_http_apps,
        upload_credentials=upload_credentials,
        artifact_download_credentials=artifact_download_credentials,
        telemetry=telemetry,
        build_memory_persistence_runtime=build_memory_persistence_runtime,
    )

    app = FastAPI(
        title="MCP General Adapter",
        lifespan=lifespan,
        routes=combined_routes,
    )
    apply_cors_middleware(app, resolved_config)
    app.state.adapter_config = resolved_config
    app.state.session_store = session_store
    app.state.proxy_map = proxy_map
    app.state.upstream_health = upstream_health
    app.state.persistence_runtime = runtime_ref["current"]
    app.state.persistence_policy = persistence_policy
    app.state.upload_credentials = upload_credentials
    app.state.artifact_download_credentials = artifact_download_credentials
    app.state.telemetry = telemetry
    mount_path_to_server_id = {mount.server.mount_path: server_id for server_id, mount in proxy_map.items()}
    cancellation_observer = CancellationObserver()
    upload_route = build_server_upload_path(resolved_config.core.upload_path, "{server_id}")
    upload_route_prefix = upload_path_prefix(resolved_config.core.upload_path)

    register_middlewares(
        app=app,
        resolved_config=resolved_config,
        persistence_policy=persistence_policy,
        runtime_ref=runtime_ref,
        session_store=session_store,
        upstream_health=upstream_health,
        mount_path_to_server_id=mount_path_to_server_id,
        cancellation_observer=cancellation_observer,
        upload_path_prefix=upload_route_prefix,
        upload_credentials=upload_credentials,
        artifact_download_credentials=artifact_download_credentials,
        telemetry=telemetry,
        build_memory_persistence_runtime=build_memory_persistence_runtime,
    )
    register_routes(
        app=app,
        resolved_config=resolved_config,
        proxy_map=proxy_map,
        upstream_health=upstream_health,
        persistence_policy=persistence_policy,
        runtime_ref=runtime_ref,
        session_store=session_store,
        upload_route=upload_route,
        telemetry=telemetry,
        build_memory_persistence_runtime=build_memory_persistence_runtime,
        save_upload_stream=save_upload_stream,
    )

    return app
