# Configuration

`servers[]` is the only required section. Everything else has safe defaults and can be left out entirely when you are starting fresh.

---

## The only required section

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

If you enable upload helper tools or HTTP artifact download links, treat `core.public_base_url` as required in any non-trivial deployment. On localhost the adapter can often guess a usable URL. Behind Docker networking, a reverse proxy, ingress, or a load balancer, guessing is fragile. Set the public base URL to the exact external address your clients use.

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

## Tool discovery and metadata shaping

Four optional config groups change how tool metadata is presented to agents.

The adapter now starts from a conservative default stance here:

- tool metadata sanitization defaults to `sanitize`
- tool-definition pinning defaults to `warn`

That means a fresh config already cleans suspicious visible tool text and already tells the client when an upstream tool catalog drifts mid-session. Stricter deployments can still move those controls to `block`.

- `core.code_mode_enabled` enables FastMCP Code Mode globally. You can override it per server with `servers[].code_mode_enabled`.
- In Code Mode, the adapter exposes server-prefixed synthetic tools such as `<server_id>_search`, `<server_id>_get_schema`, `<server_id>_tags`, `<server_id>_list_tools`, and `<server_id>_execute`.
- `core.shorten_descriptions` enables shorter upload-consumer tool descriptions globally. You can override it per server with `servers[].shorten_descriptions`.
- `core.short_description_max_tokens` and `servers[].short_description_max_tokens` control the token budget for that shortened first-sentence summary.
- `core.tool_description_policy` controls how much description prose from upstream tools is forwarded at all. You can override it per server with `servers[].tool_description_policy`.
- In `truncate` mode, the adapter keeps only the first configured characters for tool and schema descriptions.
- In `strip` mode, the adapter removes description text entirely while keeping the tool payload MCP-compatible.
- `core.tool_metadata_sanitization` can clean tool titles, descriptions, `annotations.title`, and schema text before that metadata reaches the client. You can override it per server with `servers[].tool_metadata_sanitization`.
- In `sanitize` mode, the adapter forwards the cleaned version and logs that it had to make a change.
- In `block` mode, the adapter hides tools whose visible metadata would have needed cleanup.
- `core.tool_definition_pinning` pins the client-visible tool catalog on first exposure for a given `Mcp-Session-Id`, then detects later description/schema drift. You can override it per server with `servers[].tool_definition_pinning`. In `block + error`, `block_error_session_action` decides whether the adapter leaves the old session alive or invalidates it immediately.
- In `warn` mode, which is the default, the adapter keeps the current catalog visible but marks that the session has seen drift.

Use these only when you need a smaller, cleaner tool surface for weaker models or coding agents. They do not change the underlying upload or artifact behavior.

---

## Security controls and guardrails

If you are reading `config.yaml` from an operator point of view, these are the main groups that change the adapter's security posture.

- `core.auth` controls route auth, signed upload URLs, and signed artifact download URLs.
- `core.tool_description_policy` and `servers[].tool_description_policy` let you preserve, truncate, or strip upstream description prose before it reaches the client.
- `core.tool_metadata_sanitization` and `servers[].tool_metadata_sanitization` clean or block suspicious model-visible tool text before it reaches the client.
- `core.tool_definition_pinning` and `servers[].tool_definition_pinning` protect one adapter session from mid-session tool-definition drift.
- `servers[].disabled_tools` removes tools from the exposed surface entirely for one server.
- `uploads.require_sha256`, `uploads.max_file_bytes`, `artifacts.max_per_session`, `sessions.max_total_session_size`, and `storage.max_size` turn local-dev defaults into explicit intake and storage limits.
- `state_persistence.unavailable_policy` decides whether the adapter fails closed or serves in a more permissive degraded mode when its state backend is unavailable.
- `core.allow_artifacts_download` and `core.public_base_url` decide whether HTTP artifact links exist at all and which public address they use.
- `core.code_mode_enabled` and `servers[].code_mode_enabled` can reduce the visible tool surface, but they should be treated as surface-shaping controls, not as a substitute for auth or tool-definition pinning.

By default, the adapter takes a middle path:

- clean suspicious visible tool text before forwarding it
- warn on tool-definition drift instead of silently accepting it

That gives new deployments a safer baseline without turning every legitimate upstream upgrade into an immediate hard failure.

If you want the "why" behind those knobs rather than just the field names, read the [Security](security/index.md) section alongside this page.

---

## Common mistakes

!!! warning "Wrong `mount_path`"
  The `mount_path` in your config must exactly match the URL your agent connects to. If your config says `/mcp/playwright` but your agent points at `/mcp/playwright-browser`, the adapter will return 404.

!!! warning "Tool name mismatch"
  Tool names in `adapters[].tools` must exactly match the names the upstream server reports in `list_tools`. A typo — `browser_screenshot` instead of `browser_take_screenshot` — means the adapter will never intercept that tool. The tool will still be callable as passthrough, but artifacts will not be captured.

!!! warning "Forgetting `adapters[]` entries"
  If you do not add a tool to any `adapters[]` entry, the adapter treats it as passthrough. For tools that write files (like `browser_take_screenshot`), this means the artifact will be written to the server's disk and the client will receive a raw filesystem path it cannot read. Add the tool to an `artifact_producer` entry to enable artifact capture.

!!! warning "Leaving `public_base_url` unset in real deployments"
  If clients use `<server_id>_get_upload_url(...)` or HTTP download links through a proxy, ingress, or load balancer, the adapter needs `core.public_base_url` to build the right external URL. Without it, the generated URL may point at an internal container or pod address that the client cannot reach.

---

## Deployment scenarios

If you want opinionated configuration profiles instead of field-by-field guidance, start with these scenario pages.

- [Passthrough-Only Gateway Scenario](configuration/passthrough-only-gateway.md) - pure multi-server relay for teams that mainly want auth, routing, health, and circuit-breaker behavior.
- [Local Dev Scenario](configuration/local-dev.md) - fastest path for local testing, debugging, and first-time setup.
- [Single-Node Durable Scenario](configuration/single-node-durable.md) - one durable adapter node with auth, disk persistence, and real limits.
- [Distributed Production Scenario](configuration/distributed-production.md) - multi-replica deployment with Redis-backed state and shared storage.
- [High-Security Scenario](configuration/high-security.md) - security-focused hardening for auth, uploads, artifact access, and tool exposure.
- [Restricted-Limits Scenario](configuration/restricted-limits.md) - tighter caps for sessions, uploads, artifacts, and total storage in shared environments.
- [High-Observability Scenario](configuration/high-observability.md) - telemetry- and operations-focused visibility for production debugging and monitoring.
- [Agent-Optimized Code Mode Scenario](configuration/agent-optimized-code-mode.md) - compact tool discovery for coding agents and smaller models.
- [Public Demo Downloads Scenario](configuration/public-demo-downloads.md) - browser-facing demo profile with signed, human-clickable upload and artifact links.
- [Private Demo Links Scenario](configuration/private-demo-links.md) - internal demo profile with reliable human-clickable links and protected access.
- [Config Reference](configuration/config-reference.md) - exhaustive field-by-field reference to use alongside the scenario guides.

Together, these pages let you move from the simplest relay shape to progressively more opinionated operating profiles.

The detailed config reference page is generated from `config.yaml.template`. If you change the template, regenerate the page instead of editing the reference markdown by hand.

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

- **Next:** [Detailed Reference](configuration/config-reference.md) — every field, default, and constraint.
- **See also:** [Security](security/index.md) — enable bearer token auth.
- **See also:** [Telemetry](telemetry.md) — enable OpenTelemetry metrics.
