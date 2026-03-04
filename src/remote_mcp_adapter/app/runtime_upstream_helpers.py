"""Upstream probe and startup-readiness helper utilities."""

from __future__ import annotations

import asyncio
import time

from ..proxy.factory import ProxyMount

_STARTUP_PROBE_MAX_DELAY_SECONDS = 5


async def probe_upstream(mount: ProxyMount) -> dict[str, str]:
    """Probe one upstream MCP server reachability.

    Args:
        mount: Proxy mount containing server config and client registry.

    Returns:
        Status dict with keys ``server_id``, ``mount_path``, ``upstream_url``,
        ``status`` (``"ok"`` or ``"error"``), and optionally ``detail``.
    """
    client = mount.clients.build_probe_client()
    try:
        async with client:
            await client.list_tools()
        return {
            "server_id": mount.server.id,
            "mount_path": mount.server.mount_path,
            "upstream_url": mount.server.upstream.url,
            "status": "ok",
        }
    except Exception as exc:
        return {
            "server_id": mount.server.id,
            "mount_path": mount.server.mount_path,
            "upstream_url": mount.server.upstream.url,
            "status": "error",
            "detail": str(exc),
        }


def all_upstreams_ready(checks: list[dict[str, str]]) -> bool:
    """Return True only when every probe check reports status='ok'.

    Args:
        checks: List of probe result dicts.

    Returns:
        True if all checks have ``status="ok"``.
    """
    return all(check.get("status") == "ok" for check in checks)


def not_ready_server_ids(checks: list[dict[str, str]]) -> list[str]:
    """Collect server IDs that are not yet reporting status='ok'.

    Args:
        checks: List of probe result dicts.

    Returns:
        List of server ID strings with non-``"ok"`` status.
    """
    return [check["server_id"] for check in checks if check.get("status") != "ok"]


async def probe_all_upstreams(proxy_map: dict[str, ProxyMount]) -> list[dict[str, str]]:
    """Concurrently probe all mounted upstreams and return their status dicts.

    Args:
        proxy_map: Mapping of server ID to ``ProxyMount`` instances.

    Returns:
        List of probe result dicts, one per mounted server.
    """
    return await asyncio.gather(*(probe_upstream(mount) for mount in proxy_map.values()))


async def wait_for_upstream_readiness(
    proxy_map: dict[str, ProxyMount],
    max_wait_seconds: int,
) -> tuple[list[dict[str, str]], float]:
    """Probe upstream readiness until all servers are reachable or timeout elapses.

    Uses exponential backoff (capped at 5 s) between probe attempts.

    Args:
        proxy_map: Mapping of server ID to ``ProxyMount`` instances.
        max_wait_seconds: Maximum wall-clock seconds to spend probing.

    Returns:
        Tuple of ``(final_checks, elapsed_seconds)``.
    """
    started = time.monotonic()
    deadline = started + max_wait_seconds
    attempt = 0
    checks = await probe_all_upstreams(proxy_map)
    while not all_upstreams_ready(checks):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        delay = min(remaining, min(2**attempt, _STARTUP_PROBE_MAX_DELAY_SECONDS))
        await asyncio.sleep(delay)
        attempt += 1
        checks = await probe_all_upstreams(proxy_map)
    return checks, (time.monotonic() - started)


def build_startup_readiness(
    max_wait_seconds: int,
    waited_seconds: float,
    checks: list[dict[str, str]],
) -> dict[str, object]:
    """Build startup-readiness snapshot exposed through app state and health endpoint.

    Args:
        max_wait_seconds: Budget allotted for startup probing.
        waited_seconds: Actual elapsed time during probing.
        checks: Final probe result dicts.

    Returns:
        Dict summarizing readiness, wait budget, and any not-ready servers.
    """
    not_ready_servers = not_ready_server_ids(checks)
    return {
        "ready_within_wait_budget": not not_ready_servers,
        "max_start_wait_seconds": max_wait_seconds,
        "waited_seconds": round(waited_seconds, 3),
        "not_ready_servers": not_ready_servers,
    }
