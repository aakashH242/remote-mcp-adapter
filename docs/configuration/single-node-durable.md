# Single-Node Durable Scenario

One node, persistent state, real limits. More serious than local dev, without the Redis and multi-pod coordination of a distributed setup.

---

## What this scenario is for

This profile is a good fit when you want something more serious than local dev, but you are **not** running a horizontally scaled deployment yet.

Typical examples:

- one VM running the adapter and one or more upstream servers
- one Docker host with persistent mounted storage
- one Kubernetes pod backed by a persistent volume
- an internal service where durability matters more than elasticity

This profile is still simple compared to a distributed setup, but it starts making durability, cleanup, and resource control explicit.

---

## What this scenario assumes

A typical single-node durable deployment looks like this:

- one adapter instance
- local persistent disk or a mounted volume
- one shared storage root for the adapter and upstreams
- no need for Redis-backed multi-replica state
- restart survival matters
- security and limits should be tighter than local dev

If you want a deployment that can survive process restarts cleanly without taking on the complexity of Redis and multi-pod coordination, this is the right next step after local dev.

---

## Recommended knobs and values

These are the main settings to care about for a durable single-node deployment.

### Core

```yaml
core:
  host: "0.0.0.0"
  port: 8932
  log_level: "info"
  public_base_url: "https://mcp-adapter.example.com"
  allow_artifacts_download: true
  code_mode_enabled: false
```

`host: "0.0.0.0"` is still the practical default in containers and services. `public_base_url` should be set once clients access the adapter through a real hostname or reverse proxy. `allow_artifacts_download: true` is reasonable here because you are now operating a real service rather than a throwaway dev stack. `code_mode_enabled: false` remains the safest baseline unless you specifically want a reduced tool surface.

If your users call `<server_id>_get_upload_url(...)`, this is not just a nice extra. It is the setting that makes the returned upload URL point at the real public address instead of an internal container or host address.

### Auth

```yaml
core:
  auth:
    enabled: true
    header_name: "X-Mcp-Adapter-Auth-Token"
    token: "${MCP_ADAPTER_TOKEN}"
    signed_upload_ttl_seconds: 300
```

Once the adapter is reachable over a network, auth should stop being optional. Signed upload URLs should last long enough for normal user workflows, but not forever. Storing the token in an environment variable keeps secrets out of committed config.

### State persistence

```yaml
state_persistence:
  type: "disk"
  unavailable_policy: "fail_closed"

  disk:
    local_path: "/data/shared/state/adapter_state.sqlite3"
    wal:
      enabled: true
```

`disk` is the right default for a durable single-node deployment. SQLite is simple, local, and reliable enough for one adapter process. `fail_closed` is safer than pretending persistence is healthy when it is not. An explicit `local_path` makes the durability location obvious to operators.

### Storage

```yaml
storage:
  root: "/data/shared"
  lock_mode: "file"
  max_size: "10Gi"
  orphan_sweeper_enabled: true
  orphan_sweeper_grace_seconds: 300
```

`root` should point at durable mounted storage shared with upstreams. `file` locking is appropriate for one-node durable deployments. `max_size` puts a real ceiling on disk growth. Orphan cleanup should stay on unless you have a very specific reason to disable it.

### Sessions, uploads, and artifacts

```yaml
sessions:
  max_active: 100
  idle_ttl_seconds: 1800
  max_total_session_size: "500Mi"
  allow_revival: true
  tombstone_ttl_seconds: 86400

uploads:
  enabled: true
  max_file_bytes: "50Mi"
  ttl_seconds: 300
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 1800
  max_per_session: 50
  expose_as_resources: true
```

Unlike local dev, this profile should set real limits. `max_active`, `max_total_session_size`, and `max_per_session` protect the node from silent exhaustion. `require_sha256: true` is a sensible production default for upload integrity. `allow_revival: true` improves user experience during reconnects and restarts on a single node too.

### Telemetry

```yaml
telemetry:
  enabled: false
```

Telemetry is still optional here. Keep it off unless you already have an OTel sink or a clear operational reason to enable it. You can add it later without changing the basic durability story.

### Servers

A durable single-node deployment still uses the same `servers[]` model as local dev, but you should assume the upstream and adapter are now part of a real service boundary.

```yaml
servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    upstream:
      transport: "streamable_http"
      url: "http://playwright.internal:8931/mcp"

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

The server model does not fundamentally change. What changes is that the surrounding storage, auth, persistence, and limits are no longer casual. By this stage you should be intentional about which tools are upload consumers and artifact producers.

---

## Full example

```yaml
core:
  host: "0.0.0.0"
  port: 8932
  log_level: "info"
  public_base_url: "https://mcp-adapter.example.com"
  allow_artifacts_download: true
  code_mode_enabled: false

  auth:
    enabled: true
    header_name: "X-Mcp-Adapter-Auth-Token"
    token: "${MCP_ADAPTER_TOKEN}"
    signed_upload_ttl_seconds: 300

state_persistence:
  type: "disk"
  unavailable_policy: "fail_closed"

  disk:
    local_path: "/data/shared/state/adapter_state.sqlite3"
    wal:
      enabled: true

storage:
  root: "/data/shared"
  lock_mode: "file"
  max_size: "10Gi"
  orphan_sweeper_enabled: true
  orphan_sweeper_grace_seconds: 300

sessions:
  max_active: 100
  idle_ttl_seconds: 1800
  max_total_session_size: "500Mi"
  allow_revival: true
  tombstone_ttl_seconds: 86400

uploads:
  enabled: true
  max_file_bytes: "50Mi"
  ttl_seconds: 300
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 1800
  max_per_session: 50
  expose_as_resources: true

telemetry:
  enabled: false

servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    upstream:
      transport: "streamable_http"
      url: "http://playwright.internal:8931/mcp"

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

---

## Tradeoffs

This profile is stricter than local dev, but still intentionally avoids distributed-system complexity.

### What you gain

- restart-survivable metadata and sessions
- safer defaults for auth and resource usage
- real limits on storage and artifact growth
- a better baseline for internal production use
- easier operations than a Redis-backed deployment

### What you give up

- no horizontal scale
- the node remains a single point of failure
- local disk or mounted volume health matters a lot
- still less robust than a distributed production profile

---

## Signs you should move past this profile

Move to the next profile when any of the following becomes true:

- you need more than one adapter replica
- you want rolling upgrades without session fragility
- you need failure tolerance beyond one host
- you want shared state across multiple nodes

When that happens, the next likely profile is:

- distributed production / multi-replica deployment

---

## Common single-node durable mistakes

!!! warning "Using disk persistence without durable storage"
  If the container or pod filesystem is ephemeral, `state_persistence.type: "disk"` does not actually buy you much. Put the SQLite file on a real persistent volume.

!!! warning "Forgetting `public_base_url`"
  Once the adapter sits behind a real hostname or reverse proxy, generated links should use that external address rather than container-internal hostnames.

!!! warning "Leaving local-dev limits in place"
  Unbounded sessions, storage, and artifact counts feel fine in a sandbox. They are much riskier once real users and longer runtimes are involved.

!!! warning "Auth still disabled"
  This profile assumes the adapter is now a real service. If it is reachable over a network, leaving auth disabled is usually a mistake.

---

## Next steps

- **Back to:** [Configuration](../configuration.md) — overview and scenario index.
- **Previous scenario:** [Local Dev Scenario](local-dev.md) — simplest starting point.
- **See also:** [Security](../security/index.md) — auth and signed uploads.
- **See also:** [Config Reference](config-reference.md) — exact field behavior and defaults.
- **Next scenario:** [Distributed Production Scenario](distributed-production.md) — add shared state and storage for multi-replica deployments.
