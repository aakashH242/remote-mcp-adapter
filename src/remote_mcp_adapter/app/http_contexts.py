"""Shared context dataclasses for HTTP middleware and route registration."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI

from ..proxy.artifact_download_credentials import ArtifactDownloadCredentialManager
from ..proxy.upload_credentials import UploadCredentialManager


@dataclass(slots=True)
class MiddlewareRegistrationContext:
    """Shared dependencies required to register HTTP middleware stack.

    Attributes:
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

    app: FastAPI
    resolved_config: object
    persistence_policy: object
    runtime_ref: dict[str, object]
    session_store: object
    upstream_health: dict[str, object]
    mount_path_to_server_id: dict[str, str]
    cancellation_observer: object
    upload_path_prefix: str
    upload_credentials: UploadCredentialManager | None
    artifact_download_credentials: ArtifactDownloadCredentialManager | None
    telemetry: object | None
    build_memory_persistence_runtime: object


@dataclass(slots=True)
class RouteRegistrationContext:
    """Shared dependencies required to register HTTP routes.

    Attributes:
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

    app: FastAPI
    resolved_config: object
    proxy_map: dict[str, object]
    upstream_health: dict[str, object]
    persistence_policy: object
    runtime_ref: dict[str, object]
    session_store: object
    upload_route: str
    telemetry: object | None
    build_memory_persistence_runtime: object
    save_upload_stream: object
