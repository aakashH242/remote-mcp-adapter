# Remote MCP Adapter

**What you'll learn here:** what the adapter is, what problem it solves, and whether you need it.

---

## What it is

Remote MCP Adapter sits between an MCP client and one or more upstream MCP servers. It makes remote MCP usage workable for file input/output workflows where client and server do not share a filesystem.

It is an application-layer proxy. It stages upload inputs, captures artifact outputs, and forwards passthrough tool calls.

## The root problem

When MCP servers run remotely (container/VM), local filesystem assumptions break:

1. **File inputs:** upstream cannot read client-local paths.
2. **File outputs:** upstream writes files the client cannot directly read.

The adapter closes those gaps with `upload://` and `artifact://` workflows. See [Core Concepts](core-concepts.md).

## Who needs this

You likely need it when:

- clients and upstream servers run on different machines
- you need session-scoped uploads/artifacts with cleanup and quotas
- you want auth, health, and circuit-breaker controls in front of upstream servers

If client and server already share a filesystem, you may not need it.

## What it does not do

It does not proxy websocket transport. It works at MCP tool-call level over Streamable HTTP.

## Quick Demo

<video controls preload="metadata" width="100%">
  <source src="assets/demo.mp4" type="video/mp4" />
  Your browser does not support the video tag.
</video>

---

## Next steps

- **Next:** [Getting Started](getting-started.md) - run it locally.
- **See also:** [Core Concepts](core-concepts.md) - sessions, `upload://`, and `artifact://`.
