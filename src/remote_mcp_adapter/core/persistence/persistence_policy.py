"""Policy controller for persistence backend unavailability handling."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Literal

from fastmcp.exceptions import ToolError

from ...config import StatePersistenceUnavailablePolicy

if TYPE_CHECKING:
    from ...telemetry import TelemetryManager

logger = logging.getLogger(__name__)

PersistenceStartupAction = Literal["continue_fail_closed", "switch_to_fallback", "exit"]
PersistenceRuntimeAction = Literal["none", "activate_fail_closed", "switch_to_fallback", "exit"]


class PersistenceUnavailableError(ToolError):
    """Raised when persistence is unavailable under fail-closed policy."""


class PersistencePolicyController:
    """Tracks persistence policy state and computes policy actions."""

    def __init__(
        self,
        *,
        configured_backend: str,
        unavailable_policy: StatePersistenceUnavailablePolicy,
        telemetry: TelemetryManager | None = None,
    ) -> None:
        """Initialize the persistence policy controller.

        Args:
            configured_backend: Name of the originally configured backend type.
            unavailable_policy: Policy to apply when persistence is unavailable.
            telemetry: Optional telemetry recorder for policy transitions.
        """
        self._configured_backend = configured_backend
        self._policy = unavailable_policy
        self._telemetry = telemetry
        self._active_backend = configured_backend
        self._unavailable = False
        self._fallback_active = configured_backend == "memory"
        self._exit_requested = False
        self._degraded_reason: str | None = None
        self._last_error: str | None = None
        self._last_transition_at: float | None = None

    @property
    def active_backend(self) -> str:
        """Currently active persistence backend label."""
        return self._active_backend

    @property
    def unavailable_policy(self) -> StatePersistenceUnavailablePolicy:
        """Configured policy for backend unavailability."""
        return self._policy

    def should_reject_stateful_requests(self) -> bool:
        """Return True when fail-closed policy is active and backend is unavailable.

        Returns:
            True if stateful requests should be rejected.
        """
        return self._policy == "fail_closed" and self._unavailable

    def handle_startup_failure(self, *, phase: str, error: str) -> PersistenceStartupAction:
        """Record a startup failure and return the policy action to take.

        Args:
            phase: Startup lifecycle phase where the failure occurred.
            error: Error string describing the failure.

        Returns:
            One of ``'continue_fail_closed'``, ``'switch_to_fallback'``, or ``'exit'``.
        """
        self._last_error = error
        self._last_transition_at = time.time()
        if self._policy == "fallback_memory":
            self._fallback_active = True
            self._active_backend = "memory"
            self._unavailable = False
            self._degraded_reason = f"fallback_memory_activated_during_{phase}"
            logger.warning(
                "Persistence backend unavailable during startup; falling back to in-memory mode",
                extra={
                    "configured_backend": self._configured_backend,
                    "phase": phase,
                    "error": error,
                },
            )
            self._emit_transition(action="switch_to_fallback", source=f"startup:{phase}")
            return "switch_to_fallback"

        if self._policy == "fail_closed":
            self._unavailable = True
            self._degraded_reason = f"persistence_unavailable_during_{phase}"
            logger.error(
                "Persistence backend unavailable during startup; fail-closed policy enabled",
                extra={
                    "configured_backend": self._configured_backend,
                    "phase": phase,
                    "error": error,
                },
            )
            self._emit_transition(action="continue_fail_closed", source=f"startup:{phase}")
            return "continue_fail_closed"

        self._exit_requested = True
        self._unavailable = True
        self._degraded_reason = f"persistence_unavailable_exit_during_{phase}"
        logger.critical(
            "Persistence backend unavailable during startup; exiting per policy",
            extra={
                "configured_backend": self._configured_backend,
                "phase": phase,
                "error": error,
            },
        )
        self._emit_transition(action="exit", source=f"startup:{phase}")
        return "exit"

    def handle_runtime_failure(self, *, component: str, error: str) -> PersistenceRuntimeAction:
        """Record a runtime failure and return the policy action to take.

        Args:
            component: Name of the component reporting the failure.
            error: Error string describing the failure.

        Returns:
            One of ``'none'``, ``'activate_fail_closed'``, ``'switch_to_fallback'``, or ``'exit'``.
        """
        if self._fallback_active:
            self._last_error = error
            self._last_transition_at = time.time()
            self._emit_transition(action="none", source=f"runtime:{component}")
            return "none"

        self._last_error = error
        self._last_transition_at = time.time()
        if self._policy == "fallback_memory":
            self._fallback_active = True
            self._active_backend = "memory"
            self._unavailable = False
            self._degraded_reason = f"fallback_memory_activated_by_{component}"
            logger.warning(
                "Persistence runtime failure detected; switching to in-memory fallback",
                extra={
                    "configured_backend": self._configured_backend,
                    "component": component,
                    "error": error,
                },
            )
            self._emit_transition(action="switch_to_fallback", source=f"runtime:{component}")
            return "switch_to_fallback"

        if self._policy == "fail_closed":
            if not self._unavailable:
                logger.error(
                    "Persistence runtime failure detected; enabling fail-closed guard",
                    extra={
                        "configured_backend": self._configured_backend,
                        "component": component,
                        "error": error,
                    },
                )
            self._unavailable = True
            self._degraded_reason = f"persistence_unavailable_via_{component}"
            self._emit_transition(action="activate_fail_closed", source=f"runtime:{component}")
            return "activate_fail_closed"

        self._exit_requested = True
        self._unavailable = True
        self._degraded_reason = f"persistence_unavailable_exit_via_{component}"
        logger.critical(
            "Persistence runtime failure detected; exiting per policy",
            extra={
                "configured_backend": self._configured_backend,
                "component": component,
                "error": error,
            },
        )
        self._emit_transition(action="exit", source=f"runtime:{component}")
        return "exit"

    def handle_runtime_recovery(self, *, component: str) -> None:
        """Clear the fail-closed flag when a previously unavailable backend recovers.

        Args:
            component: Name of the component reporting recovery.
        """
        if self._policy != "fail_closed":
            return
        if not self._unavailable:
            return
        self._unavailable = False
        self._degraded_reason = None
        self._last_transition_at = time.time()
        logger.info(
            "Persistence backend recovered; fail-closed guard cleared",
            extra={
                "configured_backend": self._configured_backend,
                "component": component,
            },
        )
        self._emit_transition(action="recover", source=f"runtime:{component}")

    def _emit_transition(self, *, action: str, source: str) -> None:
        """Emit a policy transition event to telemetry when enabled.

        Args:
            action: Policy action label (e.g. ``switch_to_fallback``).
            source: Transition source context string.
        """
        if self._telemetry is None:
            return
        self._telemetry.record_persistence_policy_transition_nowait(
            action=action,
            source=source,
            policy=self._policy,
            configured_backend=self._configured_backend,
        )

    def snapshot(self) -> dict[str, object]:
        """Return a health-serializable dict of current policy state.

        Returns:
            Dict with configured/active backend, policy flags, and degradation info.
        """
        status = "ok"
        if self._unavailable or self._fallback_active or self._exit_requested:
            status = "degraded"
        return {
            "configured_backend": self._configured_backend,
            "active_backend": self._active_backend,
            "unavailable_policy": self._policy,
            "status": status,
            "unavailable": self._unavailable,
            "fallback_active": self._fallback_active,
            "exit_requested": self._exit_requested,
            "degraded_reason": self._degraded_reason,
            "last_error": self._last_error,
            "last_transition_at": self._last_transition_at,
        }
