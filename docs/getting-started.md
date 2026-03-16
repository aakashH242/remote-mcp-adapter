# Getting Started

How to run the adapter locally using Docker Compose or directly from source, confirm it is working, and connect your agent.

For production deployment options, published artifacts, and Kubernetes installs, see [Deployment](deployment.md).

---

## Prerequisites

- Docker and Docker Compose (for the Compose path), **or** Python 3.12+ and [uv](https://docs.astral.sh/uv/) (for the from-source path).
- A copy of `config.yaml` pointing at your upstream MCP server. The repo ships a working example that uses [Playwright MCP](https://github.com/microsoft/playwright-mcp) as the upstream.

---

## Run the adapter locally

Choose the path that matches how you want to start:

=== "Docker Compose"

    The repository includes a [`compose.yaml`](https://github.com/aakashH242/remote-mcp-adapter/blob/main/compose.yaml) that starts two containers: the Playwright MCP server on port 8931 and the Remote MCP Adapter on port 8932. They communicate over a private bridge network and share a mounted volume at `./data` for uploaded and artifact files.

    ```bash
    git clone https://github.com/aakashH242/remote-mcp-adapter.git
    cd remote-mcp-adapter

    # The repo ships a config.yaml ready for the Compose setup.
    # Edit it if you want to point at a different upstream.
    docker compose up --build
    ```

    Once the containers are up, the adapter listens at `http://localhost:8932`. The Playwright server is exposed at `http://localhost:8932/mcp/playwright` (the `mount_path` defined in `config.yaml`).

    If you see `"status": "degraded"`, the adapter started but cannot reach the upstream yet. Check `docker compose logs playwright` — the Playwright container sometimes takes a few seconds to become ready. Wait a moment and re-check `/healthz`.

=== "Run from source"

    Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

    The repo ships two config files:

    - `config.yaml` — for Docker Compose; `upstream.url` points at `http://playwright:8931/mcp` (the Compose network hostname).
    - `config.local.yaml` — for running from source; `upstream.url` already points at `http://localhost:8931/mcp`.

    Use `config.local.yaml` to avoid the Compose hostname mismatch:

    ```bash
    git clone https://github.com/aakashH242/remote-mcp-adapter.git
    cd remote-mcp-adapter

    uv sync
    uv run remote-mcp-adapter --config config.local.yaml
    ```

    You need a running upstream MCP server for the adapter to connect to. If you want to use Playwright MCP, start it separately:

    ```bash
    # In a separate terminal — requires Node.js and npx
    npx @playwright/mcp --headless --port 8931
    ```

### Sanity check

Hit the health endpoint to confirm the adapter is running and connected:

```bash
curl http://localhost:8932/healthz
```

A healthy response looks like this (HTTP 200):

```json
{
  "status": "ok",
  "servers": [
    {
      "server_id": "playwright",
      "mount_path": "/mcp/playwright",
      "status": "ok"
    }
  ]
}
```

---

## Configuring your agent or IDE

Once the adapter is running, point your agent at the adapter's `mount_path` URL rather than directly at the upstream server. The adapter handles the session header (`Mcp-Session-Id`) automatically as part of the MCP protocol.

Choose the client you are wiring up:

=== "OpenAI Codex"

    In `config.toml`:

    ```toml
    [mcp_servers.playwright]
    url = "http://localhost:8932/mcp/playwright"
    ```

=== "GitHub Copilot"

    In `mcp.json` (workspace or user settings):

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

=== "Antigravity"

    In `mcp_config.json`:

    ```json
    {
      "mcpServers": {
        "playwright": {
          "serverUrl": "http://localhost:8932/mcp/playwright"
        }
      }
    }
    ```

### Sanity check for agents

After connecting your agent, ask it to list available tools. You should see tools like `browser_take_screenshot`, `browser_pdf_save`, and a helper tool named `playwright_get_upload_url` injected by the adapter. If the tool list is empty or the connection fails, check that:

- The URL in your agent config ends with the correct `mount_path` (e.g. `/mcp/playwright`, not `/mcp`).
- The adapter process is running and `/healthz` returns `200`.
- If auth is enabled, the correct token header is present.

---

## Next steps

- **Next:** [Core Concepts](core-concepts.md) — sessions, upload handles, and artifacts. Worth reading before you start calling tools in anger.
- **See also:** [Configuration](configuration.md) — add more servers or tune timeouts.
- **See also:** [Security](security/index.md) — enable bearer token auth.
