"""Application lifespan construction and background task orchestration."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager, suppress
import logging

from ..proxy.hooks import AdapterWireState, wire_adapters
from ..core.storage.write_policy import set_redis_storage_lock_provider
from ..log_redaction import install_log_redaction_filter
from .runtime import (
    activate_memory_persistence_fallback,
    run_cleanup_supervisor,
    run_redis_persistence_monitor,
    run_startup_reconciliation,
    run_upstream_health_monitor,
    wire_adapters_until_ready,
)
from .runtime_upstream_helpers import build_startup_readiness, wait_for_upstream_readiness

logger = logging.getLogger(__name__)


def build_lifespan(
    *,
    resolved_config,
    runtime_ref,
    session_store,
    proxy_map,
    upstream_health,
    write_policy_lock_mode,
    persistence_policy,
    mounted_http_apps,
    upload_credentials,
    artifact_download_credentials,
    telemetry,
    build_memory_persistence_runtime,
):
    """Build app lifespan context manager with startup/shutdown orchestration.

    Constructs a FastAPI lifespan async context manager that orchestrates
    startup probing, adapter wiring, cleanup supervision, health monitoring,
    Redis persistence monitoring, and graceful shutdown.

    Args:
        resolved_config: Full adapter configuration.
        runtime_ref: Mutable dict holding the ``current`` persistence runtime.
        session_store: Session store for state management.
        proxy_map: Mapping of server ID to ``ProxyMount`` instances.
        upstream_health: Per-server upstream health monitors.
        write_policy_lock_mode: Resolved storage lock mode.
        persistence_policy: Policy controller tracking persistence state.
        mounted_http_apps: Per-server mounted FastAPI sub-applications.
        upload_credentials: Optional upload credential manager.
        artifact_download_credentials: Optional artifact download credential manager.
        telemetry: Optional telemetry recorder.
        build_memory_persistence_runtime: Factory for in-memory fallback runtime.

    Returns:
        Async context manager suitable for FastAPI's ``lifespan`` parameter.
    """

    @asynccontextmanager
    async def lifespan(app):
        """Orchestrate startup, background workers, and graceful shutdown.

        Args:
            app: FastAPI application instance.
        """
        cleanup_task: asyncio.Task[None] | None = None
        cleanup_stop_event: asyncio.Event | None = None
        wire_task: asyncio.Task[None] | None = None
        health_tasks: list[asyncio.Task[None]] = []
        redis_persistence_health_task: asyncio.Task[None] | None = None
        memory_snapshot_task: asyncio.Task[None] | None = None
        memory_snapshot_stop_event: asyncio.Event | None = None
        async with AsyncExitStack() as stack:
            # Install at startup so uvicorn handlers and any dynamic handlers are covered.
            install_log_redaction_filter(config=resolved_config)
            if telemetry is not None and getattr(telemetry, "enabled", False):
                await telemetry.start()
                # Telemetry startup may register additional handlers (for example OTel logs).
                install_log_redaction_filter(config=resolved_config)
            active_runtime = runtime_ref["current"]
            if write_policy_lock_mode == "redis" and getattr(active_runtime, "backend_type", None) != "memory":
                set_redis_storage_lock_provider(active_runtime.lock_provider)
            else:
                set_redis_storage_lock_provider(None)
            for mounted in mounted_http_apps.values():
                await stack.enter_async_context(mounted.lifespan(app))
            try:
                await runtime_ref["current"].state_repository.session_count()
            except Exception as exc:
                startup_action = persistence_policy.handle_startup_failure(
                    phase="startup_probe",
                    error=str(exc),
                )
                if startup_action == "exit":
                    raise
                if startup_action == "switch_to_fallback":
                    await activate_memory_persistence_fallback(
                        runtime_ref=runtime_ref,
                        session_store=session_store,
                        app=app,
                        build_memory_persistence_runtime=build_memory_persistence_runtime,
                    )
            app.state.startup_reconciliation = await run_startup_reconciliation(
                config=resolved_config,
                policy_controller=persistence_policy,
                runtime_ref=runtime_ref,
                session_store=session_store,
                app=app,
                build_memory_persistence_runtime=build_memory_persistence_runtime,
            )

            startup_checks, waited_seconds = await wait_for_upstream_readiness(
                proxy_map=proxy_map,
                max_wait_seconds=resolved_config.core.max_start_wait_seconds,
            )
            startup_readiness = build_startup_readiness(
                max_wait_seconds=resolved_config.core.max_start_wait_seconds,
                waited_seconds=waited_seconds,
                checks=startup_checks,
            )
            app.state.startup_readiness = startup_readiness
            if startup_readiness["ready_within_wait_budget"]:
                logger.info(
                    "All upstreams became ready during startup wait",
                    extra={"waited_seconds": startup_readiness["waited_seconds"]},
                )
            else:
                not_ready_servers = ", ".join(startup_readiness.get("not_ready_servers", [])) or "unknown"
                logger.warning(
                    "Startup wait elapsed before all upstreams were ready; running in degraded mode (not_ready_servers=%s)",
                    not_ready_servers,
                    extra=startup_readiness,
                )

            adapter_wire_state = AdapterWireState()
            initial_wire_status = await wire_adapters(
                config=resolved_config,
                proxy_map=proxy_map,
                store=session_store,
                state=adapter_wire_state,
                upload_credentials=upload_credentials,
                artifact_download_credentials=artifact_download_credentials,
                telemetry=telemetry,
            )
            app.state.adapter_wiring = {
                "status_by_server": initial_wire_status,
                "ready": bool(initial_wire_status) and all(initial_wire_status.values()),
            }
            if not app.state.adapter_wiring["ready"]:

                async def wire_until_ready() -> None:
                    """Retry adapter wiring until all servers confirm readiness."""
                    status_map = await wire_adapters_until_ready(
                        config=resolved_config,
                        proxy_map=proxy_map,
                        session_store=session_store,
                        state=adapter_wire_state,
                        upload_credentials=upload_credentials,
                        artifact_download_credentials=artifact_download_credentials,
                        telemetry=telemetry,
                    )
                    app.state.adapter_wiring = {
                        "status_by_server": status_map,
                        "ready": True,
                    }

                wire_task = asyncio.create_task(wire_until_ready())
            if resolved_config.core.cleanup_interval_seconds is not None:
                cleanup_stop_event = asyncio.Event()
                cleanup_task = asyncio.create_task(
                    run_cleanup_supervisor(
                        session_store=session_store,
                        interval_seconds=resolved_config.core.cleanup_interval_seconds,
                        stop_event=cleanup_stop_event,
                        telemetry=telemetry,
                    ),
                    name="session-store-cleanup-supervisor",
                )
            await asyncio.gather(*(monitor.run_once() for monitor in upstream_health.values()))
            for monitor in upstream_health.values():
                if not monitor.enabled:
                    continue
                health_tasks.append(asyncio.create_task(run_upstream_health_monitor(monitor)))
            redis_persistence_monitor = runtime_ref["current"].redis_health_monitor
            if redis_persistence_monitor is not None:
                redis_persistence_health_task = asyncio.create_task(
                    run_redis_persistence_monitor(
                        monitor=redis_persistence_monitor,
                        policy_controller=persistence_policy,
                        runtime_ref=runtime_ref,
                        session_store=session_store,
                        app=app,
                        build_memory_persistence_runtime=build_memory_persistence_runtime,
                    ),
                    name="redis-persistence-health-monitor",
                )
            memory_snapshot_manager = runtime_ref["current"].memory_snapshot_manager
            if memory_snapshot_manager is not None:
                memory_snapshot_stop_event = asyncio.Event()
                try:
                    await memory_snapshot_manager.run_once()
                except Exception:
                    logger.exception("Initial memory snapshot failed; periodic snapshots will continue")
                memory_snapshot_task = asyncio.create_task(
                    memory_snapshot_manager.run_loop(memory_snapshot_stop_event),
                    name="memory-state-snapshot-manager",
                )
            try:
                yield
            finally:
                if memory_snapshot_task is not None:
                    if memory_snapshot_stop_event is not None:
                        memory_snapshot_stop_event.set()
                    memory_snapshot_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await memory_snapshot_task
                if redis_persistence_health_task is not None:
                    redis_persistence_health_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await redis_persistence_health_task
                for health_task in health_tasks:
                    health_task.cancel()
                for health_task in health_tasks:
                    try:
                        await health_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.debug("Upstream health task exited with error during shutdown", exc_info=True)
                if wire_task is not None:
                    wire_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await wire_task
                if cleanup_task is not None:
                    if cleanup_stop_event is not None:
                        cleanup_stop_event.set()
                        cleanup_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await cleanup_task
                await asyncio.gather(*(mount.clients.close_all() for mount in proxy_map.values()))
                await session_store.shutdown()
                await runtime_ref["current"].close()
                set_redis_storage_lock_provider(None)
                if telemetry is not None and getattr(telemetry, "enabled", False):
                    await telemetry.shutdown()

    return lifespan
