# High-Observability Scenario

**What you'll learn here:** which telemetry and operational visibility settings matter once the adapter becomes a real service, what signals are worth turning on first, and how to improve debugging and incident response without changing the core MCP behavior.

---

## What this scenario is for

This profile is for deployments where operators need to understand what the adapter is doing in production, not just whether it is up.

Typical examples:

- a shared internal platform with on-call ownership
- a production deployment that needs metrics, traces-adjacent visibility, or export to an OTel collector
- environments where upstream instability must be diagnosed quickly
- deployments where queue pressure, cleanup behavior, and session churn need to be visible
- systems where health checks alone are not enough for operations

This is best understood as an **observability overlay**. You usually apply it on top of either:

- a single-node durable deployment, or
- a distributed production deployment

It is not about changing business behavior. It is about making behavior visible.

---

## What this scenario assumes

A typical high-observability setup assumes:

- the adapter is important enough to monitor deliberately
- there is an OpenTelemetry collector or vendor endpoint available
- operators want more than a binary healthy/unhealthy signal
- production debugging speed matters
- some additional operational complexity is acceptable in exchange for better visibility

If you expect to answer questions like “Why are uploads failing?”, “Why is this upstream degrading?”, or “Why is storage pressure climbing?”, this is the right scenario.

---

## Recommended knobs and values

These are the highest-value settings to make explicit when observability matters.

### Core

```yaml
core:
  log_level: "info"
  max_start_wait_seconds: 60
  cleanup_interval_seconds: 60
  upstream_metadata_cache_ttl_seconds: 60
```

Why:

- `log_level: "info"` gives operators more useful runtime signals than quieter defaults
- startup and cleanup timings become easier to reason about when they are explicitly set
- a shorter metadata cache TTL can make upstream changes visible faster during operations and debugging

This profile is not about noisy debugging logs all the time. It is about having enough signal to operate the service confidently.

### Telemetry

```yaml
telemetry:
  enabled: true
  transport: "http"
  endpoint: "https://otel-collector.internal/v1/metrics"
  logs_endpoint: "https://otel-collector.internal/v1/logs"
  service_name: "remote-mcp-adapter"
  service_namespace: "mcp"
  export_interval_seconds: 10
  export_timeout_seconds: 5
  max_queue_size: 2048
  queue_batch_size: 256
  periodic_flush_seconds: 5
  shutdown_drain_timeout_seconds: 10
  emit_logs: true
  flush_on_shutdown: true
  drop_on_queue_full: true
```

Why:

- `enabled: true` is the core switch that turns observability from optional to real
- `service_name` and `service_namespace` make environments easier to separate in observability backends
- explicit export cadence and queue sizing make telemetry behavior predictable under load
- `flush_on_shutdown: true` improves signal preservation during restarts and rollouts

If your collector requires headers, add them explicitly:

```yaml
telemetry:
  headers:
    Authorization: "Bearer ${OTEL_TOKEN}"
```

### Telemetry queue behavior

```yaml
telemetry:
  max_queue_size: 2048
  queue_batch_size: 256
  periodic_flush_seconds: 5
  drop_on_queue_full: true
```

Why:

- in production, blocking core request handling because the telemetry pipeline is slow is usually the wrong tradeoff
- dropping excess telemetry under sustained pressure is often preferable to coupling service latency to exporter latency
- batch sizing and flush cadence let you tune exporter pressure without relying on unsupported worker-count knobs

### Health and upstream behavior

```yaml
core:
  max_start_wait_seconds: 60

  upstream_ping:
    interval_seconds: 10
    timeout_seconds: 3
    failure_threshold: 3
    open_cooldown_seconds: 15
    half_open_probe_allowance: 3
```

Why:

- upstream visibility matters more once multiple dependencies are involved
- explicit ping and cooldown settings make degraded behavior easier to interpret from metrics and logs
- operators need to understand whether an upstream is down, flapping, or recovering

### State persistence and topology

This profile works with both durable single-node and distributed deployments.

- for one-node setups, pair it with disk persistence
- for multi-replica setups, pair it with Redis persistence

Observability does not replace a sound topology. It helps you understand the topology you already chose.

### Auth and security

```yaml
core:
  auth:
    enabled: true
```

Why:

- observability is not a replacement for auth
- production services that are important enough to monitor are usually also important enough to protect

### Storage and limits

This overlay should usually be paired with explicit limits from either the durable or restricted-limits profiles.

Why:

- observability tells you when pressure is rising
- limits tell the system what to do before pressure becomes catastrophic
- both together are much more useful than either one alone

---

## Full example

This example focuses on the observability-specific settings you would layer onto a real deployment.

```yaml
core:
  log_level: "info"
  max_start_wait_seconds: 60
  cleanup_interval_seconds: 60
  upstream_metadata_cache_ttl_seconds: 60

  auth:
    enabled: true

  upstream_ping:
    interval_seconds: 10
    timeout_seconds: 3
    failure_threshold: 3
    open_cooldown_seconds: 15
    half_open_probe_allowance: 3

telemetry:
  enabled: true
  transport: "http"
  endpoint: "https://otel-collector.internal/v1/metrics"
  logs_endpoint: "https://otel-collector.internal/v1/logs"
  service_name: "remote-mcp-adapter"
  service_namespace: "mcp"
  export_interval_seconds: 10
  export_timeout_seconds: 5
  max_queue_size: 2048
  queue_batch_size: 256
  periodic_flush_seconds: 5
  shutdown_drain_timeout_seconds: 10
  emit_logs: true
  drop_on_queue_full: true
  flush_on_shutdown: true

  headers:
    Authorization: "Bearer ${OTEL_TOKEN}"
```

---

## What this profile improves

Compared to a minimally instrumented deployment, this profile gives you better visibility into:

- upstream health and recovery behavior
- exporter backlog and telemetry pressure
- per-instance runtime behavior in multi-node environments
- whether restarts and shutdowns lose operational signal
- whether configuration changes have measurable effect

This profile improves your ability to explain and diagnose behavior. It does not replace careful limits, auth, or topology design.

---

## Common high-observability mistakes

!!! warning "Turning on telemetry without naming the service clearly"
  Metrics become much less useful when multiple environments and services all report under ambiguous names.

!!! warning "Letting telemetry backpressure affect request handling"
  Observability should help the service, not become a new availability risk. Queue and drop behavior should be chosen intentionally.

!!! warning "Using observability as a substitute for limits"
  Metrics can tell you that storage or sessions are growing, but they do not enforce any ceiling on their own.

!!! warning "Collector auth left implicit"
  If your exporter requires headers or vendor tokens, define them explicitly instead of assuming the environment around the process will always inject them correctly.

!!! warning "No one reviews the signals"
  Instrumentation only helps if somebody actually knows what the important dashboards, alerts, and failure patterns look like.

---

## When to apply this profile

Use this overlay when:

- the adapter has operational owners
- incident response speed matters
- upstream instability needs to be diagnosed quickly
- production behavior needs to be measured rather than guessed

If your bigger concern is strict risk reduction or strict fairness, the high-security and restricted-limits profiles are the more immediately relevant overlays.

---

## Next steps

- **Back to:** [Configuration](../configuration.md) — overview and scenario index.
- **Previous scenario:** [Restricted-Limits Scenario](restricted-limits.md) — stricter resource ceilings.
- **See also:** [Telemetry](../telemetry.md) — exporter setup, field meanings, and operational details.
- **See also:** [Health](../health.md) — health endpoint behavior and degraded states.
- **Next scenario:** [Agent-Optimized Code Mode Scenario](agent-optimized-code-mode.md) — compact discovery for coding agents and smaller models.
