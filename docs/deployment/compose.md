# Deploy with Docker Compose

What the repository's Compose setup includes, what you need before you run it, and how to bring it up and verify it.

---

## Why use this path

Docker Compose is the easiest way to get a full end-to-end stack running quickly.

It is a good fit when you want:

- one machine
- one adapter
- one upstream MCP server
- shared local storage
- a setup that is easy to inspect and tear down

If you are evaluating the project or just want a working baseline before doing anything more serious, this is the path to use.

---

## What the repository Compose file does

The repository's [compose.yaml](https://github.com/aakashH242/remote-mcp-adapter/blob/main/compose.yaml) starts two services:

- `playwright` — the upstream Playwright MCP server
- `remote-mcp-adapter` — the adapter itself

It also wires in two important mounts:

- `./data` → shared storage for uploaded files and generated artifacts
- `./config.yaml` → `/etc/remote-mcp-adapter/config.yaml`

That means the Compose setup is not just starting containers. It is also establishing the shared filesystem relationship the adapter depends on.

One detail matters here: the repository Compose file currently builds the adapter image locally from the Dockerfile. That is fine for local testing and contributor workflows. If you want to use the published adapter image instead, replace the adapter service's `build: .` with `image: ghcr.io/aakashh242/remote-mcp-adapter:<tag>`.

---

## Prerequisites

Before you run this setup, make sure you have:

- Docker installed
- Docker Compose available
- a local checkout of this repository
- a usable `config.yaml`

The repository already includes a `config.yaml` suitable for the bundled Playwright example, so most people can start without writing one from scratch.

---

## Quick start

```bash
git clone https://github.com/aakashH242/remote-mcp-adapter.git
cd remote-mcp-adapter
docker compose up -d
```

If you want to see logs while the services start, drop the `-d`.

---

## What to expect after startup

Once the stack is up:

- the adapter should be reachable on `http://localhost:8932`
- the Playwright upstream should be reachable through the adapter at `http://localhost:8932/mcp/playwright`
- uploads and artifacts should go through the shared `./data` directory

If you keep everything on plain localhost, you can usually leave `core.public_base_url` unset. If you put this stack behind a tunnel, reverse proxy, or non-localhost hostname, set `core.public_base_url` so helper-generated upload URLs and HTTP download links point at the address your client really uses.

Check health with:

```bash
curl http://localhost:8932/healthz
```

If the adapter reports `degraded` for a short time right after startup, that usually means the upstream is still coming up. Wait a few seconds and check again.

---

## Useful day-to-day commands

Start the stack:

```bash
docker compose up -d
```

View logs:

```bash
docker compose logs -f
```

Restart the stack:

```bash
docker compose restart
```

Stop the stack:

```bash
docker compose down
```

---

## Common Compose mistakes

!!! warning "Changing `storage.root` without changing mounts"
    If your config points to a different storage path, the mounted shared directory needs to match it for both the adapter and the upstream.

!!! warning "Using the wrong upstream URL"
    In the Compose setup, the upstream is reachable as `http://playwright:8931/mcp`, not `http://localhost:8931/mcp` from inside the adapter container.

!!! warning "Leaving `public_base_url` unset once localhost stops being the client address"
    If the client reaches the adapter through a tunnel, reverse proxy, or custom hostname, helper-generated upload URLs can be wrong unless `core.public_base_url` is set explicitly.

!!! warning "Assuming Compose is the production path"
    Compose is excellent for local and small deployments, but once you need Kubernetes-native operations, the Helm path is usually a better fit.

---

## When to move on from Compose

Move on to Helm when:

- you want Kubernetes deployment
- you need ingress or load balancer management
- you want cluster-native persistence controls
- you need a cleaner production install/upgrade workflow

---

## Next steps

- **Previous topic:** [Deployment](../deployment.md) — overview of the available deployment paths.
- **Next:** [Deploy with Helm](helm.md) — Kubernetes install path using the published chart.
- **See also:** [Getting Started](../getting-started.md) — quick local run path.
- **See also:** [Configuration](../configuration.md) — tune the runtime once the stack is up.
