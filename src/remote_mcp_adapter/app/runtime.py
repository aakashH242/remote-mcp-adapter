"""Runtime helper functions for app startup, persistence, and background workers."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os

from fastapi import FastAPI

from ..config import AdapterConfig
from ..core import PersistencePolicyController
from ..core.persistence.redis_persistence_health import RedisPersistenceHealthMonitor
from ..core.persistence.startup_reconciliation import run_startup_state_reconciliation
from ..core.storage.store import SessionStore
from ..core.storage.write_policy import set_redis_storage_lock_provider
from ..proxy.factory import ProxyMount
from ..proxy.hooks import AdapterWireState, wire_adapters
from ..proxy.artifact_download_credentials import ArtifactDownloadCredentialManager
from ..proxy.upload_credentials import UploadCredentialManager
from ..proxy.upstream_health import UpstreamHealthMonitor, resolve_upstream_ping_policy

logger = logging.getLogger(__name__)
_CLEANUP_RESTART_MAX_BACKOFF_SECONDS = 30


def terminate_process_for_policy_exit() -> None:
    """Hard-kill the process when the persistence unavailability policy demands it."""
    logger.critical("Terminating process due to persistence unavailable_policy=exit")
    os._exit(1)


def build_upstream_health_monitors(
    config: AdapterConfig,
    proxy_map: dict[str, ProxyMount],
    telemetry=None,
) -> dict[str, UpstreamHealthMonitor]:
    """Build per-server health monitors from config and resolved proxy mounts.

    Args:
        config: Full adapter configuration carrying per-server ping overrides.
        proxy_map: Mapping of server ID to ProxyMount instances.

    Returns:
        Dict of server ID to UpstreamHealthMonitor.
    """
    monitors: dict[str, UpstreamHealthMonitor] = {}
    for server in config.servers:
        mount = proxy_map[server.id]
        policy = resolve_upstream_ping_policy(
            core_defaults=config.core.upstream_ping,
            server_overrides=server.upstream_ping,
        )
        monitors[server.id] = UpstreamHealthMonitor(
            server_id=server.id,
            mount_path=server.mount_path,
            upstream_url=server.upstream.url,
            policy=policy,
            client_registry=mount.clients,
            telemetry=telemetry,
        )
    return monitors


async def collect_upstream_health_checks(
    upstream_health: dict[str, UpstreamHealthMonitor],
) -> list[dict[str, object]]:
    """Concurrently snapshot health state from every upstream monitor.

    Args:
        upstream_health: Mapping of server ID to UpstreamHealthMonitor.

    Returns:
        List of health-snapshot dicts, one per monitor.
    """
    return await asyncio.gather(*(monitor.health_snapshot() for monitor in upstream_health.values()))


async def run_upstream_health_monitor(monitor: UpstreamHealthMonitor) -> None:
    """Drive one monitor's run loop, absorbing unexpected errors without crashing the task.

    Args:
        monitor: Health monitor instance to run.
    """
    try:
        await monitor.run_loop()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Upstream health monitor crashed", extra={"server_id": monitor.server_id})


async def activate_memory_persistence_fallback(
    *,
    runtime_ref: dict[str, object],
    session_store: SessionStore,
    app: FastAPI,
    build_memory_persistence_runtime,
) -> None:
    """Swap in-memory storage backends in-place when Redis is no longer viable.

    Args:
        runtime_ref: Mutable dict holding the current runtime under the ``current`` key.
        session_store: The live session store whose backends will be replaced.
        app: FastAPI application whose ``state.persistence_runtime`` will be updated.
        build_memory_persistence_runtime: Callable that constructs a new in-memory runtime.
    """
    current_runtime = runtime_ref["current"]
    if getattr(current_runtime, "backend_type", None) == "memory":
        return
    previous_backend = getattr(current_runtime, "backend_type", "unknown")
    fallback_runtime = build_memory_persistence_runtime()
    session_store.replace_backends(
        state_repository=fallback_runtime.state_repository,
        lock_provider=fallback_runtime.lock_provider,
    )
    runtime_ref["current"] = fallback_runtime
    app.state.persistence_runtime = fallback_runtime
    upload_credentials = getattr(app.state, "upload_credentials", None)
    if isinstance(upload_credentials, UploadCredentialManager):
        previous_backend = upload_credentials.nonce_backend
        upload_credentials.use_memory_nonce_store()
        logger.warning(
            "Upload nonce replay protection downgraded to in-memory backend after persistence fallback",
            extra={"previous_backend": previous_backend, "current_backend": upload_credentials.nonce_backend},
        )
    logger.info(
        "Persistence backend switched to in-memory fallback",
        extra={"previous_backend": previous_backend, "current_backend": fallback_runtime.backend_type},
    )
    set_redis_storage_lock_provider(None)
    await current_runtime.close()


async def run_redis_persistence_monitor(
    *,
    monitor: RedisPersistenceHealthMonitor,
    policy_controller: PersistencePolicyController,
    runtime_ref: dict[str, object],
    session_store: SessionStore,
    app: FastAPI,
    build_memory_persistence_runtime,
) -> None:
    """Poll Redis health on an interval and apply the configured failure policy.

    Switches to the memory fallback when the policy is ``switch_to_fallback`` and
    terminates the process when the policy is ``exit``.

    Args:
        monitor: Redis health monitor instance that performs the ping.
        policy_controller: Tracks failure counts and resolves the policy action.
        runtime_ref: Mutable dict holding the ``current`` runtime.
        session_store: Live session store for backend replacement on fallback.
        app: FastAPI app whose ``state.persistence_runtime`` is kept in sync.
        build_memory_persistence_runtime: Callable that constructs a new in-memory runtime.

    Raises:
        asyncio.CancelledError: Re-raised on task cancellation.
    """
    previous_status: str | None = None
    try:
        while True:
            await monitor.run_once()
            snapshot = await monitor.health_snapshot()
            current_status = str(snapshot.get("status") or "unknown")
            if current_status != previous_status:
                logger.info(
                    "Redis persistence health status changed",
                    extra={"previous_status": previous_status, "current_status": current_status},
                )
                previous_status = current_status
            if snapshot.get("status") == "ok":
                policy_controller.handle_runtime_recovery(component="redis_persistence_health")
            else:
                error = str(((snapshot.get("ping") or {}).get("last_error")) or "Redis ping failure.")
                action = policy_controller.handle_runtime_failure(
                    component="redis_persistence_health",
                    error=error,
                )
                logger.info(
                    "Applied persistence runtime failure policy action",
                    extra={"component": "redis_persistence_health", "action": action},
                )
                if action == "switch_to_fallback":
                    await activate_memory_persistence_fallback(
                        runtime_ref=runtime_ref,
                        session_store=session_store,
                        app=app,
                        build_memory_persistence_runtime=build_memory_persistence_runtime,
                    )
                    return
                if action == "exit":
                    terminate_process_for_policy_exit()
                    return
            await asyncio.sleep(monitor.interval_seconds)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Redis persistence health monitor crashed")


async def apply_runtime_failure_policy(
    *,
    policy_controller: PersistencePolicyController,
    runtime_ref: dict[str, object],
    session_store: SessionStore,
    app: FastAPI,
    component: str,
    error: str,
    build_memory_persistence_runtime,
) -> None:
    """Dispatch the persistence failure policy action for the given component and error.

    Args:
        policy_controller: Tracks failure history and resolves the policy action.
        runtime_ref: Mutable dict holding the ``current`` runtime.
        session_store: Live session store for backend replacement on fallback.
        app: FastAPI app whose ``state.persistence_runtime`` is kept in sync.
        component: Name of the component that reported the failure.
        error: Error string describing the failure.
        build_memory_persistence_runtime: Callable that constructs a new in-memory runtime.
    """
    action = policy_controller.handle_runtime_failure(component=component, error=error)
    if action == "switch_to_fallback":
        await activate_memory_persistence_fallback(
            runtime_ref=runtime_ref,
            session_store=session_store,
            app=app,
            build_memory_persistence_runtime=build_memory_persistence_runtime,
        )
    elif action == "exit":
        terminate_process_for_policy_exit()


async def run_startup_reconciliation(
    *,
    config: AdapterConfig,
    policy_controller: PersistencePolicyController,
    runtime_ref: dict[str, object],
    session_store: SessionStore,
    app: FastAPI,
    build_memory_persistence_runtime,
) -> dict[str, object]:
    """Run startup state reconciliation, falling back to memory on policy-driven failure.

    Args:
        config: Full adapter config carrying reconciliation settings.
        policy_controller: Resolves startup failure policy actions.
        runtime_ref: Mutable dict holding the ``current`` runtime.
        session_store: Live session store for backend replacement on fallback.
        app: FastAPI app whose ``state.persistence_runtime`` is kept in sync.
        build_memory_persistence_runtime: Callable that constructs a new in-memory runtime.

    Returns:
        Reconciliation result dict with ``status``, ``mode``, and optional ``detail``.

    Raises:
        Exception: Re-raised when the policy action is ``exit``.
    """
    try:
        return await run_startup_state_reconciliation(
            config=config,
            state_repository=runtime_ref["current"].state_repository,
        )
    except Exception as exc:
        logger.exception("Startup reconciliation failed")
        startup_action = policy_controller.handle_startup_failure(
            phase="startup_reconciliation",
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
            try:
                return await run_startup_state_reconciliation(
                    config=config,
                    state_repository=runtime_ref["current"].state_repository,
                )
            except Exception as fallback_exc:
                logger.exception("Startup reconciliation failed after fallback")
                return {
                    "status": "error",
                    "mode": config.state_persistence.reconciliation.mode,
                    "reason": "startup_reconciliation_failed_after_fallback",
                    "detail": str(fallback_exc),
                }
        return {
            "status": "error",
            "mode": config.state_persistence.reconciliation.mode,
            "reason": "startup_reconciliation_failed",
            "detail": str(exc),
        }


async def cleanup_worker(
    *,
    session_store: SessionStore,
    interval_seconds: int,
    stop_event: asyncio.Event,
    telemetry=None,
) -> None:
    """Run cleanup cycles until asked to stop.

    Args:
        session_store: Session store providing the ``cleanup_once`` method.
        interval_seconds: Seconds to wait between cleanup cycles.
        stop_event: Event signalling the worker should exit.
        telemetry: Optional telemetry recorder.
    """
    while True:
        if stop_event.is_set():
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            return
        except asyncio.TimeoutError:
            pass

        try:
            result = await session_store.cleanup_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cleanup cycle failed; continuing cleanup worker")
            if telemetry is not None:
                await telemetry.record_cleanup_cycle(result={}, status="error")
            continue

        if telemetry is not None:
            await telemetry.record_cleanup_cycle(result=result, status="ok")

        logger.debug("cleanup cycle heartbeat", extra=result)
        if any(value for value in result.values()):
            logger.info("cleanup cycle", extra=result)


async def run_cleanup_supervisor(
    *,
    session_store: SessionStore,
    interval_seconds: int,
    stop_event: asyncio.Event,
    telemetry=None,
) -> None:
    """Keep cleanup worker alive by restarting it on unexpected exits.

    Uses exponential backoff (capped at 30 s) between restart attempts.

    Args:
        session_store: Session store providing the ``cleanup_once`` method.
        interval_seconds: Seconds between cleanup cycles within the worker.
        stop_event: Event signalling the supervisor should exit.
        telemetry: Optional telemetry recorder.
    """
    restart_count = 0
    while not stop_event.is_set():
        worker = asyncio.create_task(
            cleanup_worker(
                session_store=session_store,
                interval_seconds=interval_seconds,
                stop_event=stop_event,
                telemetry=telemetry,
            ),
            name="session-store-cleanup-worker",
        )
        try:
            await worker
            if stop_event.is_set():
                return
            restart_count += 1
            logger.info(
                "Cleanup worker exited unexpectedly; restarting",
                extra={"restart_count": restart_count},
            )
        except asyncio.CancelledError:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            if stop_event.is_set():
                raise
            task = asyncio.current_task()
            if task is not None:
                task.uncancel()
            restart_count += 1
            logger.info(
                "Cleanup supervisor cancelled unexpectedly; restarting",
                extra={"restart_count": restart_count},
            )
        except Exception:
            restart_count += 1
            logger.exception(
                "Cleanup worker crashed; restarting",
                extra={"restart_count": restart_count},
            )

        backoff_seconds = min(2 ** min(restart_count, 5), _CLEANUP_RESTART_MAX_BACKOFF_SECONDS)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=backoff_seconds)
        except asyncio.TimeoutError:
            continue


async def wire_adapters_until_ready(
    *,
    config: AdapterConfig,
    proxy_map: dict[str, ProxyMount],
    session_store: SessionStore,
    state: AdapterWireState,
    upload_credentials: UploadCredentialManager | None = None,
    artifact_download_credentials: ArtifactDownloadCredentialManager | None = None,
    telemetry=None,
    retry_interval_seconds: int = 15,
) -> dict[str, bool]:
    """Retry adapter registration until all configured servers are reachable.

    Args:
        config: Full adapter configuration.
        proxy_map: Mapping of server ID to ``ProxyMount`` instances.
        session_store: Session store for state management.
        state: Idempotent wiring state tracker.
        upload_credentials: Optional upload credential manager.
        artifact_download_credentials: Optional artifact download credential manager.
        telemetry: Optional telemetry recorder.
        retry_interval_seconds: Seconds between retry attempts.

    Returns:
        Final per-server status dict mapping server ID to readiness bool.
    """
    attempt = 0
    while True:
        attempt += 1
        status_map = await wire_adapters(
            config=config,
            proxy_map=proxy_map,
            store=session_store,
            state=state,
            upload_credentials=upload_credentials,
            artifact_download_credentials=artifact_download_credentials,
            telemetry=telemetry,
        )
        if telemetry is not None:
            not_ready_count = len([server_id for server_id, ready in status_map.items() if not ready])
            await telemetry.record_adapter_wiring_run(
                result="ready" if status_map and not not_ready_count else "retry",
                total_servers=len(status_map),
                not_ready_servers=not_ready_count,
            )
        if status_map and all(status_map.values()):
            logger.info(
                "Adapter wiring complete",
                extra={"servers": list(status_map.keys()), "attempt": attempt},
            )
            return status_map
        await asyncio.sleep(retry_interval_seconds)
