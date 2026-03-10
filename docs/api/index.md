# API Reference

**What you'll learn here:** which Python modules are meant for normal integrators, which ones are implementation details, and where to start reading the code through generated API docs.

---

## How to use this section

This API reference is split into two parts on purpose.

- **Public API** is the small surface area that most readers should start with.
- **Internal API** covers the modules that make the adapter work, but are more likely to change as the implementation evolves.

If you are trying to embed the adapter, load config, or understand the top-level runtime entry points, stay in the public section first.

If you are contributing to the adapter itself, debugging proxy behavior, or tracing state and telemetry flows, the internal section is the right place.

---

## Public API

These modules are the best first stop for normal users and integrators:

- `remote_mcp_adapter.server` - app factory and top-level wiring
- `remote_mcp_adapter.config.load` - YAML loading and environment interpolation
- `remote_mcp_adapter.config.schemas.root` - the top-level validated config contract

Go to [Public API](public.md).

## Internal API

These modules are more implementation-oriented:

- `remote_mcp_adapter.proxy.factory` - per-server proxy construction and session-pinned upstream clients
- `remote_mcp_adapter.proxy.hooks` - adapter tool/resource override wiring
- `remote_mcp_adapter.telemetry.manager` - async telemetry collection and export
- `remote_mcp_adapter.core.storage.store` - session, upload, artifact, cleanup, and quota state handling

Go to [Internal API](internal.md).

---

## Reading order

If you are new to the codebase, this order is the least confusing:

1. [How It Works](../how-it-works.md)
2. [Troubleshooting](../troubleshooting.md)
3. [Public API](public.md)
4. [Internal API](internal.md)

---

## Next steps

- **Previous topic:** [Troubleshooting](../troubleshooting.md) - common failures and practical fixes.
- **Next:** [Public API](public.md) - generated reference for the main entry points.
