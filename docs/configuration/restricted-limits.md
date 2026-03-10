# Restricted-Limits Scenario

**What you'll learn here:** how to configure the adapter for tighter resource control, which limits matter most for shared environments, and how to reduce the chance that one client or workflow consumes disproportionate storage, session capacity, or upload bandwidth.

---

## What this scenario is for

This profile is for deployments where the main concern is not only topology or auth, but **resource pressure**.

Typical examples:

- a shared internal adapter used by many teams
- a cost-sensitive deployment where storage growth must stay predictable
- environments where runaway screenshot or PDF generation is a realistic risk
- clusters where uploads must be kept small and short-lived
- deployments where fairness matters more than convenience

This is best understood as a **limits-focused overlay**. You usually apply it on top of either:

- a single-node durable deployment, or
- a distributed production deployment

It is not about where the adapter runs. It is about how tightly you want to control consumption.

---

## What this scenario assumes

A typical restricted-limits deployment assumes:

- more than one user, team, or workload may share the adapter
- storage exhaustion is a real operational risk
- uploads and artifacts should not remain available indefinitely
- session counts and session size should have hard boundaries
- it is acceptable for some larger or longer workflows to be rejected

If you are trying to prevent one heavy workflow from quietly degrading everyone else's experience, this is the right scenario.

---

## Recommended knobs and values

These are the settings that matter most when you want the adapter to be stricter about resource usage.

### Storage

```yaml
storage:
  max_size: "5Gi"
```

Why:

- this creates a real upper bound on total stored file content
- without a global storage cap, the adapter can keep writing until the underlying volume fails
- in shared environments, total storage should always be intentional

The exact value depends on how many users and artifacts you expect, but the important part is that it is finite.

### Sessions

```yaml
sessions:
  max_active: 50
  idle_ttl_seconds: 900
  max_total_session_size: "250Mi"
  max_in_flight_per_session: 4
  tombstone_ttl_seconds: 3600
```

Why:

- `max_active` prevents unbounded session sprawl
- `idle_ttl_seconds` shortens the lifetime of abandoned sessions
- `max_total_session_size` limits how much one session can consume in aggregate
- `max_in_flight_per_session` stops a single session from flooding the adapter with too many simultaneous operations
- a shorter `tombstone_ttl_seconds` reduces the retention of dead-session metadata

These values make the system stricter and sometimes less forgiving, but that is the point of the profile.

### Uploads

```yaml
uploads:
  enabled: true
  max_file_bytes: "10Mi"
  ttl_seconds: 120
  require_sha256: true
```

Why:

- smaller upload caps protect disk, memory, and network usage
- shorter TTLs reduce how long staged content lingers if a tool call never follows
- `require_sha256: true` is still a good baseline when clients are sending real files into a shared service

If users regularly need larger uploads, raise the cap deliberately rather than leaving it effectively open-ended.

### Artifacts

```yaml
artifacts:
  enabled: true
  ttl_seconds: 600
  max_per_session: 10
  expose_as_resources: true
```

Why:

- artifact generation can be surprisingly expensive in real workflows
- tighter `max_per_session` values prevent endless file creation loops
- shorter artifact TTLs keep the shared storage pool cleaner
- `expose_as_resources: true` keeps normal MCP behavior intact even while limits are stricter

### Core and auth

```yaml
core:
  allow_artifacts_download: false

  auth:
    enabled: true
```

Why:

- restricted limits are most useful in shared environments, and shared environments usually should not be anonymous
- disabling artifact HTTP downloads is one way to reduce the number of external access paths if MCP resources alone are enough for your clients

### State persistence and topology

This profile works on top of both single-node and distributed setups.

- for single-node durable deployments, pair it with `state_persistence.type: "disk"`
- for multi-replica deployments, pair it with `state_persistence.type: "redis"`

The overlay is about limits, not topology, so those choices should already come from the base scenario you are applying it to.

---

## Full example

This example shows the overlay-style knobs you would tighten. It is not intended to replace the full base scenario.

```yaml
core:
  allow_artifacts_download: false

  auth:
    enabled: true

storage:
  max_size: "5Gi"

sessions:
  max_active: 50
  idle_ttl_seconds: 900
  max_total_session_size: "250Mi"
  max_in_flight_per_session: 4
  tombstone_ttl_seconds: 3600

uploads:
  enabled: true
  max_file_bytes: "10Mi"
  ttl_seconds: 120
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 600
  max_per_session: 10
  expose_as_resources: true
```

---

## What this profile tightens

Compared to a more permissive setup, this profile deliberately:

- lowers the maximum number of active sessions
- shortens idle session lifetime
- caps total storage used by a single session
- caps the number of concurrent operations per session
- reduces maximum upload size
- shortens upload retention
- reduces artifact count and retention
- forces operators to make conscious tradeoffs about file-heavy workflows

This profile is about predictability and fairness, not maximum flexibility.

---

## Common restricted-limits mistakes

!!! warning "Applying strict caps without telling users"
  Smaller upload and artifact limits will change user behavior. If clients are used to large screenshots, PDFs, or file batches, they need to know what changed.

!!! warning "Capping everything, but leaving `storage.max_size` unset"
  Per-session limits help, but the whole deployment can still drift into disk exhaustion if total storage remains uncapped.

!!! warning "Using long TTLs with tight size caps"
  Very small quotas combined with very long retention windows create unnecessary pressure. If you want stricter limits, cleanup usually needs to become more aggressive too.

!!! warning "Treating resource limits as a substitute for auth"
  Rate and size limits help with fairness and containment, but they do not replace authentication and access control.

!!! warning "Being stricter than the upstream can tolerate"
  Some workflows legitimately need larger files or more artifacts. If the limits are too tight, users may experience failures that look arbitrary unless the system's expectations are documented clearly.

---

## When to apply this profile

Use this overlay when:

- the adapter is shared by many users or tenants
- storage growth needs to remain bounded and predictable
- fairness matters more than maximum throughput for any one session
- operators would rather reject oversized workflows than risk platform-wide pressure

If the main concern is exposure risk rather than capacity control, the high-security profile is the more relevant overlay.

---

## Next steps

- **Back to:** [Configuration](../configuration.md) — overview and scenario index.
- **Previous scenario:** [High-Security Scenario](high-security.md) — security-focused hardening.
- **See also:** [Config Reference](config-reference.md) — exact fields for sessions, uploads, artifacts, and storage limits.
- **See also:** [Security](../security.md) — auth still matters even in limit-focused deployments.
- **Next scenario:** [High-Observability Scenario](high-observability.md) — improve telemetry, runtime visibility, and production debugging.
