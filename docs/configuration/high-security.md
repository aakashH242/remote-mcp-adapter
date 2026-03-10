# High-Security Scenario

**What you'll learn here:** which settings matter most when the adapter is exposed to untrusted clients or shared environments, which defaults should become stricter, and how to reduce the blast radius of uploads, artifacts, and tool execution.

---

## What this scenario is for

This is a security-hardened profile for deployments where convenience should no longer be the default.

Typical examples:

- an adapter exposed outside localhost
- an internal platform used by multiple teams
- an environment with compliance or audit expectations
- a shared gateway in front of sensitive upstream tools
- production systems where uploads and artifact access must be tightly controlled

This scenario is best understood as a **security overlay**. You usually apply it on top of either:

- a single-node durable deployment, or
- a distributed production deployment

It is not about topology. It is about tightening the operational posture.

---

## What this scenario assumes

A typical high-security deployment assumes:

- the adapter is reachable over a real network
- unauthenticated access is unacceptable
- uploaded content should be treated as untrusted
- artifact access should be constrained deliberately
- filesystem escape risks should be minimized
- operators prefer explicit failures over permissive fallback behavior

If you are asking, “What should we tighten before we call this safe enough?”, this is the right scenario.

---

## Recommended knobs and values

These are the highest-value security settings to make explicit.

### Core

```yaml
core:
  public_base_url: "https://mcp-adapter.example.com"
  allow_artifacts_download: false
  code_mode_enabled: false
```

Why:

- `public_base_url` should be explicit in secure deployments so generated URLs never depend on inferred internal addresses.
- `allow_artifacts_download: false` removes one HTTP access surface unless you truly need browser- or URL-based artifact fetches.
- `code_mode_enabled: false` keeps the default behavior predictable unless you have a specific reason to expose Code Mode.

Even when artifact downloads stay off, `public_base_url` still matters if you expose `<server_id>_get_upload_url(...)`. It keeps signed upload URLs anchored to the public address your clients are supposed to use.

### Auth

```yaml
core:
  auth:
    enabled: true
    header_name: "X-Mcp-Adapter-Auth-Token"
    token: "${MCP_ADAPTER_TOKEN}"
    signing_secret: "${MCP_ADAPTER_SIGNING_SECRET}"
    signed_upload_ttl_seconds: 120
```

Why:

- auth must be enabled
- the main token and signing secret should be separate
- a shorter signed upload TTL reduces replay opportunity and stale-link exposure
- all secrets should come from environment interpolation, not committed literals

If your users regularly upload large files over slower networks, increase the TTL carefully rather than disabling signing discipline.

### CORS

```yaml
core:
  cors:
    enabled: false
```

Why:

- if the adapter does not need to be called directly from browsers, keep CORS off
- every unnecessary browser-facing surface is one more thing to reason about

If browser access is required, use a very tight allow-list:

```yaml
core:
  cors:
    enabled: true
    allowed_origins:
      - "https://app.example.com"
    allowed_methods: ["POST", "GET", "OPTIONS"]
    allowed_headers: ["*"]
    allow_credentials: false
```

### State persistence

```yaml
state_persistence:
  unavailable_policy: "fail_closed"
```

Why:

- secure deployments should prefer explicit failure to silent degraded behavior
- if shared state or durable state is unavailable, serving requests from an uncertain fallback mode is usually the wrong security posture

For distributed deployments, avoid `fallback_memory`. For single-node deployments, use it only if you have consciously accepted the tradeoff.

### Storage

```yaml
storage:
  root: "/data/shared"
  max_size: "10Gi"
  lock_mode: "auto"
  artifact_locator_policy: "storage_only"
  artifact_locator_allowed_roots: []
```

Why:

- `artifact_locator_policy: "storage_only"` is the safest baseline because it prevents artifact lookup from wandering into broader configured roots
- leaving `artifact_locator_allowed_roots` empty avoids accidental path expansion
- `max_size` prevents storage exhaustion from becoming a silent denial-of-service vector
- `lock_mode: "auto"` is fine as long as the underlying persistence topology is already correct

Only use `allow_configured_roots` when you have a very specific and reviewed need.

### Sessions, uploads, and artifacts

```yaml
sessions:
  max_active: 100
  idle_ttl_seconds: 1800
  max_total_session_size: "500Mi"
  allow_revival: true

uploads:
  enabled: true
  max_file_bytes: "25Mi"
  ttl_seconds: 120
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 600
  max_per_session: 25
  expose_as_resources: true
```

Why:

- secure deployments should set explicit ceilings instead of trusting defaults
- `require_sha256: true` adds integrity checking to file intake
- shorter TTLs reduce the lifetime of uploaded and generated content
- tighter `max_file_bytes` and `max_per_session` reduce abuse potential and accidental runaway workflows

These numbers are starting points, not universal truths. The important part is to define real limits.

### Servers

The biggest security question at the server layer is not only where the upstream lives, but **what tools should actually be exposed**.

```yaml
servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    disabled_tools:
      - "dangerous_tool_name"
      - "^experimental_.*$"

    upstream:
      transport: "streamable_http"
      url: "http://playwright.internal:8931/mcp"
```

Why:

- not every upstream tool belongs in every environment
- hiding risky, admin-like, or experimental tools reduces accidental exposure
- per-server review is often more valuable than global hand-waving about “security”

### Telemetry

```yaml
telemetry:
  enabled: true
```

Why:

- security without visibility is weak operations
- even minimal telemetry helps detect misuse patterns, failures, and pressure on uploads or sessions
- in serious environments, telemetry and logs are part of the security posture, not separate from it

---

## Full example

```yaml
core:
  public_base_url: "https://mcp-adapter.example.com"
  allow_artifacts_download: false
  code_mode_enabled: false

  auth:
    enabled: true
    header_name: "X-Mcp-Adapter-Auth-Token"
    token: "${MCP_ADAPTER_TOKEN}"
    signing_secret: "${MCP_ADAPTER_SIGNING_SECRET}"
    signed_upload_ttl_seconds: 120

  cors:
    enabled: false

state_persistence:
  unavailable_policy: "fail_closed"

storage:
  root: "/data/shared"
  max_size: "10Gi"
  lock_mode: "auto"
  artifact_locator_policy: "storage_only"
  artifact_locator_allowed_roots: []

sessions:
  max_active: 100
  idle_ttl_seconds: 1800
  max_total_session_size: "500Mi"
  allow_revival: true

uploads:
  enabled: true
  max_file_bytes: "25Mi"
  ttl_seconds: 120
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 600
  max_per_session: 25
  expose_as_resources: true

telemetry:
  enabled: true

servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    disabled_tools:
      - "dangerous_tool_name"
      - "^experimental_.*$"

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

## What this profile tightens

Compared to a more permissive setup, this profile deliberately:

- turns auth from optional into mandatory
- shortens the lifetime of signed upload URLs
- prefers no browser CORS by default
- disables HTTP artifact downloads unless explicitly needed
- keeps artifact resolution inside the storage root
- requires upload checksums
- applies real limits to sessions, uploads, and artifacts
- encourages per-server tool pruning

That is the point of the profile: reduce accidental exposure and reduce the amount of trust the environment demands.

---

## Common high-security mistakes

!!! warning "Auth enabled, but secrets are committed"
  A strong setting is weakened immediately if the token or signing secret is committed to source control or copied into shared plaintext files.

!!! warning "Turning on artifact downloads by habit"
  HTTP download links are convenient, but they increase the external surface area. Leave them off unless there is a real client need.

!!! warning "Using `allow_configured_roots` casually"
  Expanding artifact lookup outside the storage root can be valid, but it should be a deliberate, reviewed exception rather than a default.

!!! warning "High security without limits"
  Security is not only auth. Unbounded uploads, sessions, and artifacts still create denial-of-service risk.

!!! warning "Protecting the adapter but not reviewing upstream tools"
  The adapter can protect transport and storage boundaries, but it cannot make an inherently dangerous upstream tool safe just by proxying it.

---

## When to apply this profile

Use this as an overlay when:

- the adapter is reachable by anything beyond a single trusted developer
- the environment is shared
- uploads may contain sensitive or untrusted content
- operators want stricter defaults than the convenience-oriented scenarios

If the main problem is capacity pressure rather than exposure risk, the next more relevant profile is a restricted-limits overlay.

---

## Next steps

- **Back to:** [Configuration](../configuration.md) — overview and scenario index.
- **Previous scenario:** [Distributed Production Scenario](distributed-production.md) — topology first, then hardening.
- **See also:** [Security](../security.md) — header auth, signed URLs, and protected endpoints.
- **See also:** [Config Reference](config-reference.md) — exact fields for auth, CORS, storage, and limits.
- **Next scenario:** [Restricted-Limits Scenario](restricted-limits.md) — tighten resource ceilings for shared environments.
