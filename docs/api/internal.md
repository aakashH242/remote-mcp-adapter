# Internal API

**What you'll learn here:** the modules that drive proxy wiring, state handling, and telemetry inside the adapter.

---

## What belongs here

These modules are useful when you are:

- contributing to the adapter
- debugging request flow or upstream client behavior
- tracing session state, artifact handling, or telemetry emission

They are important, but they are more implementation-oriented than the public API section.

---

## `remote_mcp_adapter.proxy.factory`

This module builds the per-server proxy map and manages session-pinned upstream clients.

::: remote_mcp_adapter.proxy.factory

---

## `remote_mcp_adapter.proxy.hooks`

This module wires upload-consumer overrides, artifact-producer overrides, local helper tools, and local resources into each proxy surface.

::: remote_mcp_adapter.proxy.hooks

---

## `remote_mcp_adapter.telemetry.manager`

This module owns the async telemetry queue and OpenTelemetry emission flow.

::: remote_mcp_adapter.telemetry.manager

---

## `remote_mcp_adapter.core.storage.store`

This module manages sessions, uploads, artifacts, cleanup, quota enforcement, and related state transitions.

::: remote_mcp_adapter.core.storage.store

---

## Next steps

- **Previous topic:** [Public API](public.md) - user-facing modules and entry points.
- **See also:** [Troubleshooting](../troubleshooting.md) - common failures and practical fixes.
