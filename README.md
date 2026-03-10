# Remote MCP Adapter

> An MCP gateway that makes remote servers feel local — it manages file uploads to tools and captures generated files back to the client.

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg?style=flat-square)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg?style=flat-square)](LICENSE)
[![Release](https://img.shields.io/github/v/release/aakashH242/remote-mcp-adapter?display_name=tag&style=flat-square&color=orange)](https://github.com/aakashH242/remote-mcp-adapter/releases)
[![MCP Badge](https://lobehub.com/badge/mcp/aakashh242-remote-mcp-adapter)](https://lobehub.com/mcp/aakashh242-remote-mcp-adapter)

[![Lint](https://img.shields.io/github/actions/workflow/status/aakashH242/remote-mcp-adapter/lint.yml?branch=main&label=lint&style=flat-square)](https://github.com/aakashH242/remote-mcp-adapter/actions/workflows/lint.yml)
[![Tests](https://img.shields.io/github/actions/workflow/status/aakashH242/remote-mcp-adapter/tests.yml?branch=main&label=tests&style=flat-square)](https://github.com/aakashH242/remote-mcp-adapter/actions/workflows/tests.yml)
[![Docker CI](https://img.shields.io/github/actions/workflow/status/aakashH242/remote-mcp-adapter/docker.yml?branch=main&label=docker&style=flat-square)](https://github.com/aakashH242/remote-mcp-adapter/actions/workflows/docker.yml)
[![Helm CI](https://img.shields.io/github/actions/workflow/status/aakashH242/remote-mcp-adapter/helm.yml?branch=main&label=helm&style=flat-square)](https://github.com/aakashH242/remote-mcp-adapter/actions/workflows/helm.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/aakashH242/remote-mcp-adapter/main/.github/badges/coverage-badge.json&style=flat-square)](https://github.com/aakashH242/remote-mcp-adapter/actions/workflows/coverage-badge.yml)
[![Docs](https://img.shields.io/badge/docs-online-blue?style=flat-square)](https://aakashh242.github.io/remote-mcp-adapter/)



---

The [Model Context Protocol](https://modelcontextprotocol.io/) supports remote servers over Streamable HTTP. But most MCP servers were built assuming the client and server share a filesystem. When you move a server to a container or a remote machine, two things break: tools that read local files can't reach files on the client's machine, and tools that write files (screenshots, PDFs) save them on the server where the client can't retrieve them.

This adapter sits between your client and your upstream MCP servers. It stages uploaded files so tools can read them, captures tool output files as artifacts the client can read back, and forwards everything else unchanged.

---

## Key Features

- 🌐 **Multiserver relay** - Expose multiple upstream MCP servers under one gateway (`/mcp/<server>`).
- 🖥️ **Code mode** - Collapse any server's tool surface into a single discover/execute interface for coding agents.
- ⬆️ **File uploads** - Stage client files and pass them to tools via `upload://...` handles.
- 📬 **File outputs** - Capture screenshots, PDFs, and more, returning them as `artifact://...` MCP resources with optional download links.
- ⏳ **Sessions** - Provide per-session isolation, TTL cleanup, and optional “revival” on reconnect.
- 💾 **State backends** - Use in-memory (dev), SQLite (single node), or Redis (multi-node).
- 💓 **Upstream health** - Perform active checks and implement a circuit breaker to prevent cascading failures.
- 🔁 **Resilience** - Retry and reconnect when upstream sessions drop.
- 🔒 **Auth** - Use bearer tokens and signed, one-time upload URLs.
- 📊 **Observability** - Collect OpenTelemetry metrics with optional log export.
- 🛡️ **Safe storage** - Ensure atomic writes, orphan cleanup, and enforce quota limits.

---

## What's New

<details>
<summary>**v0.2.0 (03-10-2026)**</summary>
- Tools can now be hidden per server using either tool names or regex. Set under `servers[].disabled_tools`.
- [Code mode](https://blog.cloudflare.com/code-mode/) can be enabled globally or for each server.
- Upload consumer tool descriptions can be shortened. Set `core.shorten_descriptions` to set globally or set at each `server[].shorten_descriptions`.
- Helm chart for deployment.
</details>

---

## Core concepts

Three ideas cover most of what the adapter does.

- **Sessions.** Every client connection is identified by `Mcp-Session-Id`. The adapter scopes uploads, artifacts, and quotas to that session. This header is managed by the MCP client library automatically.

- **Upload handles.** When a tool needs a file, the agent calls `<server_id>_get_upload_url(...)`, POSTs the file, and receives an `upload://sessions/<sid>/<upload_id>` handle. It passes that handle as the tool argument. The adapter resolves it to a real filesystem path before forwarding the call upstream. The adapter exposes a MCP resource to serve as a guide for executing the staged upload flow. In local dev, the helper can usually infer a working upload URL. In any real deployment behind Docker networking, a reverse proxy, ingress, or a load balancer, set `core.public_base_url` explicitly so the returned upload URL points at the address your client can actually reach.

- **Artifact references.** When a tool configured as an artifact producer creates a file, the adapter captures it and returns an `artifact://sessions/<sid>/<artifact_id>/<filename>` URI in the tool result. The agent calls `resources/read` on that URI to get the file bytes back.

---

## Quick Start

### Docker Compose (recommended)

The repo includes a [`compose.yaml`](compose.yaml) that starts Playwright MCP on port 8931 and the adapter on port 8932.

```bash
git clone https://github.com/aakashH242/remote-mcp-adapter.git
cd remote-mcp-adapter
docker compose up --build
```

Verify the adapter is running:

```bash
curl http://localhost:8932/healthz
```

### From source

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/aakashH242/remote-mcp-adapter.git
cd remote-mcp-adapter
uv sync
uv run remote-mcp-adapter --config config.yaml
```
---

### Configure in IDE/Agent

**OpenAI Codex**

Add the adapter in `config.toml`.

```toml
[mcp_servers.playwright]
url = "http://localhost:8932/mcp/playwright"
```

**GitHub Copilot**

Add the adapter in `mcp.json`.

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

**Antigravity**

Add the adapter in `mcp_config.json`.

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

**Use this prompt -**

```
Go to https://www.csm-testcenter.org/ and upload the readme of our repo there. Take screenshot for evidence and report back once done. Also give me the download URL for the screenshot.
```

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

⚠️ When adapters are enabled, it is important that the adapter and the upstream servers share a common directory - 
either via local filesystem or network storage. 

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

- [Deployment docs](https://aakashh242.github.io/remote-mcp-adapter/deployment/)
- [Deploy with Helm](https://aakashh242.github.io/remote-mcp-adapter/deployment/helm/)

For a direct container deployment, pull and run the image:


```
docker pull ghcr.io/aakashh242/remote-mcp-adapter:latest

docker run -d -v ./shared:/<your-path> -v ./config.yaml:/etc/remote-mcp-adapter/config.yaml -p 8932:8932 ghcr.io/aakashh242/remote-mcp-adapter:latest

```

---

## Documentation

Full documentation lives in the [MkDocs site](https://aakashh242.github.io/remote-mcp-adapter/):

| Page                                                                                                | What it covers                                          |
|-----------------------------------------------------------------------------------------------------|---------------------------------------------------------|
| [Getting Started](https://aakashh242.github.io/remote-mcp-adapter/getting-started/)                 | Run the adapter in under 5 minutes                      |
| [Core Concepts](https://aakashh242.github.io/remote-mcp-adapter/core-concepts/)                     | Sessions, `upload://` handles, `artifact://` references |
| [How It Works](https://aakashh242.github.io/remote-mcp-adapter/how-it-works/)                       | Tool buckets, request flow diagram                      |
| [Configuration](https://aakashh242.github.io/remote-mcp-adapter/configuration/)                     | Quick config guide with examples                        |
| [Config Reference](https://aakashh242.github.io/remote-mcp-adapter/configuration/config-reference/) | Every field and default                                 |
| [Deployment](https://aakashh242.github.io/remote-mcp-adapter/deployment/)                           | Choose Docker Compose or Helm paths                     |
| [Deploy with Helm](https://aakashh242.github.io/remote-mcp-adapter/deployment/helm/)               | Kubernetes shapes, overlays, and install flow           |
| [Security](https://aakashh242.github.io/remote-mcp-adapter/security/)                               | Auth setup and upload URL signing                       |
| [Telemetry](https://aakashh242.github.io/remote-mcp-adapter/telemetry/)                             | OpenTelemetry metrics catalog                           |
| [Health](https://aakashh242.github.io/remote-mcp-adapter/health/)                                   | `/healthz` endpoint semantics and example payloads      |
| [Troubleshooting](https://aakashh242.github.io/remote-mcp-adapter/troubleshooting/)                 | Common problems and fixes                               |

---

## License

[MIT](LICENSE)
