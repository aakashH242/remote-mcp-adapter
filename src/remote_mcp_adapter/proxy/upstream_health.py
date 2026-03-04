"""Active upstream ping monitoring with per-server circuit-breaker state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Literal

from ..config import UpstreamPingConfig, UpstreamPingOverridesConfig
from .factory import SessionClientRegistry

logger = logging.getLogger(__name__)
BreakerState = Literal["closed", "open", "half_open"]


@dataclass(slots=True, frozen=True)
class ResolvedUpstreamPingPolicy:
    """Resolved ping policy for one server after applying overrides."""

    enabled: bool
    interval_seconds: int
    timeout_seconds: int
    failure_threshold: int
    open_cooldown_seconds: int
    half_open_probe_allowance: int


def _first_defined(*values):
    """Return the first non-None value from the sequence, or None if all are None.

    Args:
        *values: Values to check in order.
    """
    for value in values:
        if value is not None:
            return value
    return None


def resolve_upstream_ping_policy(
    *,
    core_defaults: UpstreamPingConfig,
    server_overrides: UpstreamPingOverridesConfig,
) -> ResolvedUpstreamPingPolicy:
    """Resolve ping policy using server override precedence over core defaults.

    Args:
        core_defaults: Core-level ping configuration.
        server_overrides: Server-level ping override configuration.
    """
    return ResolvedUpstreamPingPolicy(
        enabled=bool(_first_defined(server_overrides.enabled, core_defaults.enabled, True)),
        interval_seconds=int(_first_defined(server_overrides.interval_seconds, core_defaults.interval_seconds, 15)),
        timeout_seconds=int(_first_defined(server_overrides.timeout_seconds, core_defaults.timeout_seconds, 5)),
        failure_threshold=int(_first_defined(server_overrides.failure_threshold, core_defaults.failure_threshold, 3)),
        open_cooldown_seconds=int(
            _first_defined(server_overrides.open_cooldown_seconds, core_defaults.open_cooldown_seconds, 30)
        ),
        half_open_probe_allowance=int(
            _first_defined(
                server_overrides.half_open_probe_allowance,
                core_defaults.half_open_probe_allowance,
                2,
            )
        ),
    )


class UpstreamHealthMonitor:
    """Per-upstream active ping loop with explicit circuit-breaker transitions."""

    def __init__(
        self,
        *,
        server_id: str,
        mount_path: str,
        upstream_url: str,
        policy: ResolvedUpstreamPingPolicy,
        client_registry: SessionClientRegistry,
        telemetry=None,
    ) -> None:
        """Initialize the health monitor.

        Args:
            server_id: Server identifier being monitored.
            mount_path: Mount path of the server proxy.
            upstream_url: Upstream server URL.
            policy: Resolved ping policy configuration.
            client_registry: Session client registry for probe clients.
            telemetry: Optional telemetry manager.
        """
        self._server_id = server_id
        self._mount_path = mount_path
        self._upstream_url = upstream_url
        self._policy = policy
        self._clients = client_registry
        self._telemetry = telemetry
        self._lock = asyncio.Lock()

        self._state: BreakerState = "closed"
        self._consecutive_failures = 0
        self._half_open_probe_count = 0
        self._half_open_success_count = 0
        self._opened_at_monotonic: float | None = None

        self._last_ping_latency_ms: float | None = None
        self._last_ping_error: str | None = None
        self._last_ping_success_at_epoch: float | None = None
        self._last_ping_failure_at_epoch: float | None = None

    @property
    def enabled(self) -> bool:
        """Indicate whether active pinging is configured for this upstream."""
        return self._policy.enabled

    @property
    def server_id(self) -> str:
        """Return the server id this monitor is tracking."""
        return self._server_id

    def _transition_to_open_locked(self, now_monotonic: float) -> None:
        """Open the circuit breaker and record when it opened.

        Args:
            now_monotonic: Current monotonic timestamp.
        """
        self._state = "open"
        self._opened_at_monotonic = now_monotonic
        self._half_open_probe_count = 0
        self._half_open_success_count = 0

    def _transition_to_half_open_locked(self) -> None:
        """Transition to half-open state to allow limited probe requests."""
        self._state = "half_open"
        self._opened_at_monotonic = None
        self._half_open_probe_count = 0
        self._half_open_success_count = 0

    def _transition_to_closed_locked(self) -> None:
        """Close the circuit breaker and reset all failure/probe counters."""
        self._state = "closed"
        self._opened_at_monotonic = None
        self._consecutive_failures = 0
        self._half_open_probe_count = 0
        self._half_open_success_count = 0

    def _advance_state_for_time_locked(self, now_monotonic: float) -> None:
        """Promote an open breaker to half-open once the cooldown window has elapsed.

        Args:
            now_monotonic: Current monotonic timestamp.
        """
        if self._state != "open" or self._opened_at_monotonic is None:
            return
        elapsed = now_monotonic - self._opened_at_monotonic
        if elapsed >= self._policy.open_cooldown_seconds:
            self._transition_to_half_open_locked()
            logger.info(
                "Circuit breaker transitioning from open to half_open after cooldown for server '%s' on '%s'",
                self._server_id,
                self._mount_path,
                extra={
                    "server_id": self._server_id,
                    "session_id": "health-probe",
                    "cooldown_seconds": self._policy.open_cooldown_seconds,
                },
            )

    async def _emit_breaker_state(self, state: BreakerState) -> None:
        """Best-effort telemetry emission for breaker-state gauge updates.

        Args:
            state: Current breaker state label.
        """
        if self._telemetry is None or not getattr(self._telemetry, "enabled", False):
            return
        await self._telemetry.set_circuit_breaker_state(server_id=self._server_id, state=state)

    async def _begin_probe(self) -> tuple[bool, BreakerState]:
        """Decide under lock whether a probe should fire, advancing time-based state.

        Returns:
            Tuple of (should_probe, state_before_probe).
        """
        async with self._lock:
            now_monotonic = time.monotonic()
            self._advance_state_for_time_locked(now_monotonic)
            if self._state == "open":
                return False, self._state
            if self._state == "half_open":
                if self._half_open_probe_count >= self._policy.half_open_probe_allowance:
                    return False, self._state
                self._half_open_probe_count += 1
            return True, self._state

    async def run_once(self) -> None:
        """Run a single ping evaluation cycle."""
        if not self._policy.enabled:
            return

        should_probe, state_before_probe = await self._begin_probe()
        if not should_probe:
            return

        start = time.perf_counter()
        try:
            client = self._clients.build_probe_client(timeout_seconds=self._policy.timeout_seconds)
            async with client:
                is_alive = await client.ping()
            if not is_alive:
                raise RuntimeError("Ping did not return an empty response result.")
        except Exception as exc:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            await self._record_failure(exc=exc, latency_ms=latency_ms, prior_state=state_before_probe)
            return

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        await self._record_success(latency_ms=latency_ms, prior_state=state_before_probe)

    async def run_loop(self) -> None:
        """Run periodic active ping checks until cancelled."""
        while True:
            await self.run_once()
            await asyncio.sleep(self._policy.interval_seconds)

    async def _record_success(self, *, latency_ms: float, prior_state: BreakerState) -> None:
        """Record a successful ping, resetting failure counts and closing the breaker if warranted.

        Args:
            latency_ms: Ping round-trip time in milliseconds.
            prior_state: Breaker state before the probe.
        """
        closed_from_half_open = False
        recovered_from_failures = False
        async with self._lock:
            self._last_ping_latency_ms = latency_ms
            self._last_ping_error = None
            self._last_ping_success_at_epoch = time.time()

            if self._state == "closed":
                recovered_from_failures = self._consecutive_failures > 0
                self._consecutive_failures = 0
            elif self._state == "half_open":
                self._half_open_success_count += 1
                if self._half_open_success_count >= self._policy.half_open_probe_allowance:
                    self._transition_to_closed_locked()
                    closed_from_half_open = True

        logger.debug(
            "Upstream ping success for server '%s' on '%s'",
            self._server_id,
            self._mount_path,
            extra={
                "server_id": self._server_id,
                "session_id": "health-probe",
                "latency_ms": latency_ms,
                "state_before_probe": prior_state,
            },
        )
        if self._telemetry is not None and getattr(self._telemetry, "enabled", False):
            await self._telemetry.record_upstream_ping(
                server_id=self._server_id,
                result="success",
                latency_ms=latency_ms,
                state_before_probe=prior_state,
            )
            await self._emit_breaker_state(self._state)
        if recovered_from_failures:
            logger.info(
                "Upstream recovered after prior ping failures for server '%s' on '%s'",
                self._server_id,
                self._mount_path,
                extra={
                    "server_id": self._server_id,
                    "session_id": "health-probe",
                    "latency_ms": latency_ms,
                },
            )
        if closed_from_half_open:
            logger.info(
                "Circuit breaker closed after successful half-open probes for server '%s' on '%s'",
                self._server_id,
                self._mount_path,
                extra={
                    "server_id": self._server_id,
                    "session_id": "health-probe",
                    "latency_ms": latency_ms,
                    "probe_allowance": self._policy.half_open_probe_allowance,
                },
            )

    async def _record_failure(
        self,
        *,
        exc: Exception,
        latency_ms: float,
        prior_state: BreakerState,
    ) -> None:
        """Record a failed ping, opening the breaker at threshold and resetting cached clients.

        Args:
            exc: The exception raised by the ping.
            latency_ms: Ping round-trip time in milliseconds.
            prior_state: Breaker state before the probe.
        """
        open_triggered = False
        failure_count = 0
        async with self._lock:
            now_monotonic = time.monotonic()
            self._last_ping_latency_ms = latency_ms
            self._last_ping_error = str(exc)
            self._last_ping_failure_at_epoch = time.time()

            if self._state == "closed":
                self._consecutive_failures += 1
                failure_count = self._consecutive_failures
                if self._consecutive_failures >= self._policy.failure_threshold:
                    self._transition_to_open_locked(now_monotonic)
                    open_triggered = True
            elif self._state == "half_open":
                self._transition_to_open_locked(now_monotonic)
                open_triggered = True
                failure_count = self._policy.failure_threshold
            else:
                self._opened_at_monotonic = now_monotonic
                open_triggered = True
                failure_count = self._policy.failure_threshold

        logger.warning(
            "Upstream ping failure for server '%s' on '%s'",
            self._server_id,
            self._mount_path,
            extra={
                "server_id": self._server_id,
                "session_id": "health-probe",
                "latency_ms": latency_ms,
                "failure_count": failure_count,
                "failure_threshold": self._policy.failure_threshold,
                "state_before_probe": prior_state,
                "error": str(exc),
            },
        )
        if self._telemetry is not None and getattr(self._telemetry, "enabled", False):
            await self._telemetry.record_upstream_ping(
                server_id=self._server_id,
                result="failure",
                latency_ms=latency_ms,
                state_before_probe=prior_state,
            )
            await self._emit_breaker_state(self._state)

        if not open_triggered:
            return

        reset_count = await self._clients.reset_cached_clients(reason="circuit_breaker_open")
        logger.warning(
            "Circuit breaker opened for server '%s' on '%s' after ping failures; cached session clients reset",
            self._server_id,
            self._mount_path,
            extra={
                "server_id": self._server_id,
                "session_id": "health-probe",
                "open_cooldown_seconds": self._policy.open_cooldown_seconds,
                "reset_sessions": reset_count,
            },
        )
        await self._emit_breaker_state("open")

    async def allow_proxy_request(self) -> tuple[bool, str | None]:
        """Return whether proxied traffic should be served right now.

        Returns:
            Tuple of (allowed, reason). Reason is None when allowed.
        """
        if not self._policy.enabled:
            return True, None

        async with self._lock:
            self._advance_state_for_time_locked(time.monotonic())
            if self._state == "closed":
                return True, None
            if self._state == "open":
                return False, "Upstream is unhealthy (circuit breaker open)."
            return False, "Upstream is recovering (circuit breaker half_open)."

    async def health_snapshot(self) -> dict[str, object]:
        """Build health metadata for ``/healthz``.

        Returns:
            Dict of health status fields.
        """
        if not self._policy.enabled:
            return {
                "server_id": self._server_id,
                "mount_path": self._mount_path,
                "upstream_url": self._upstream_url,
                "status": "ok",
                "detail": "upstream_ping_disabled",
            }

        async with self._lock:
            self._advance_state_for_time_locked(time.monotonic())
            state = self._state
            status = "ok" if state == "closed" else "degraded"
            payload: dict[str, object] = {
                "server_id": self._server_id,
                "mount_path": self._mount_path,
                "upstream_url": self._upstream_url,
                "status": status,
                "breaker": {
                    "state": state,
                    "consecutive_failures": self._consecutive_failures,
                    "failure_threshold": self._policy.failure_threshold,
                    "open_cooldown_seconds": self._policy.open_cooldown_seconds,
                    "half_open_probe_allowance": self._policy.half_open_probe_allowance,
                    "half_open_probe_count": self._half_open_probe_count,
                    "half_open_success_count": self._half_open_success_count,
                },
                "ping": {
                    "interval_seconds": self._policy.interval_seconds,
                    "timeout_seconds": self._policy.timeout_seconds,
                    "last_latency_ms": self._last_ping_latency_ms,
                    "last_error": self._last_ping_error,
                    "last_success_at": self._last_ping_success_at_epoch,
                    "last_failure_at": self._last_ping_failure_at_epoch,
                },
            }
            if state != "closed":
                payload["detail"] = "upstream_unhealthy"
            return payload
