# Configuration

**What you'll learn here:** how to write a working `config.yaml` quickly, what the required fields are, and the most common configuration mistakes.

---

## The only required section

The only thing you must define is `servers[]`. Every other section (`core`, `storage`, `sessions`, `uploads`, `artifacts`, `state_persistence`, `telemetry`) has safe defaults and can be omitted entirely when you are just getting started.

Each server entry requires three fields: `id`, `mount_path`, and `upstream.url`. The `id` is a short slug used in logging and storage paths. It is also used in helper tool names when upload helpers are active (`<id>_get_upload_url`). The `mount_path` is the HTTP path where the adapter will accept MCP requests for this server.

---

## Minimal working example

This is the minimal configuration for a single Playwright MCP upstream. It uses all defaults for timeouts, storage, sessions, and auth.

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

With this config the adapter does the following:

- Listens on `http://0.0.0.0:8932` (the default port).
- Proxies all MCP requests to `http://localhost:8931/mcp`.
- Wraps `browser_file_upload` as an upload consumer: it resolves `upload://` handles in the `paths` argument before forwarding.
- Wraps `browser_take_screenshot` and `browser_pdf_save` as artifact producers: it captures their output files and returns `artifact://` URIs.
- Treats everything else as passthrough.

---

## Adding a second server

You can proxy multiple upstream servers under a single adapter process. Each server gets its own `mount_path`:

```yaml
servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    upstream:
      url: "http://playwright-host:8931/mcp"
    adapters:
      - type: "artifact_producer"
        tools: ["browser_take_screenshot"]
        output_path_argument: "filename"
        output_locator:
          mode: "regex"

  - id: "myserver"
    mount_path: "/mcp/myserver"
    upstream:
      url: "http://myserver-host:9000/mcp"
```

The second server has no `adapters` entries, so all its tools are passthrough.

---

## Storage and persistence

By default, the adapter stores files and metadata under `/data/shared`. In Docker, mount a volume at that path. When running from source you can override it:

```yaml
storage:
  root: "/tmp/mcp-adapter-data"
```

For multi-replica deployments, switch the state backend to Redis so session metadata is shared across processes:

```yaml
state_persistence:
  type: "redis"

  redis:
    host: "redis.internal"
    port: 6379
```

---

## Override precedence

The adapter inherits tool-call settings from three levels. More-specific settings always win:

```
adapters[].overrides  >  servers[].tool_defaults  >  core.defaults
```

For example, to give a single tool a longer timeout without affecting everything else:

```yaml
servers:
  - id: "playwright"
    adapters:
      - type: "artifact_producer"
        tools: ["browser_pdf_save"]
        output_path_argument: "filename"
        output_locator:
          mode: "regex"
        overrides:
          tool_call_timeout_seconds: 120
```

---

## Common mistakes

⚠️ "Wrong `mount_path`"
    The `mount_path` in your config must exactly match the URL your agent connects to. If your config says `/mcp/playwright` but your agent points at `/mcp/playwright-browser`, the adapter will return 404.

⚠️ "Tool name mismatch"
    Tool names in `adapters[].tools` must exactly match the names the upstream server reports in `list_tools`. A typo — `browser_screenshot` instead of `browser_take_screenshot` — means the adapter will never intercept that tool. The tool will still be callable as passthrough, but artifacts will not be captured.

⚠️ "Forgetting `adapters[]` entries"
    If you do not add a tool to any `adapters[]` entry, the adapter treats it as passthrough. For tools that write files (like `browser_take_screenshot`), this means the artifact will be written to the server's disk and the client will receive a raw filesystem path it cannot read. Add the tool to an `artifact_producer` entry to enable artifact capture.

---

## Production checklist

The adapter's defaults are designed to "just work" locally without any configuration. Several of them are explicitly unbounded because a universal limit would be wrong for every deployment. The table below lists the fields that default to `None` (no limit) or an insecure value, what actually breaks in production if you leave them unset, and a sensible starting point.

| Field | Default | Risk if omitted | Recommended starting value |
|---|---|---|---|
| `sessions.idle_ttl_seconds` | `None` | **Sessions never expire.** The cleanup job runs on schedule but has nothing to collect. Uploads and artifacts age out on their own TTLs, but session records and storage-quota tracking accumulate forever. Disk fills up slowly and silently. | `1800` (30 min) |
| `sessions.max_active` | `None` | Any caller can open sessions without limit. Without auth, this means anything that can reach the adapter can exhaust memory and disk. | `100` |
| `sessions.max_total_session_size` | `None` | A single session can grow until the volume runs out. One large upload or a runaway screenshot loop will starve other sessions. | `"500MB"` |
| `storage.max_size` | `None` | Total storage on the mounted volume is uncapped. The adapter will write until the OS or Docker returns a disk-full error, which will likely surface as an opaque 500. | `"10GB"` |
| `artifacts.max_per_session` | `None` | A misbehaving client can generate unlimited artifact files. Without a per-session cap the orphan sweeper still runs, but session-level quota enforcement never fires. | `50` |
| `core.auth.enabled` | `false` | Any client that can reach the adapter can call tools, upload files, and read artifacts. Enable auth in any environment that is not strictly localhost-only. | `true` + a strong `token` |

Copy the block below into your `config.yaml` and adjust the values for your deployment:

```yaml
core:
  auth:
    enabled: true
    token: "<your-secret-token>"     # keep out of version control

storage:
  max_size: "10GB"                   # hard cap on total volume usage

sessions:
  max_active: 100                    # max concurrent sessions
  idle_ttl_seconds: 1800             # idle session expires after 30 min
  max_total_session_size: "500MB"    # per-session storage cap

artifacts:
  max_per_session: 50                # per-session artifact count cap
```

---

## High-availability (multi-replica)

Running more than one adapter process introduces two requirements that do not exist in a single-node deployment: session state must be shared across replicas, and generated URLs must point to the load balancer rather than to the individual container. The table below covers every field that changes meaning or becomes required in an HA setup.

| Field | Default | What it does in HA | Recommendation                                                                      |
|---|---|---|-------------------------------------------------------------------------------------|
| `state_persistence.type` | `"disk"` | With the default disk backend, each replica maintains its own SQLite file. A client that reconnects to a different replica sees a fresh session with no uploads or artifacts. | Set to `"redis"`.                                                                   |
| `state_persistence.redis.host` | `None` | Required when `type` is `"redis"`. | Set to your Redis host.                                                             |
| `state_persistence.unavailable_policy` | `"fail_closed"` | Controls what happens when Redis is unreachable. `fail_closed` returns errors to callers. `exit` terminates the process so the orchestrator can restart it — cleaner for Kubernetes. `fallback_memory` is dangerous in HA: each replica builds its own in-memory state and session routing silently breaks. | Keep `"fail_closed"` or switch to `"exit"`. Never use `"fallback_memory"` in HA.    |
| `state_persistence.snapshot_interval_seconds` | `30` | Snapshot interval for `state_persistence.type: memory` (periodic memory snapshots). This does not control Redis write cadence. | Keep default unless you run `type: memory`.                                         |
| `storage.lock_mode` | `"auto"` | In `"auto"` mode the adapter picks `"redis"` when `state_persistence.type` is `"redis"`. This ensures only one replica writes a given file at a time. No action needed if you set `type: "redis"`. | Leave as `"auto"`.                                                                  |
| `sessions.allow_revival` | `true` | When `true`, a client that reconnects — to any replica — with the same `Mcp-Session-Id` recovers its previous session from Redis state. This is what makes HA transparent to clients. | Keep `true`.                                                                        |
| `core.public_base_url` | `None` | Signed upload URLs and artifact download links embed the adapter's address. Without this field, the URLs generated by one replica point at only that replica's `host:port`, which breaks behind a load balancer and generates non-routable addresses in Docker overlay networks. | Set to the external URL of your load balancer, e.g. `"https://mcp-adapter.domain"`. |
| `core.allow_artifacts_download` | `false` | When `true`, download links are embedded in artifact URIs returned to clients. Only useful if `public_base_url` is set — otherwise the links are malformed. | Set to `true` once `public_base_url` is configured.                                 |
| `core.upstream_metadata_cache_ttl_seconds` | `300` | Tool lists are cached per-replica. If an upstream is redeployed, different replicas may serve stale tool listings for up to this many seconds. | Lower to `60` in environments where upstreams are frequently redeployed.            |
| `uploads.require_sha256` | `false` | When `true`, each uploaded file must include a matching `sha256` multipart form field (one `sha256` value per `file` part, same order). The adapter verifies each digest before accepting the file. The `<server_id>_get_upload_url(...)` helper advertises this via `sha256_required=true`. | Set to `true` in production. |
| `uploads.ttl_seconds` | `120` | Retention window for staged upload records/files before cleanup. This is not signed URL expiry. | Increase if clients need more time between upload and tool call.                    |
| `core.auth.signed_upload_ttl_seconds` | `120` | Expiry window for signed upload URLs returned by `<server_id>_get_upload_url(...)`. | Increase (for example `300`) for slower networks or larger uploads.                 |

### Sticky sessions and ingress routing

With a Redis backend and `allow_revival: true`, each adapter replica is effectively stateless between requests — any replica can serve any client request because session metadata is in Redis. Sticky sessions at the ingress (LB → adapter) are therefore **not required for correctness**. The exception is long-lived SSE streams: if your MCP client holds a persistent SSE connection for server-push notifications, that socket is tied to the pod that opened it, so a subsequent request routed to a different pod cannot write back to it. In Streamable HTTP's standard request-response mode this does not apply, but if you use SSE-based streaming, add session affinity at the ingress to avoid dropped stream responses.

**Sticky sessions do not solve HA for the upstream tier.** Most stateful MCP servers (Playwright, shell-based servers) bind their session context — the browser instance, the working directory — to a specific process. The adapter sends a `Mcp-Session-Id` header on every upstream call, but if the upstream is itself behind a load balancer, requests for the same session may land on different upstream pods and the upstream session will break. Adding sticky sessions at the ingress only covers the client → adapter hop; it does nothing for the adapter → upstream hop.

The recommended patterns for upstream HA are:

- **Single upstream pod per server.** Run each upstream server as a single replica (e.g. one Playwright pod). The adapter tier scales horizontally; the upstream tier does not need to. This is the simplest and most common setup.
- **Header-based upstream affinity.** The adapter already forwards `Mcp-Session-Id` on every call to the upstream. If your upstream is behind a Kubernetes Service (ClusterIP), add an L7 proxy — Istio `VirtualService` with `consistentHash.httpHeaderName: mcp-session-id`, Envoy `hash_policy`, Traefik sticky sessions, or NGINX `upstream-hash-by` — in front of the upstream pods and configure it to use that header as the affinity key. What the adapter cannot do today is discover individual pod addresses itself or maintain its own pod-to-session routing table; that still has to live in the upstream-side proxy layer.
- **Stateless upstream.** If the upstream server itself has a shared-state backend (unlikely for browser-based servers, more plausible for API-wrapper servers), it can run HA natively with no routing constraints.

### Shared volume

The adapter stores uploaded files and artifact files on the filesystem (`storage.root`). In a multi-replica setup all replicas must share the same underlying volume. Use a networked filesystem (NFS, EFS, Azure Files) or a CSI driver — not separate per-replica ephemeral volumes.

Session *metadata* lives in Redis; file *content* lives on the shared volume. Both must be consistent for a session to be portable across replicas.

### HA Example Configuration

```yaml
core:
  public_base_url: "https://mcp-adapter.domain"
  allow_artifacts_download: true
  upstream_metadata_cache_ttl_seconds: 60

  auth:
    enabled: true
    token: "<your-secret-token>"

storage:
  root: "/data/shared"              # must be the same networked volume on all replicas
  max_size: "10GB"

sessions:
  max_active: 100
  idle_ttl_seconds: 1800
  max_total_session_size: "500MB"
  allow_revival: true               # enables session portability across replicas

artifacts:
  max_per_session: 50

uploads:
  require_sha256: true
  ttl_seconds: 300

state_persistence:
  type: "redis"
  snapshot_interval_seconds: 10
  unavailable_policy: "fail_closed" # or "exit" for k8s

  redis:
    host: "redis.internal"
    port: 6379
```

---

## Next steps

- **Next:** [Config Reference](config-reference.md) — every field, default, and constraint.
- **See also:** [Security](security.md) — enable bearer token auth.
- **See also:** [Telemetry](telemetry.md) — enable OpenTelemetry metrics.
