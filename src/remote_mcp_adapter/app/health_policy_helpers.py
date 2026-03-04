"""Shared health and persistence-policy helper utilities for HTTP registration."""

from __future__ import annotations

from fastapi import FastAPI

from .runtime import apply_runtime_failure_policy


async def apply_runtime_failure_policy_if_persistent_backend(
    *,
    resolved_config,
    persistence_policy,
    runtime_ref,
    session_store,
    app: FastAPI,
    component: str,
    error: str,
    build_memory_persistence_runtime,
) -> bool:
    """Apply runtime failure policy when backend is not memory, returning whether policy was applied.

    Args:
        resolved_config: Full adapter configuration.
        persistence_policy: Policy controller tracking failure state.
        runtime_ref: Mutable dict holding the ``current`` runtime.
        session_store: Session store whose backends may be swapped.
        app: FastAPI application instance.
        component: Name of the failing component.
        error: Error description string.
        build_memory_persistence_runtime: Factory for in-memory fallback runtime.

    Returns:
        True if the policy was applied (backend was not memory), False otherwise.
    """
    if resolved_config.state_persistence.type == "memory":
        return False
    await apply_runtime_failure_policy(
        policy_controller=persistence_policy,
        runtime_ref=runtime_ref,
        session_store=session_store,
        app=app,
        component=component,
        error=error,
        build_memory_persistence_runtime=build_memory_persistence_runtime,
    )
    return True


def build_healthz_payload(
    *,
    app: FastAPI,
    resolved_config,
    checks: list[dict[str, object]],
    persistence: dict[str, object],
    persistence_policy,
) -> tuple[dict[str, object], bool]:
    """Build health response payload and return whether any degraded condition exists.

    Args:
        app: FastAPI application instance providing runtime state.
        resolved_config: Full adapter configuration.
        checks: List of upstream health check result dicts.
        persistence: Mutable persistence status dict (enriched in-place).
        persistence_policy: Policy controller for policy snapshot.

    Returns:
        Tuple of ``(payload_dict, has_error)`` where *has_error* is True
        when any upstream, persistence, or wiring check indicates degradation.
    """
    persistence["configured_type"] = resolved_config.state_persistence.type
    persistence["effective_type"] = persistence.get("effective_type") or persistence.get("type")
    persistence["fallback_active"] = persistence["configured_type"] != persistence["effective_type"]
    policy_snapshot = persistence_policy.snapshot()
    persistence["policy"] = policy_snapshot
    has_upstream_error = any(item.get("status") != "ok" for item in checks)
    has_persistence_error = persistence.get("status") != "ok" or policy_snapshot.get("status") != "ok"
    startup_readiness = getattr(app.state, "startup_readiness", None)
    adapter_wiring = getattr(app.state, "adapter_wiring", None)
    startup_reconciliation = getattr(app.state, "startup_reconciliation", None)
    wiring_not_ready = isinstance(adapter_wiring, dict) and (adapter_wiring.get("ready") is False)
    has_error = has_upstream_error or wiring_not_ready or has_persistence_error
    payload: dict[str, object] = {
        "status": "ok" if not has_error else "degraded",
        "package": "remote_mcp_adapter",
        "servers": checks,
        "persistence": persistence,
    }
    if isinstance(startup_readiness, dict):
        payload["startup"] = startup_readiness
    if isinstance(startup_reconciliation, dict):
        payload["startup_reconciliation"] = startup_reconciliation
    if isinstance(adapter_wiring, dict):
        payload["adapter_wiring"] = adapter_wiring
    if has_upstream_error:
        payload["degraded_reason"] = "upstream_unhealthy"
    elif wiring_not_ready:
        payload["degraded_reason"] = "adapter_wiring_incomplete"
    elif has_persistence_error:
        payload["degraded_reason"] = policy_snapshot.get("degraded_reason") or "persistence_unhealthy"
    return payload, has_error
