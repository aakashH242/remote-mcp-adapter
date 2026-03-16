# Passthrough-Only Gateway Scenario

Auth, routing, health checks, and circuit-breaker protection in front of one or more upstreams — without any upload or artifact mediation. The pure relay shape.

---

## What this scenario is for

This profile is for teams that want the adapter's **gateway behavior**, not its upload and artifact mediation.

Typical examples:

- one adapter in front of several upstream MCP servers
- a platform team that wants one stable entry point and one auth model
- environments where upstream tools already behave correctly without `upload://` or `artifact://` rewriting
- deployments that mainly need health checks, routing, and failure isolation
- a first rollout where you want to standardize access before you introduce adapter-wrapped tools

If your main goal is "put one clean relay in front of multiple MCP servers," this is the right place to start.

---

## What this scenario assumes

A passthrough-only gateway usually looks like this:

- one adapter process or deployment
- one or more upstream MCP servers behind it
- no `upload_consumer` entries
- no `artifact_producer` entries
- clients connect to the adapter, not directly to the upstreams
- the value comes from central auth, stable routing, and operational guardrails

This profile is intentionally simple. It does **not** try to exercise the adapter's file-handling features.

---

## Recommended knobs and values

These are the settings that matter most when the adapter is acting as a relay.

### Core

```yaml
core:
  host: "0.0.0.0"
  port: 8932
  log_level: "info"
  public_base_url: "https://mcp-gateway.example.com"
  allow_artifacts_download: false
  code_mode_enabled: false
```

`public_base_url` is still worth setting once clients use a real hostname, even if you are not relying on upload helpers right away. `allow_artifacts_download: false` keeps the surface area smaller because this profile is not about download links. `code_mode_enabled: false` keeps the normal tool list visible while teams get used to the gateway. `log_level: "info"` is a good operating default for a shared relay.

### Auth

```yaml
core:
  auth:
    enabled: true
    token: "${MCP_ADAPTER_TOKEN}"
```

Centralized auth is one of the main reasons to use this profile. It gives clients one consistent header and token model even if upstreams differ internally, and it keeps the relay useful even when the upstreams are otherwise simple.

### Upstream health and circuit breaker

```yaml
core:
  upstream_ping:
    enabled: true
    interval_seconds: 15
    timeout_seconds: 5
    failure_threshold: 3
    open_cooldown_seconds: 30
    half_open_probe_allowance: 2
```

This is another major reason to keep the adapter in front of upstreams. Unhealthy upstreams fail fast instead of hanging every caller, and recovery behavior becomes visible and predictable.

### Uploads and artifacts

```yaml
uploads:
  enabled: false

artifacts:
  enabled: false
```

This makes the intent obvious: you are not trying to use file mediation at all. Clients will not see upload-helper behavior that nobody plans to use, which reduces confusion for teams that only want a gateway layer.

### Storage and persistence

If you are truly using the adapter as a relay only, these can stay simple.

```yaml
state_persistence:
  type: "memory"

storage:
  root: "/tmp/remote-mcp-adapter"
```

Without upload and artifact handling, the storage and persistence pressure is much lower. `memory` is often enough if you do not need durable session recovery, and you can tighten this later if the relay becomes more important operationally.

If the gateway becomes shared or long-lived, moving to `disk` persistence is still reasonable.

### Servers

A passthrough-only config deliberately has **no** `adapters` entries.

```yaml
servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    upstream:
      transport: "streamable_http"
      url: "http://playwright.internal:8931/mcp"

  - id: "fetch"
    mount_path: "/mcp/fetch"
    upstream:
      transport: "streamable_http"
      url: "http://fetch.internal:8080/mcp"
```

Every upstream tool remains passthrough. The adapter still provides a stable mount path per upstream, giving teams one place for auth, health, and operational policy without changing tool behavior.

---

## Full example

```yaml
core:
  host: "0.0.0.0"
  port: 8932
  log_level: "info"
  public_base_url: "https://mcp-gateway.example.com"
  allow_artifacts_download: false
  code_mode_enabled: false

  auth:
    enabled: true
    token: "${MCP_ADAPTER_TOKEN}"

  upstream_ping:
    enabled: true
    interval_seconds: 15
    timeout_seconds: 5
    failure_threshold: 3
    open_cooldown_seconds: 30
    half_open_probe_allowance: 2

state_persistence:
  type: "memory"

storage:
  root: "/tmp/remote-mcp-adapter"

uploads:
  enabled: false

artifacts:
  enabled: false

servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    upstream:
      transport: "streamable_http"
      url: "http://playwright.internal:8931/mcp"

  - id: "fetch"
    mount_path: "/mcp/fetch"
    upstream:
      transport: "streamable_http"
      url: "http://fetch.internal:8080/mcp"
```

---

## What you gain

- one stable front door for multiple MCP servers
- one auth model for clients
- one place to observe upstream health
- circuit-breaker protection in front of flaky upstreams
- cleaner routing and simpler client configuration

## What you do not get

- no upload helper tools
- no `upload://` resolution
- no artifact capture or `artifact://` resources
- no adapter-managed file lifecycle benefits

That tradeoff is often perfectly fine. Sometimes a relay is exactly what a team needs.

---

## Common passthrough-gateway mistakes

!!! warning "Expecting artifacts without configuring adapters"
  If a tool writes files and you do not wrap it with an `artifact_producer`, the adapter will not capture those files for you. This profile is deliberately not solving that problem.

!!! warning "Leaving uploads enabled just because they exist"
  If your deployment is meant to be relay-only, make that explicit. It is easier for operators and users to understand what the service is supposed to do.

!!! warning "Using passthrough as a permanent excuse to avoid good routing"
  Even a pure relay still benefits from intentional `mount_path` design, auth, and upstream health settings.

!!! warning "Forgetting that upstream behavior still owns the user experience"
  The adapter can standardize access, but it does not make a poorly behaved upstream suddenly stateful, scalable, or user-friendly.

---

## When to move past this profile

Move beyond this profile when:

- you need upload helper tools
- you need artifact capture and download links
- you want the adapter to rewrite tool behavior rather than just relay it
- users start asking for safer file workflows instead of raw upstream behavior

That usually means the next step is the local-dev scenario or a more production-oriented file-handling profile.

---

## Next steps

- **Back to:** [Configuration](../configuration.md) — overview and scenario index.
- **See also:** [Core Concepts](../core-concepts.md) — understand passthrough behavior and what adapters add.
- **See also:** [How It Works](../how-it-works.md) — request flow, routing, and health behavior.
- **Next scenario:** [Local Dev Scenario](local-dev.md) — start adding upload and artifact handling without much operational overhead.
