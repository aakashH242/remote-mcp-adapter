# Local Dev Scenario

The starting profile for first-time setup, local iteration, and debugging the adapter's file-handling flows. Optimized for simplicity and fast feedback, not for security or production scale.

---

## What this scenario is for

This is the best starting profile for:

- trying the adapter for the first time
- running everything on your own machine
- debugging upload and artifact flows
- iterating on config quickly without production hardening
- local Docker Compose or direct-from-source development

This profile is intentionally optimized for **simplicity and fast feedback**, not for security or horizontal scale.

---

## What this scenario assumes

A typical local-dev setup looks like this:

- one adapter process
- one upstream MCP server, or a small number of upstreams
- one shared local directory for uploads and artifacts
- no public internet exposure
- no requirement for durable multi-node state
- convenience matters more than strict limits

If that sounds like your setup, this is the right first scenario.

---

## Recommended knobs and values

These are the main settings to care about for local development.

### Core

```yaml
core:
  host: "0.0.0.0"
  port: 8932
  log_level: "info"
  allow_artifacts_download: true
  code_mode_enabled: false
```

`host: "0.0.0.0"` works well in Docker and local containers. `log_level: "info"` is easier to debug than the quieter production-style defaults. `allow_artifacts_download: true` is convenient when you want to quickly inspect generated files. `code_mode_enabled: false` keeps the full tool surface visible while you are learning and debugging. You can usually leave `public_base_url` unset locally because the adapter can infer a usable address on localhost.

If your local setup runs behind a tunnel, reverse proxy, or some other hostname that the client uses instead of `localhost`, set `core.public_base_url` anyway. That keeps upload helper URLs and download links honest.

### Auth

```yaml
core:
  auth:
    enabled: false
```

Local development is usually trusted and short-lived. Disabling auth removes client setup friction. You should not keep this setting for anything exposed beyond localhost or a private dev environment.

### State persistence

```yaml
state_persistence:
  type: "memory"
  snapshot_interval_seconds: 30
  unavailable_policy: "fallback_memory"
```

`memory` is the simplest option and avoids external dependencies. It is ideal when you do not care about durable sessions across restarts — the adapter starts fast and is easy to reset. If you want sessions to survive restarts on your own machine, `disk` is also reasonable for local work, but `memory` is the cleanest default profile.

### Storage

```yaml
storage:
  root: "/tmp/mcp-adapter-data"
  lock_mode: "process"
  max_size: null
```

Use a path you can inspect and wipe easily. `process` locking is enough for a single process on one machine. `max_size: null` keeps the setup friction low while you experiment.

If you use Docker or Compose, the path should match the mounted shared directory used by both the adapter and the upstream container.

### Sessions, uploads, and artifacts

```yaml
sessions:
  idle_ttl_seconds: 1800
  max_active: null
  max_total_session_size: null
  allow_revival: true

uploads:
  enabled: true
  max_file_bytes: "50Mi"
  ttl_seconds: 300
  require_sha256: false

artifacts:
  enabled: true
  ttl_seconds: 1800
  max_per_session: null
  expose_as_resources: true
```

Keep uploads and artifacts enabled so you exercise the adapter's main value. Use a real idle TTL so forgotten sessions do not pile up forever. Use a slightly longer upload TTL than the smallest defaults so manual testing is less annoying. Keep `require_sha256: false` for convenience while iterating locally. Leave the stricter per-session caps unset unless you are deliberately testing quota behavior.

### Telemetry

```yaml
telemetry:
  enabled: false
```

Unnecessary for most local work — fewer moving parts and less noise while debugging basic behavior.

### Servers

A good local-dev server entry usually looks like this:

```yaml
servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    upstream:
      transport: "streamable_http"
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

This exercises both the upload path and the artifact path. It is the fastest way to confirm the adapter is doing real work instead of only passing requests through, and it matches the most common first-run setup in this repository.

---

## Full example

```yaml
core:
  host: "0.0.0.0"
  port: 8932
  log_level: "info"
  allow_artifacts_download: true
  code_mode_enabled: false

  auth:
    enabled: false

state_persistence:
  type: "memory"
  snapshot_interval_seconds: 30
  unavailable_policy: "fallback_memory"

storage:
  root: "/tmp/mcp-adapter-data"
  lock_mode: "process"
  max_size: null

sessions:
  idle_ttl_seconds: 1800
  max_active: null
  max_total_session_size: null
  allow_revival: true

uploads:
  enabled: true
  max_file_bytes: "50Mi"
  ttl_seconds: 300
  require_sha256: false

artifacts:
  enabled: true
  ttl_seconds: 1800
  max_per_session: null
  expose_as_resources: true

telemetry:
  enabled: false

servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    upstream:
      transport: "streamable_http"
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

---

## Tradeoffs

This profile is intentionally lenient.

### What you gain

- easiest first setup
- no Redis or database dependency
- fewer auth and telemetry variables to manage
- easier debugging and iteration
- easier manual artifact inspection

### What you give up

- no real security boundary
- no durable cross-restart session state
- no horizontal scale story
- weak quota discipline
- not suitable for public or shared production environments

---

## Signs you should move past this profile

Switch to a stricter scenario when any of the following becomes true:

- the adapter is reachable by other people or systems
- you want sessions to survive restarts reliably
- you need multiple replicas
- disk growth starts to matter
- you want telemetry, stronger auth, or stricter upload controls

When that happens, the next likely profiles are:

- a single-node durable deployment
- a distributed production deployment
- a high-security overlay
- a restricted-limits overlay

---

## Common local-dev mistakes

!!! warning "Storage path mismatch"
  The adapter and upstream must agree on the same shared directory. If the adapter writes to one path and the upstream reads from another, uploads and artifacts will fail in confusing ways.

!!! warning "Testing passthrough only"
  If you do not configure at least one `upload_consumer` or `artifact_producer`, you are mostly testing plain proxying rather than the adapter's file-handling behavior.

!!! warning "Using this profile outside local environments"
  `auth.enabled: false` and unbounded size limits are convenient locally, but they are bad defaults for shared or internet-reachable deployments.

---

## Next steps

- **Back to:** [Configuration](../configuration.md) — overview and baseline guidance.
- **Previous scenario:** [Passthrough-Only Gateway Scenario](passthrough-only-gateway.md) — pure relay without adapter-wrapped file handling.
- **See also:** [Getting Started](../getting-started.md) — local run paths.
- **See also:** [Core Concepts](../core-concepts.md) — understand `upload://` and `artifact://` flows.
- **Next scenario:** [Single-Node Durable Scenario](single-node-durable.md) — add persistence, auth, and real limits without going distributed.
