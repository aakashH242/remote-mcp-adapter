# Public API

**What you'll learn here:** the small set of Python modules that most users and integrators should treat as the adapter's public code surface.

---

## What belongs here

These modules are the ones most likely to matter if you are:

- running the adapter from Python
- loading and validating config programmatically
- trying to understand the top-level contract without digging through storage and proxy internals

This is not a formal long-term compatibility promise, but it is the clearest public-facing surface in the current codebase.

---

## `remote_mcp_adapter.server`

This is the main Python entry point for building the FastAPI application.

::: remote_mcp_adapter.server

---

## `remote_mcp_adapter.config.load`

This module loads YAML config, expands environment placeholders, and returns a validated `AdapterConfig`.

::: remote_mcp_adapter.config.load

---

## `remote_mcp_adapter.config.schemas.root`

This is the top-level config schema. If you want to understand what the fully validated config object looks like in Python, start here.

::: remote_mcp_adapter.config.schemas.root

---

## Next steps

- **Previous topic:** [API Reference](index.md) - how this section is split and how to read it.
- **Next:** [Internal API](internal.md) - implementation-oriented modules for contributors.
