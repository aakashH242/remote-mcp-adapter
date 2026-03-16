# Distributed Production Scenario

Multi-replica deployment with Redis-backed shared state, networked storage, and the operational discipline to match.

---

## What this scenario is for

This profile is for real distributed deployments where one adapter instance is no longer enough.

Typical examples:

- multiple adapter replicas behind a load balancer or ingress
- Kubernetes deployments with more than one pod
- environments where rolling upgrades should not destroy active sessions
- shared infrastructure where adapter failover matters
- production systems where state and file storage must remain consistent across nodes

This is the first scenario that should be treated as genuinely distributed rather than just durable.

---

## What this scenario assumes

A typical distributed production setup looks like this:

- two or more adapter replicas
- one external address in front of them
- Redis for shared session metadata
- shared network storage for uploads and artifacts
- upstream servers either run as singletons or use their own affinity strategy
- auth is enabled
- resource limits are real and intentional

If any replica can receive a request for any session, then both metadata and file content must be shared correctly. That is the defining constraint of this profile.

---

## Recommended knobs and values

These are the main settings to care about once the adapter is distributed across multiple nodes.

### Core

```yaml
core:
  host: "0.0.0.0"
  port: 8932
  log_level: "info"
  public_base_url: "https://mcp-adapter.example.com"
  allow_artifacts_download: true
  upstream_metadata_cache_ttl_seconds: 60
  code_mode_enabled: false
```

`public_base_url` is no longer optional in practice once URLs need to resolve through a load balancer or ingress. `allow_artifacts_download: true` is now much more useful because externally routable URLs can be generated correctly. `upstream_metadata_cache_ttl_seconds: 60` is a safer production value when upstreams may be redeployed and replicas should converge faster on updated tool metadata. `code_mode_enabled: false` remains a baseline choice unless you specifically want that smaller interface.

If this deployment exposes `<server_id>_get_upload_url(...)`, assume `public_base_url` is mandatory. Without it, one replica may hand back a URL that only makes sense inside the cluster or container network.

### Auth

```yaml
core:
  auth:
    enabled: true
    header_name: "X-Mcp-Adapter-Auth-Token"
    token: "${MCP_ADAPTER_TOKEN}"
    signing_secret: "${MCP_ADAPTER_SIGNING_SECRET}"
    signed_upload_ttl_seconds: 300
```

Distributed production should assume a real security boundary. Use a dedicated `signing_secret` rather than coupling everything to the main auth token. Signed upload URLs should be long enough for normal user flows, but short enough to remain low-risk.

### State persistence

```yaml
state_persistence:
  type: "redis"
  unavailable_policy: "fail_closed"

  redis:
    host: "redis.internal"
    port: 6379
    db: 0
    key_base: "remote-mcp-adapter"
```

`redis` is the correct state backend when sessions must survive across replicas — each replica needs access to the same session metadata store. `fail_closed` is the safest default when shared state is unavailable. A `key_base` helps isolate adapter data from other workloads sharing the same Redis instance.

If your platform prefers crash-and-restart behavior instead of serving failures, `unavailable_policy: "exit"` can also be a valid production choice.

### Storage

```yaml
storage:
  root: "/data/shared"
  lock_mode: "redis"
  max_size: "50Gi"
  orphan_sweeper_enabled: true
  orphan_sweeper_grace_seconds: 300
```

`root` must point at genuinely shared storage across all replicas. `lock_mode: "redis"` makes distributed file coordination explicit. Storage limits should reflect real production capacity, not just local experimentation. Cleanup should remain enabled because stale uploads and artifacts become more expensive in shared environments.

If you prefer the adapter to infer this from `state_persistence.type: "redis"`, `lock_mode: "auto"` is also reasonable. The point is that distributed locking behavior must exist.

### Sessions, uploads, and artifacts

```yaml
sessions:
  max_active: 500
  idle_ttl_seconds: 1800
  max_total_session_size: "1Gi"
  allow_revival: true
  tombstone_ttl_seconds: 86400

uploads:
  enabled: true
  max_file_bytes: "100Mi"
  ttl_seconds: 300
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 1800
  max_per_session: 100
  expose_as_resources: true
```

Distributed production should have explicit quotas. `allow_revival: true` is part of what makes multi-replica behavior feel transparent to clients — a reconnecting client recovers its session regardless of which replica it hits. `require_sha256: true` is a sensible integrity default once uploads cross real networks and infrastructure layers. The exact numbers should be adjusted for your workload, but the important thing is that they exist.

### Telemetry

```yaml
telemetry:
  enabled: true
  transport: "http"
  endpoint: "https://otel-collector.internal/v1/metrics"
```

Once the deployment is distributed, operators need visibility. Telemetry stops being "nice to have" and starts becoming part of incident response and capacity planning. This page keeps the example minimal, but real deployments often add resource attributes, headers, and queue tuning too.

### Servers

The server definitions remain familiar, but upstream routing discipline matters more now.

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

The adapter tier can be distributed, but the upstream tier must still preserve session correctness. For stateful upstreams, a single upstream replica per server is often the simplest reliable choice. If upstreams are also load-balanced, they need their own session-affinity strategy.

---

## Full example

```yaml
core:
  host: "0.0.0.0"
  port: 8932
  log_level: "info"
  public_base_url: "https://mcp-adapter.example.com"
  allow_artifacts_download: true
  upstream_metadata_cache_ttl_seconds: 60
  code_mode_enabled: false

  auth:
    enabled: true
    header_name: "X-Mcp-Adapter-Auth-Token"
    token: "${MCP_ADAPTER_TOKEN}"
    signing_secret: "${MCP_ADAPTER_SIGNING_SECRET}"
    signed_upload_ttl_seconds: 300

state_persistence:
  type: "redis"
  unavailable_policy: "fail_closed"

  redis:
    host: "redis.internal"
    port: 6379
    db: 0
    key_base: "remote-mcp-adapter"

storage:
  root: "/data/shared"
  lock_mode: "redis"
  max_size: "50Gi"
  orphan_sweeper_enabled: true
  orphan_sweeper_grace_seconds: 300

sessions:
  max_active: 500
  idle_ttl_seconds: 1800
  max_total_session_size: "1Gi"
  allow_revival: true
  tombstone_ttl_seconds: 86400

uploads:
  enabled: true
  max_file_bytes: "100Mi"
  ttl_seconds: 300
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 1800
  max_per_session: 100
  expose_as_resources: true

telemetry:
  enabled: true
  transport: "http"
  endpoint: "https://otel-collector.internal/v1/metrics"

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

This profile gives you scale and durability, but it also introduces real operational complexity.

### What you gain

- sessions survive across replicas
- rolling upgrades become much safer
- failover is possible at the adapter tier
- shared quotas and session state behave consistently
- the deployment is much closer to a real production control plane

### What you give up

- Redis becomes a hard dependency
- shared storage becomes mandatory
- debugging gets harder than on a single node
- misconfigured upstream affinity can still break stateful tools
- more observability and operational discipline are required

---

## Common distributed-production mistakes

!!! warning "Redis metadata without shared file storage"
  Redis solves shared session metadata. It does not solve shared file content. Uploads and artifacts still need a genuinely shared filesystem.

!!! warning "Shared file storage without distributed metadata"
  A shared volume alone does not make sessions portable across replicas. Metadata and file content both have to be consistent.

!!! warning "No `public_base_url`"
  Generated URLs need to point at the external service address, not an individual pod or container hostname.

!!! warning "Assuming adapter sticky sessions solve upstream HA"
  Sticky sessions on the client-to-adapter side do not automatically preserve session affinity on the adapter-to-upstream side.

!!! warning "Using `fallback_memory` in HA"
  In a distributed system this can silently create diverging per-replica state. That usually causes more confusion than a clean failure.

---

## When this should be your default production profile

Use this as your baseline when:

- the adapter is an important shared service
- more than one replica may serve traffic
- uptime matters more than absolute simplicity
- you already have Redis and shared storage available

If you do not need any of that yet, the single-node durable profile is usually easier to operate.

---

## Next steps

- **Back to:** [Configuration](../configuration.md) — overview and scenario index.
- **Previous scenario:** [Single-Node Durable Scenario](single-node-durable.md) — durable but not distributed.
- **See also:** [Security](../security/index.md) — auth, signed uploads, and protected endpoints.
- **See also:** [Telemetry](../telemetry.md) — exporter setup and queue tuning.
- **See also:** [Config Reference](config-reference.md) — exact behavior for Redis, locks, storage, and limits.
- **Next scenario:** [High-Security Scenario](high-security.md) — tighten auth, storage boundaries, and exposure surfaces.
