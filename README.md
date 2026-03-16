# Remote MCP Adapter

> **Remote MCP servers are great until a tool needs a real file.**
> Put an MCP server in Docker, Kubernetes, or on another machine and local file paths stop making sense. Remote MCP Adapter sits in the middle, handles uploads and generated files properly, and adds the session and tool-surface safeguards you usually want once this is running for real.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg?style=flat-square)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg?style=flat-square)](LICENSE)
[![Release](https://img.shields.io/github/v/release/aakashH242/remote-mcp-adapter?display_name=tag&style=flat-square&color=orange)](https://github.com/aakashH242/remote-mcp-adapter/releases)
[![MCP Badge](https://lobehub.com/badge/mcp/aakashh242-remote-mcp-adapter)](https://lobehub.com/mcp/aakashH242-remote-mcp-adapter)
[![Featured in awesome-mcp-devtools](https://img.shields.io/badge/awesome--mcp--devtools-featured-fc60a8?style=flat-square&logo=awesomelists)](https://github.com/punkpeye/awesome-mcp-devtools)

[![Lint](https://img.shields.io/github/actions/workflow/status/aakashH242/remote-mcp-adapter/lint.yml?branch=main&label=lint&style=flat-square)](https://github.com/aakashH242/remote-mcp-adapter/actions/workflows/lint.yml)
[![Tests](https://img.shields.io/github/actions/workflow/status/aakashH242/remote-mcp-adapter/tests.yml?branch=main&label=tests&style=flat-square)](https://github.com/aakashH242/remote-mcp-adapter/actions/workflows/tests.yml)
[![Docker CI](https://img.shields.io/github/actions/workflow/status/aakashH242/remote-mcp-adapter/docker.yml?branch=main&label=docker&style=flat-square)](https://github.com/aakashH242/remote-mcp-adapter/actions/workflows/docker.yml)
[![Helm CI](https://img.shields.io/github/actions/workflow/status/aakashH242/remote-mcp-adapter/helm.yml?branch=main&label=helm&style=flat-square)](https://github.com/aakashH242/remote-mcp-adapter/actions/workflows/helm.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/aakashH242/remote-mcp-adapter/main/.github/badges/coverage-badge.json&style=flat-square)](https://github.com/aakashH242/remote-mcp-adapter/actions/workflows/coverage-badge.yml)
[![Docs](https://img.shields.io/badge/docs-online-blue?style=flat-square)](https://aakashh242.github.io/remote-mcp-adapter/)

---

## The problem

Most MCP servers were written assuming the client and server share a filesystem. Move a server into Docker, Kubernetes, or a remote machine and two things break immediately:

1. **File inputs break.** A tool receives a local path from the client, but the server cannot read it.
2. **File outputs break.** A tool writes a screenshot or PDF on the server, but the client cannot get it back.

Remote MCP Adapter sits between your client and your upstream MCP servers. It fixes those file-path breaks without turning the rest of the stack upside down.

---

## Use this when

- your MCP client and upstream server run on different machines or in different containers
- your tools need **uploads, screenshots, PDFs, downloads**, or other filesystem-backed behavior
- you want one gateway in front of multiple upstream MCP servers

## Skip this when

- the client and upstream already safely share a filesystem
- your upstream tools do not touch files at all

---

## Proof in one prompt

Once the demo stack is up, point your agent at the adapter and try this:

```
Go to https://www.csm-testcenter.org/ and upload the readme of our repo there.
Take a screenshot for evidence and report back once done.
Also give me the download URL for the screenshot.
```

This exercises the whole failure path in one go: upload from the client, use that file on the server, generate a screenshot there, and get the result back. Without an adapter, that chain usually breaks somewhere in the middle.

---

## Quick Start

### Docker Compose (recommended)

The repo ships a [`compose.yaml`](compose.yaml) that starts Playwright MCP on port 8931 and the adapter on port 8932.

```bash
git clone https://github.com/aakashH242/remote-mcp-adapter.git
cd remote-mcp-adapter
docker compose up --build
```

```bash
curl http://localhost:8932/healthz
```

### From source

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/aakashH242/remote-mcp-adapter.git
cd remote-mcp-adapter
uv sync
# config.local.yaml has upstream.url set to http://localhost:8931/mcp
uv run remote-mcp-adapter --config config.local.yaml
```

Start Playwright MCP in a second terminal (requires Node.js):

```bash
npx @playwright/mcp --headless --port 8931
```

---

### Connect your agent

**GitHub Copilot** — add to `mcp.json`:

```json
{
  "servers": {
    "playwright": {
      "url": "http://localhost:8932/mcp/playwright",
      "type": "http"
    }
  }
}
```

**OpenAI Codex** — add to `config.toml`:

```toml
[mcp_servers.playwright]
url = "http://localhost:8932/mcp/playwright"
```

**Antigravity** — add to `mcp_config.json`:

```json
{
  "mcpServers": {
    "playwright": {
      "serverUrl": "http://localhost:8932/mcp/playwright"
    }
  }
}
```

---

## Core concepts

| What it does | How |
|---|---|
| **Stage uploads** | Issues `upload://` handles; rewrites paths before the tool call |
| **Capture artifacts** | Intercepts file outputs; returns `artifact://` references the client can read back |
| **Proxy safely** | Session isolation, quotas, health checks, circuit breaker, TTL cleanup |
| **Multi-server relay** | Exposes multiple upstreams under one gateway (`/mcp/<server>`) |

## Production and security

These add operational depth once you move past the local demo. The adapter is not just a file bridge; it also gives you controls for safer routing, safer model-visible tool metadata, and safer storage.

| Area | What you get |
|---|---|
| **Security** | Bearer-token auth, signed one-time upload URLs, optional signed artifact downloads |
| **Security** | Session isolation for uploads/artifacts, auth-context binding for stateful requests |
| **Security** | Metadata sanitization for visible tool text; description preserve/truncate/strip policy |
| **Security** | Pinned tool definitions to detect mid-session drift, schema changes, and upstream rug pulls |
| **Reliability** | Retries, reconnect, circuit breaker around unstable upstreams |
| **Operations** | In-memory, SQLite, or Redis-backed state depending on deployment shape |
| **Operations** | Atomic writes, orphan cleanup, quota limits, TTL cleanup, safe storage boundaries |
| **Observability** | OpenTelemetry metrics with optional log export |
| **Agent ergonomics** | Code mode to collapse the tool surface into discover/execute flows |
| **Deployment** | Docker image and published Helm chart for Kubernetes |

---

## How it works

- **Sessions.** Every client connection is identified by `Mcp-Session-Id`. The adapter scopes uploads, artifacts, and quotas to that session automatically, and when adapter auth is enabled it binds stateful requests to the same adapter auth context that established the session.

- **Upload handles.** The agent calls `<server_id>_get_upload_url(...)`, POSTs the file, and gets an `upload://sessions/<sid>/<upload_id>` handle. The adapter resolves it to a real path before forwarding upstream. Set `core.public_base_url` in any deployment behind a reverse proxy, ingress, or load balancer so returned URLs point at your actual external address.

- **Artifact references.** When a configured artifact-producer tool writes a file, the adapter captures it and returns an `artifact://sessions/<sid>/<artifact_id>/<filename>` URI. The agent calls `resources/read` on that URI to get the bytes back.

---

## What's New

<details open>
<summary><strong>v0.3.0 (03-16-2026)</strong></summary>

- The adapter now takes a safer default stance for model-visible tool metadata:
  - `core.tool_metadata_sanitization.mode` now defaults to `sanitize`
  - `core.tool_definition_pinning.mode` now defaults to `warn`
- Tool-definition pinning and drift detection were added. The adapter can now pin the first visible tool catalog for a session, detect later tool-definition drift, and either warn, block, or invalidate the session depending on policy.
- Model-visible tool metadata sanitization was added for tool titles, descriptions, annotation titles, and schema text. In stricter mode, tools with dirty metadata can be blocked instead of forwarded.
- A new all-tools description policy was added under `tool_description_policy`, with `preserve`, `truncate`, and `strip` modes. This applies to both top-level tool descriptions and nested schema descriptions.
- Stateful session handling is stricter when adapter auth is enabled. Sessions are now bound to the auth context that established them, so a reused `Mcp-Session-Id` cannot be picked up under a different authenticated context.
- Security documentation was expanded with a dedicated docs section plus a repo-root `SECURITY.md` snapshot of implemented controls and current limits.
</details>

<details>
<summary><strong>v0.2.0 (03-10-2026)</strong></summary>

- Tools can now be hidden per server using tool names or regex. Set under `servers[].disabled_tools`.
- [Code mode](https://blog.cloudflare.com/code-mode/) can be enabled globally or per server.
- Upload consumer tool descriptions can be shortened with `core.shorten_descriptions` or per-server `shorten_descriptions`.
- Helm chart for deployment.
</details>

---

## Minimal configuration

```yaml
servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    upstream:
      url: "http://localhost:8931/mcp"

    adapters:
      - type: "upload_consumer"
        tools: ["browser_file_upload"]
        file_path_argument: "paths"

      - type: "artifact_producer"
        tools: ["browser_take_screenshot", "browser_pdf_save"]
        output_path_argument: "filename"
        output_locator:
          mode: "regex"
```

`servers[]` is the only required section. Everything else has safe defaults. The full [`config.yaml.template`](config.yaml.template) documents every field inline.

---

## Deployment Notes

When adapters are enabled, the adapter and the upstream servers must share a common directory — either via local filesystem or network storage.

You can deploy the adapter in a few different ways depending on your environment:

- **Docker Compose** for local end-to-end testing
- **Docker image** for simple container deployment
- **Helm chart** for Kubernetes deployments via the published Helm repository

Create your configuration using the [config reference](config.yaml.template), ensure your upstream servers are running, and make sure the adapter and upstreams share the same storage path configured by `storage.root`. If clients will use `<server_id>_get_upload_url(...)` or HTTP artifact download links through a hostname, proxy, ingress, or load balancer, set `core.public_base_url` to that external base URL. Otherwise the adapter may generate URLs that only make sense inside the container or pod network.

For Kubernetes, use the published Helm repository:

- `https://aakashh242.github.io/remote-mcp-adapter`

That is the repository URL Helm expects for `helm repo add`, because it is the GitHub Pages root where `index.yaml` is published. The source chart lives in [charts/remote-mcp-adapter](charts/remote-mcp-adapter) if you want to inspect or contribute to it, but that source folder is not the primary end-user install path.

Example Helm install flow:

```bash
helm repo add remote-mcp-adapter https://aakashh242.github.io/remote-mcp-adapter
helm repo update
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values.yaml
```

For Kubernetes deployment shapes, overlay patterns, and production-oriented examples, see:

- [Deployment docs](https://aakashH242.github.io/remote-mcp-adapter/deployment/)
- [Deploy with Helm](https://aakashH242.github.io/remote-mcp-adapter/deployment/helm/)

For a direct container deployment, pull and run the image:

```
docker pull ghcr.io/aakashH242/remote-mcp-adapter:latest

docker run -d -v ./shared:/<your-path> -v ./config.yaml:/etc/remote-mcp-adapter/config.yaml -p 8932:8932 ghcr.io/aakashH242/remote-mcp-adapter:latest
```

---

## Documentation

Full documentation lives in the [MkDocs site](https://aakashH242.github.io/remote-mcp-adapter/):

| Page                                                                                                | What it covers                                          |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------|
| [Getting Started](https://aakashH242.github.io/remote-mcp-adapter/getting-started/)                 | Run the adapter in under 5 minutes                      |
| [Core Concepts](https://aakashH242.github.io/remote-mcp-adapter/core-concepts/)                     | Sessions, `upload://` handles, `artifact://` references |
| [How It Works](https://aakashH242.github.io/remote-mcp-adapter/how-it-works/)                       | Tool buckets, request flow diagram                      |
| [Configuration](https://aakashH242.github.io/remote-mcp-adapter/configuration/)                     | Quick config guide with examples                        |
| [Config Reference](https://aakashH242.github.io/remote-mcp-adapter/configuration/config-reference/) | Every field and default                                 |
| [Deployment](https://aakashH242.github.io/remote-mcp-adapter/deployment/)                           | Choose Docker Compose or Helm paths                     |
| [Deploy with Helm](https://aakashH242.github.io/remote-mcp-adapter/deployment/helm/)               | Kubernetes shapes, overlays, and install flow           |
| [Security](https://aakashH242.github.io/remote-mcp-adapter/security/)                               | Auth, trust boundaries, drift defenses, and operational posture |
| [Telemetry](https://aakashH242.github.io/remote-mcp-adapter/telemetry/)                             | OpenTelemetry metrics catalog                           |
| [Health](https://aakashH242.github.io/remote-mcp-adapter/health/)                                   | `/healthz` endpoint semantics and example payloads      |
| [Troubleshooting](https://aakashH242.github.io/remote-mcp-adapter/troubleshooting/)                 | Common problems and fixes                               |

For maintainers, the repo-root [SECURITY.md](SECURITY.md) tracks the security controls and trust boundaries that are already implemented.

---

## License

[MIT](LICENSE)
