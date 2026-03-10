# Helm Scenario: Observability-First Production

**What you'll learn here:** how to deploy the adapter with operator-friendly defaults, where telemetry-related values belong in the chart, and how to combine Helm values with secret-backed credentials for a monitored production service.

---

## What this scenario is for

This page is best treated as an overlay, not as a full deployment shape by itself.

Use it when you already have a base Helm shape and now want to add telemetry-heavy production settings without mixing those concerns into every other example.

Choose it when:

- the deployment has on-call ownership
- you want explicit telemetry endpoints and pod metadata
- runtime visibility matters as much as basic availability
- you want a chart example that includes env-from-secret for observability credentials

Good base shapes to layer this onto:

- [Standalone Durable Service](standalone-durable.md)
- [Distributed Shared Platform](distributed-shared-platform.md)
- [HA Adapter Tier](ha-adapter-tier.md)

---

## Suggested values

Save this as `values-observability.yaml`:

If you want a ready-made overlay file from the repository, start from [values-observability.yaml](../../examples/helm/values-observability.yaml).

```yaml
deploymentMode: distributed
replicaCount: 2

podAnnotations:
  prometheus.io/scrape: "true"

environment:
  envFromSecret:
    name: remote-mcp-adapter-env
    keys: [MCP_ADAPTER_TOKEN, OTEL_TOKEN]

config:
  config.yaml:
    core:
      auth:
        enabled: true
        token: ${MCP_ADAPTER_TOKEN}
      upstream_ping:
        interval_seconds: 10
        timeout_seconds: 3
        failure_threshold: 3
    telemetry:
      enabled: true
      transport: http
      endpoint: https://otel-collector.example.com/v1/metrics
      logs_endpoint: https://otel-collector.example.com/v1/logs
      headers:
        Authorization: Bearer ${OTEL_TOKEN}
      emit_logs: true
      service_name: remote-mcp-adapter
      service_namespace: mcp
```

This values file assumes the base shape already covers the rest of the deployment story, including:

- `config.config.yaml.servers`
- storage and persistence choices
- ingress or service exposure
- replica and disruption strategy

On its own, this page is incomplete by design.

---

## Commands

```bash
helm repo add remote-mcp-adapter https://aakashh242.github.io/remote-mcp-adapter
helm repo update
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-ha-adapter.yaml \
  -f values-observability.yaml
```

Use a different base file if your real shape is standalone durable or single-replica distributed.

---

## What this gives you

- a chart example that already assumes monitored production ownership
- explicit telemetry exporter settings
- secret-backed telemetry credentials
- a cleaner bridge between Helm values and the runtime telemetry config

---

## Pair it with

- [High-Observability Scenario](../../configuration/high-observability.md)
- [Telemetry](../../telemetry.md)

---

## Next steps

- **Back to:** [Deploy with Helm](../helm.md) — Helm overview and scenario selection.
- **Previous scenario:** [Browser-Facing Ingress](browser-facing-ingress.md) — ingress and human-clickable links.
- **Next:** [Layered values-file pairs](layered-values-file-pairs.md) — combine a base shape and overlays cleanly before final verification.
- **See also:** [Troubleshooting](../../troubleshooting.md) — common operational failures and fixes.
