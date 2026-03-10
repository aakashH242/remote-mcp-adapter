# Remote MCP Adapter

**What you'll learn here:** what the adapter is, why it exists, when you actually need it, and the easiest way to get started.

---

## What it is

Remote MCP Adapter is a gateway for MCP servers that need to work well in remote environments.

It sits between your MCP client and one or more upstream MCP servers and smooths over the biggest pain point in remote MCP setups: file handling.

When an MCP server runs in a container, VM, or Kubernetes cluster, it no longer shares your local filesystem. That sounds small, but in practice it breaks a lot of useful tools. A browser automation tool may not be able to read a file you want to upload. A screenshot tool may create an image on the server that your client cannot directly access.

This adapter fixes that without asking upstream servers to reinvent their own storage, upload, and artifact flows.

At a high level, it helps by:

- staging uploads before tool execution
- capturing generated files after tool execution
- exposing those files back to the client through MCP-friendly handles and resources
- forwarding everything else with minimal behavior change

The result is that remote MCP servers feel much closer to local ones, especially for file-heavy workflows.

## The root problem

Most MCP servers were originally written with one quiet assumption: the client and the server can both see the same files.

That assumption falls apart the moment the server moves into a container, VM, Kubernetes pod, or another machine.

Two failures usually appear first:

1. **File inputs break.** A tool receives a local path from the client, but the upstream server cannot read that path.
2. **File outputs break.** A tool writes a screenshot, PDF, or other artifact on the server, but the client cannot access it.

Remote MCP Adapter closes those gaps with `upload://` and `artifact://` workflows. If you want the deeper mental model, read [Core Concepts](core-concepts.md).

## Quick Demo

<video controls preload="metadata" width="100%">
  <source src="assets/demo.mp4" type="video/mp4" />
  Your browser does not support the video tag.
</video>

## When you should use it

You likely want this adapter when:

- your MCP client and upstream server run on different machines or in different containers
- your tools need file uploads, screenshots, PDFs, downloads, or other filesystem-backed behavior
- you want session-scoped storage, cleanup, quotas, and optional persistence
- you want health checks, upstream monitoring, retries, or circuit-breaker behavior in front of upstream servers
- you want one gateway in front of multiple upstream MCP servers

You may not need it when:

- the client and upstream already share the same filesystem safely
- your upstream tools do not read or write files at all
- you only need a thin pass-through proxy with no session or storage behavior

## What it can do

This is not just a tiny upload shim. The adapter already includes a fairly complete operational feature set:

- 🌐 **Multi-server relay** — expose multiple upstream MCP servers behind one gateway with clean per-server mount paths such as `/mcp/playwright`
- 🖥️ **Code mode** — collapse a server's visible tool surface into a discover/execute flow for coding agents when you want a smaller interface
- ⬆️ **File uploads** — issue upload URLs, stage files safely, and pass `upload://` handles into tools
- 📬 **Artifact capture** — collect screenshots, PDFs, downloads, and other outputs as `artifact://` references, with optional download links
- ⏳ **Session isolation** — keep uploads, artifacts, limits, and lifecycle behavior scoped to each MCP session
- 💾 **State backends** — run with in-memory state for simple setups or move to SQLite and Redis-backed flows for more durable deployments
- 💓 **Upstream health** — actively monitor upstream servers and avoid blindly routing traffic into unhealthy ones
- 🔁 **Resilience controls** — support retries, reconnect behavior, and circuit-breaker-style protection around unstable upstreams
- 🔒 **Security features** — use auth headers, signed upload URLs, and safer boundaries around remote file operations
- 📊 **Observability** — expose health signals and telemetry so operators can understand what the adapter is doing
- 🛡️ **Safe storage behavior** — enforce path validation, cleanup, quotas, orphan sweeping, and atomic write patterns
- 🚀 **Flexible deployment** — run locally with Docker Compose, directly from source, or on Kubernetes with Helm

That feature set is why this project is useful both as a developer convenience layer and as a real operational boundary in front of upstream MCP servers.

## What it does not do

It is not a generic transport bridge for every MCP mode.

In particular:

- it does **not** proxy websocket transport
- it is designed around MCP tool and resource flows over Streamable HTTP
- it does not replace your upstream MCP servers; it sits in front of them

## How it works in 30 seconds

At a high level, the request flow is simple:

1. The client connects to the adapter instead of directly to the upstream server.
2. When a tool needs a file, the adapter issues an upload URL.
3. The client uploads the file, and the adapter stores it under the configured shared storage root.
4. The adapter rewrites the tool input into a real filesystem path the upstream can read.
5. The upstream tool runs normally.
6. If the tool creates a file, the adapter captures it and returns an `artifact://` reference.
7. The client reads that artifact back through the adapter.

That is why the shared storage path matters so much: the adapter and the upstream process must agree on where staged inputs and generated outputs live.

## Pick a deployment path

Start with the option that matches your environment:

=== "Docker Compose"

    Best for first-time local evaluation, demos, and quick debugging.

    - fastest way to see the full flow end-to-end
    - good default if you want a working local stack with minimal setup

    If you are new to the project, this is usually the right place to begin. Go to [Getting Started](getting-started.md).

=== "Run from source"

    Best for contributors, local development, debugging, and custom experimentation.

    - easiest path for stepping through code
    - good when you want to modify config and implementation together

    If you plan to work on the codebase itself, this is usually the best path. Go to [Getting Started](getting-started.md).

=== "Helm / Kubernetes"

    Best for cluster deployment, shared storage setups, and controlled operations.

    - supports adapter config via ConfigMap
    - supports standalone and distributed deployment modes
    - supports persistent shared storage and custom volume mounts

    If you are deploying into Kubernetes, go to [Deployment](deployment.md).

---

## A few important operating rules

Before you deploy, keep these points in mind:

- The adapter and any upstream that reads staged uploads or writes artifacts must share a compatible storage path.
- The configured `storage.root` should match the mounted shared directory used by your deployment.
- In standalone mode, upstream sidecars run alongside the adapter in the same pod.
- In distributed mode, upstream services run separately and you typically need shared network storage.
- If auth is enabled, clients must send the expected header or use signed upload URLs where applicable.

---

## Where to go next

Choose the path that matches what you need next:

=== "For users"

    - **Start here:** [Getting Started](getting-started.md)
    - **Then read:** [Core Concepts](core-concepts.md)
    - **Then configure:** [Configuration](configuration.md)

=== "For operators"

    - [Configuration](configuration.md)
    - [Config Reference](configuration/config-reference.md)
    - [Security](security.md)
    - [Health](health.md)
    - [Troubleshooting](troubleshooting.md)

=== "For developers"

    - [How It Works](how-it-works.md) for request flow and architecture
    - [Troubleshooting](troubleshooting.md) for real failure modes before diving into code
    - [API Reference](api/index.md) for the public and internal Python surface
    - [Telemetry](telemetry.md) for metrics and observability behavior
