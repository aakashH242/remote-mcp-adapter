"""Asynchronous telemetry manager backed by OpenTelemetry metrics/logs exporters."""

from __future__ import annotations

import atexit
import asyncio
import contextlib
from dataclasses import dataclass
import logging
from typing import Any

from ..config.schemas.root import AdapterConfig
from ..config.schemas.telemetry import TelemetryConfig
from ..constants import GLOBAL_SERVER_ID
from .event_dispatch import handle_event
from .otel_bootstrap import create_metric_instruments, initialize_metrics_backend, setup_log_export

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class TelemetryEvent:
    """Queued telemetry event payload."""

    kind: str
    payload: dict[str, Any]


class AdapterTelemetry:
    """Async telemetry facade for OpenTelemetry metrics and optional log export."""

    def __init__(self, *, config: TelemetryConfig) -> None:
        """Initialize the telemetry manager.

        Args:
            config: Resolved telemetry configuration section.
        """
        self._config = config
        self._enabled = bool(config.enabled)
        self._queue: asyncio.Queue[TelemetryEvent] = asyncio.Queue(maxsize=config.max_queue_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._metrics_api_module: Any | None = None
        self._atexit_registered = False

        self._meter_provider: Any | None = None
        self._logger_provider: Any | None = None
        self._otel_log_handler: Any | None = None

        self._http_requests_total = None
        self._http_request_duration = None
        self._upload_batches_total = None
        self._upload_files_total = None
        self._upload_bytes_total = None
        self._auth_rejections_total = None
        self._upstream_tool_calls_total = None
        self._upstream_tool_call_duration = None
        self._upstream_ping_total = None
        self._upstream_ping_latency = None
        self._circuit_breaker_state = None
        self._persistence_policy_transitions_total = None
        self._nonce_operations_total = None
        self._upload_credentials_total = None
        self._artifact_downloads_total = None
        self._artifact_download_bytes_total = None
        self._artifact_download_duration = None
        self._upload_failures_total = None
        self._request_rejections_total = None
        self._adapter_wiring_runs_total = None
        self._adapter_wiring_not_ready_servers = None
        self._cleanup_cycles_total = None
        self._cleanup_removed_records_total = None
        self._sessions_lifecycle_total = None

    @classmethod
    def from_config(cls, resolved_config: AdapterConfig) -> "AdapterTelemetry":
        """Construct telemetry manager from adapter config.

        Args:
            resolved_config: Full adapter configuration.
        """
        return cls(config=resolved_config.telemetry)

    @property
    def enabled(self) -> bool:
        """Return whether telemetry emission is enabled."""
        return self._enabled

    async def start(self) -> None:
        """Initialize OTel providers and start async event worker."""
        if not self._enabled:
            return
        if self._worker_task is not None:
            return

        try:
            metrics_api, meter_provider, resource, meter = initialize_metrics_backend(config=self._config)
        except Exception:
            logger.exception("OpenTelemetry dependencies are unavailable; telemetry disabled at runtime")
            self._enabled = False
            return

        self._metrics_api_module = metrics_api
        self._meter_provider = meter_provider
        for attribute_name, instrument in create_metric_instruments(meter=meter).items():
            setattr(self, attribute_name, instrument)

        if self._config.emit_logs:
            try:
                self._logger_provider, self._otel_log_handler = setup_log_export(
                    config=self._config,
                    resource=resource,
                    root_logger=logging.getLogger(),
                )
            except Exception:
                logger.exception("Failed to initialize OpenTelemetry log exporter; continuing with metrics only")
                self._logger_provider = None
                self._otel_log_handler = None

        self._worker_task = asyncio.create_task(self._worker_loop(), name="telemetry-worker")
        if self._config.flush_on_terminate and not self._atexit_registered:
            atexit.register(self._on_process_terminate)
            self._atexit_registered = True

    async def shutdown(self) -> None:
        """Stop worker and flush/shutdown telemetry providers."""
        if self._worker_task is None:
            return

        self._stop_event.set()
        await self._queue.put(TelemetryEvent(kind="shutdown", payload={}))
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._queue.join(), timeout=self._config.shutdown_drain_timeout_seconds)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._worker_task, timeout=self._config.shutdown_drain_timeout_seconds)
        self._worker_task = None

        if self._config.flush_on_shutdown:
            self._force_flush_providers(timeout_seconds=self._config.export_timeout_seconds)

        if self._otel_log_handler is not None:
            logging.getLogger().removeHandler(self._otel_log_handler)
            self._otel_log_handler = None

        if self._logger_provider is not None:
            self._logger_provider.shutdown()
            self._logger_provider = None

        if self._meter_provider is not None:
            self._meter_provider.shutdown()
            self._meter_provider = None

    async def _enqueue(self, kind: str, payload: dict[str, Any]) -> None:
        """Enqueue one event, dropping when configured and queue is saturated.

        Args:
            kind: Event type identifier.
            payload: Event data dict.
        """
        if not self._enabled or self._worker_task is None:
            return
        event = TelemetryEvent(kind=kind, payload=payload)
        if self._config.drop_on_queue_full:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("Telemetry queue full; dropping event", extra={"kind": kind})
            return
        await self._queue.put(event)

    def _enqueue_nowait(self, kind: str, payload: dict[str, Any]) -> None:
        """Synchronous best-effort queue enqueue used by sync code paths.

        Args:
            kind: Event type identifier.
            payload: Event data dict.
        """
        if not self._enabled or self._worker_task is None:
            return
        event = TelemetryEvent(kind=kind, payload=payload)
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            if not self._config.drop_on_queue_full:
                logger.debug("Telemetry queue full; dropping sync event", extra={"kind": kind})
            return

    async def record_http_request(
        self,
        *,
        method: str,
        route_group: str,
        status_code: int,
        duration_seconds: float,
        server_id: str = GLOBAL_SERVER_ID,
    ) -> None:
        """Record one HTTP request metric event.

        Args:
            method: HTTP method (e.g. GET, POST).
            route_group: Logical route group label.
            status_code: HTTP response status code.
            duration_seconds: Request duration in seconds.
            server_id: Upstream server identifier or ``global``.
        """
        await self._enqueue(
            "http_request",
            {
                "method": method.upper(),
                "route_group": route_group,
                "status_code": status_code,
                "duration_seconds": max(0.0, float(duration_seconds)),
                "server_id": (server_id or GLOBAL_SERVER_ID),
            },
        )

    async def record_upload_batch(self, *, server_id: str, file_count: int, bytes_total: int) -> None:
        """Record accepted upload batch metrics.

        Args:
            server_id: Server that received the upload.
            file_count: Number of files in the batch.
            bytes_total: Total bytes in the batch.
        """
        await self._enqueue(
            "upload_batch",
            {
                "server_id": server_id,
                "file_count": max(0, int(file_count)),
                "bytes_total": max(0, int(bytes_total)),
            },
        )

    async def record_auth_rejection(
        self,
        *,
        reason: str,
        route_group: str,
        server_id: str = GLOBAL_SERVER_ID,
    ) -> None:
        """Record an auth rejection with bounded reason labels.

        Args:
            reason: Rejection reason label.
            route_group: Logical route group label.
            server_id: Upstream server identifier or ``global``.
        """
        await self._enqueue(
            "auth_rejection",
            {
                "reason": reason,
                "route_group": route_group,
                "server_id": (server_id or GLOBAL_SERVER_ID),
            },
        )

    async def record_upstream_tool_call(
        self,
        *,
        server_id: str,
        tool_name: str,
        result: str,
        duration_seconds: float,
    ) -> None:
        """Record one upstream tool-call outcome and latency.

        Args:
            server_id: Target server identifier.
            tool_name: Name of the tool called.
            result: Outcome label (e.g. success, error).
            duration_seconds: Call duration in seconds.
        """
        await self._enqueue(
            "upstream_tool_call",
            {
                "server_id": server_id,
                "tool_name": tool_name,
                "result": result,
                "duration_seconds": max(0.0, float(duration_seconds)),
            },
        )

    async def record_upstream_ping(
        self,
        *,
        server_id: str,
        result: str,
        latency_ms: float,
        state_before_probe: str,
    ) -> None:
        """Record active upstream ping outcome and latency.

        Args:
            server_id: Target server identifier.
            result: Ping outcome label.
            latency_ms: Ping round-trip time in milliseconds.
            state_before_probe: Breaker state before the probe.
        """
        await self._enqueue(
            "upstream_ping",
            {
                "server_id": server_id,
                "result": result,
                "latency_ms": max(0.0, float(latency_ms)),
                "state_before_probe": state_before_probe,
            },
        )

    async def record_persistence_policy_transition(
        self,
        *,
        action: str,
        source: str,
        policy: str,
        configured_backend: str,
        server_id: str = GLOBAL_SERVER_ID,
    ) -> None:
        """Record one persistence-policy transition event.

        Args:
            action: Transition action label.
            source: Transition trigger source.
            policy: Policy name.
            configured_backend: Originally configured backend.
            server_id: Upstream server identifier or ``global``.
        """
        await self._enqueue(
            "persistence_policy",
            {
                "action": action,
                "source": source,
                "policy": policy,
                "configured_backend": configured_backend,
                "server_id": (server_id or GLOBAL_SERVER_ID),
            },
        )

    def record_persistence_policy_transition_nowait(
        self,
        *,
        action: str,
        source: str,
        policy: str,
        configured_backend: str,
        server_id: str = GLOBAL_SERVER_ID,
    ) -> None:
        """Synchronous best-effort variant for policy transitions.

        Args:
            action: Transition action label.
            source: Transition trigger source.
            policy: Policy name.
            configured_backend: Originally configured backend.
        """
        self._enqueue_nowait(
            "persistence_policy",
            {
                "action": action,
                "source": source,
                "policy": policy,
                "configured_backend": configured_backend,
                "server_id": (server_id or GLOBAL_SERVER_ID),
            },
        )

    async def record_nonce_operation(
        self,
        *,
        operation: str,
        result: str,
        backend: str,
        server_id: str = GLOBAL_SERVER_ID,
    ) -> None:
        """Record reserve/consume nonce outcomes by backend.

        Args:
            operation: Nonce operation name.
            result: Operation result label.
            backend: Nonce store backend name.
        """
        await self._enqueue(
            "nonce_operation",
            {
                "operation": operation,
                "result": result,
                "backend": backend,
                "server_id": (server_id or GLOBAL_SERVER_ID),
            },
        )

    async def record_upload_credential_event(
        self,
        *,
        operation: str,
        result: str,
        backend: str,
        server_id: str = GLOBAL_SERVER_ID,
    ) -> None:
        """Record signed upload credential issue/validation outcomes.

        Args:
            operation: Credential operation name.
            result: Operation result label.
            backend: Nonce store backend name.
        """
        await self._enqueue(
            "upload_credential",
            {
                "operation": operation,
                "result": result,
                "backend": backend,
                "server_id": (server_id or GLOBAL_SERVER_ID),
            },
        )

    async def record_artifact_download(
        self,
        *,
        server_id: str,
        result: str,
        auth_mode: str,
        duration_seconds: float,
        size_bytes: int = 0,
    ) -> None:
        """Record artifact download attempts, latency, and served bytes.

        Args:
            server_id: Server identifier.
            result: Outcome label (for example ``success`` or ``not_found``).
            auth_mode: Access mode (for example ``signed_url`` or ``session_context``).
            duration_seconds: Download handling duration in seconds.
            size_bytes: Served bytes for successful downloads.
        """
        await self._enqueue(
            "artifact_download",
            {
                "server_id": (server_id or GLOBAL_SERVER_ID),
                "result": result,
                "auth_mode": auth_mode,
                "duration_seconds": max(0.0, float(duration_seconds)),
                "size_bytes": max(0, int(size_bytes)),
            },
        )

    async def record_upload_failure(self, *, server_id: str, reason: str) -> None:
        """Record one upload endpoint failure reason.

        Args:
            server_id: Server identifier.
            reason: Failure reason label.
        """
        await self._enqueue(
            "upload_failure",
            {
                "server_id": (server_id or GLOBAL_SERVER_ID),
                "reason": reason,
            },
        )

    async def record_request_rejection(
        self,
        *,
        server_id: str,
        route_group: str,
        reason: str,
        status_code: int,
    ) -> None:
        """Record one non-auth request rejection emitted by middleware.

        Args:
            server_id: Server identifier.
            route_group: Normalized route group label.
            reason: Rejection reason label.
            status_code: HTTP status code returned for the rejection.
        """
        await self._enqueue(
            "request_rejection",
            {
                "server_id": (server_id or GLOBAL_SERVER_ID),
                "route_group": route_group,
                "reason": reason,
                "status_code": int(status_code),
            },
        )

    async def record_adapter_wiring_run(
        self,
        *,
        result: str,
        total_servers: int,
        not_ready_servers: int,
        server_id: str = GLOBAL_SERVER_ID,
    ) -> None:
        """Record one adapter wiring pass summary.

        Args:
            result: Wiring pass outcome label.
            total_servers: Total configured server count.
            not_ready_servers: Servers not ready after wiring.
        """
        await self._enqueue(
            "adapter_wiring",
            {
                "result": result,
                "total_servers": max(0, int(total_servers)),
                "not_ready_servers": max(0, int(not_ready_servers)),
                "server_id": (server_id or GLOBAL_SERVER_ID),
            },
        )

    async def record_cleanup_cycle(
        self,
        *,
        result: dict[str, int],
        status: str,
        server_id: str = GLOBAL_SERVER_ID,
    ) -> None:
        """Record cleanup cycle summary and removed-record counts.

        Args:
            result: Dict mapping bucket names to removed-record counts.
            status: Cycle outcome label.
        """
        await self._enqueue(
            "cleanup_cycle",
            {
                "status": status,
                "result": {key: int(value) for key, value in result.items()},
                "server_id": (server_id or GLOBAL_SERVER_ID),
            },
        )

    async def record_session_lifecycle(self, *, event: str, server_id: str) -> None:
        """Record session lifecycle transitions (create/revive/expire/tombstone).

        Args:
            event: Lifecycle event label.
            server_id: Server the session belongs to.
        """
        await self._enqueue(
            "session_lifecycle",
            {
                "event": event,
                "server_id": server_id,
            },
        )

    async def set_circuit_breaker_state(self, *, server_id: str, state: str) -> None:
        """Record current circuit breaker state using synchronous gauge.

        Args:
            server_id: Server identifier.
            state: Breaker state label (closed, half_open, open).
        """
        await self._enqueue(
            "breaker_state",
            {
                "server_id": server_id,
                "state": state,
            },
        )

    async def _drain_event_batch(self) -> list[TelemetryEvent]:
        """Wait for one event and drain additional queued events up to batch size.

        Returns:
            List of drained events. Empty list means periodic timeout occurred.
        """
        try:
            first_event = await asyncio.wait_for(self._queue.get(), timeout=self._config.periodic_flush_seconds)
        except asyncio.TimeoutError:
            if self._meter_provider is not None or self._logger_provider is not None:
                self._force_flush_providers(timeout_seconds=self._config.export_timeout_seconds)
            return []

        drained_events = [first_event]
        batch_limit = max(1, int(self._config.queue_batch_size))
        while len(drained_events) < batch_limit:
            try:
                drained_events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return drained_events

    def _process_drained_events(self, drained_events: list[TelemetryEvent]) -> bool:
        """Process one drained event batch.

        Args:
            drained_events: Telemetry events drained from queue.

        Returns:
            True when a shutdown marker was seen in the batch.
        """
        stop_requested = False
        for event in drained_events:
            if event.kind == "shutdown":
                stop_requested = True
                self._queue.task_done()
                continue
            try:
                self._handle_event(event)
            except Exception:
                logger.exception("Failed to handle telemetry event", extra={"kind": event.kind})
            finally:
                self._queue.task_done()
        return stop_requested

    async def _worker_loop(self) -> None:
        """Drain queued telemetry events and write to OTel instruments."""
        loop = asyncio.get_running_loop()
        last_flush_at = loop.time()
        while True:
            drained_events = await self._drain_event_batch()
            if not drained_events:
                last_flush_at = loop.time()
                continue

            stop_requested = self._process_drained_events(drained_events)
            now = loop.time()
            if (now - last_flush_at) >= float(self._config.periodic_flush_seconds):
                self._force_flush_providers(timeout_seconds=self._config.export_timeout_seconds)
                last_flush_at = now

            if stop_requested:
                break

    def _force_flush_providers(self, *, timeout_seconds: int) -> None:
        """Force-flush metric/log providers with bounded best-effort timeout.

        Args:
            timeout_seconds: Maximum flush wait time in seconds.
        """
        timeout_millis = max(1, int(timeout_seconds)) * 1000
        if self._logger_provider is not None:
            with contextlib.suppress(Exception):
                try:
                    self._logger_provider.force_flush(timeout_millis=timeout_millis)
                except TypeError:
                    self._logger_provider.force_flush()
        if self._meter_provider is not None:
            with contextlib.suppress(Exception):
                try:
                    self._meter_provider.force_flush(timeout_millis=timeout_millis)
                except TypeError:
                    self._meter_provider.force_flush()

    def _on_process_terminate(self) -> None:
        """Best-effort synchronous flush for interpreter termination paths."""
        if not self._enabled:
            return
        while True:
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if event.kind != "shutdown":
                with contextlib.suppress(Exception):
                    self._handle_event(event)
            self._queue.task_done()
        if self._config.flush_on_terminate:
            self._force_flush_providers(timeout_seconds=self._config.export_timeout_seconds)

    def _handle_event(self, event: TelemetryEvent) -> None:
        """Delegate event dispatch to the event_dispatch module.

        Args:
            event: Telemetry event to handle.
        """
        handle_event(manager=self, event=event)
