"""HTTP middleware and route registration for the adapter app."""

from __future__ import annotations

from fastapi import FastAPI
from ..proxy.artifact_download_credentials import ArtifactDownloadCredentialManager
from ..proxy.upload_credentials import UploadCredentialManager
from .http_contexts import MiddlewareRegistrationContext, RouteRegistrationContext
from .middleware_registration import register_middleware_stack
from .route_registration import register_route_stack


def register_middlewares(
    *,
    app: FastAPI,
    resolved_config,
    persistence_policy,
    runtime_ref,
    session_store,
    upstream_health,
    mount_path_to_server_id,
    cancellation_observer,
    upload_path_prefix,
    upload_credentials: UploadCredentialManager | None,
    artifact_download_credentials: ArtifactDownloadCredentialManager | None,
    telemetry=None,
    build_memory_persistence_runtime,
) -> None:
    """Register HTTP middleware stack.

    Constructs a ``MiddlewareRegistrationContext`` from the provided
    dependencies and delegates to ``register_middleware_stack``.

    Args:
        app: FastAPI application instance.
        resolved_config: Full adapter configuration.
        persistence_policy: Policy controller tracking persistence state.
        runtime_ref: Mutable dict holding the ``current`` runtime.
        session_store: Session store for state management.
        upstream_health: Per-server upstream health monitors.
        mount_path_to_server_id: Mapping of mount paths to server identifiers.
        cancellation_observer: In-band cancellation notification bus.
        upload_path_prefix: Bare upload route prefix.
        upload_credentials: Optional upload credential manager.
        artifact_download_credentials: Optional artifact download credential manager.
        telemetry: Optional telemetry recorder.
        build_memory_persistence_runtime: Factory for in-memory fallback runtime.
    """
    middleware_context = MiddlewareRegistrationContext(
        app=app,
        resolved_config=resolved_config,
        persistence_policy=persistence_policy,
        runtime_ref=runtime_ref,
        session_store=session_store,
        upstream_health=upstream_health,
        mount_path_to_server_id=mount_path_to_server_id,
        cancellation_observer=cancellation_observer,
        upload_path_prefix=upload_path_prefix,
        upload_credentials=upload_credentials,
        artifact_download_credentials=artifact_download_credentials,
        telemetry=telemetry,
        build_memory_persistence_runtime=build_memory_persistence_runtime,
    )
    register_middleware_stack(context=middleware_context)


def register_routes(
    *,
    app: FastAPI,
    resolved_config,
    proxy_map,
    upstream_health,
    persistence_policy,
    runtime_ref,
    session_store,
    upload_route,
    telemetry=None,
    build_memory_persistence_runtime,
    save_upload_stream,
) -> None:
    """Register adapter HTTP routes.

    Constructs a ``RouteRegistrationContext`` from the provided
    dependencies and delegates to ``register_route_stack``.

    Args:
        app: FastAPI application instance.
        resolved_config: Full adapter configuration.
        proxy_map: Mapping of server ID to ``ProxyMount`` instances.
        upstream_health: Per-server upstream health monitors.
        persistence_policy: Policy controller tracking persistence state.
        runtime_ref: Mutable dict holding the ``current`` runtime.
        session_store: Session store for state management.
        upload_route: Parameterized upload route path template.
        telemetry: Optional telemetry recorder.
        build_memory_persistence_runtime: Factory for in-memory fallback runtime.
        save_upload_stream: Callable to persist an upload stream to disk.
    """
    route_context = RouteRegistrationContext(
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
    register_route_stack(context=route_context)
