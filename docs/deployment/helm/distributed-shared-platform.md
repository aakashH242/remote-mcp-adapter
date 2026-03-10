# Helm Scenario: Distributed Shared Platform

**What you'll learn here:** how to deploy the adapter independently from upstream services, which values define that separation, and when this shape makes more sense than a sidecar-style standalone pod.

---

## What this scenario is for

This is the right shape when upstreams already exist as their own services.

Choose it when:

- the adapter should be deployed independently of upstream servers
- upstream teams own their own services
- you want a cleaner platform boundary
- you are moving toward a shared internal MCP gateway

Redis is not automatically required for this shape. If you keep a single adapter replica, disk-backed state can still be enough. Redis becomes important when multiple adapter replicas need to share the same session metadata.

One subtle but important point: this chart only mounts the shared storage into the adapter pod. If your upstream tools use `upload_consumer` or `artifact_producer` adapters, those external upstream services must also mount the same shared filesystem at the same effective path themselves. Shared metadata alone is not enough; the files must be reachable from both sides.

---

## Suggested values

Save this as `values-distributed.yaml`:

If you want a ready-made file from the repository instead of rebuilding it by hand, start from [values-distributed.yaml](../../examples/helm/values-distributed.yaml).

```yaml
deploymentMode: distributed
replicaCount: 1

persistence:
  enabled: true
  existingClaim: remote-mcp-adapter-shared

environment:
  envFromSecret:
    name: remote-mcp-adapter-env
    keys: [MCP_ADAPTER_TOKEN]

ingress:
  enabled: true
  hosts:
    - host: mcp-gateway.example.com
      paths:
        - path: /
          pathType: Prefix

config:
  config.yaml:
    core:
      public_base_url: https://mcp-gateway.example.com
      auth:
        enabled: true
        token: ${MCP_ADAPTER_TOKEN}
    storage:
      root: /data/shared
    state_persistence:
      type: disk
    servers:
      - id: playwright
        mount_path: /mcp/playwright
        upstream:
          transport: streamable_http
          url: http://playwright.default.svc.cluster.local:8931/mcp
      - id: fetch
        mount_path: /mcp/fetch
        upstream:
          transport: streamable_http
          url: http://fetch.default.svc.cluster.local:8080/mcp
```

---

## Commands

```bash
helm repo add remote-mcp-adapter https://aakashh242.github.io/remote-mcp-adapter
helm repo update
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-distributed.yaml
```

---

## What this gives you

- a clean adapter service boundary
- independent upstream release cycles
- room to grow into a shared internal platform
- one place for auth, ingress, routing, and relay policy

---

## Pair it with

- [Passthrough-Only Gateway Scenario](../../configuration/passthrough-only-gateway.md) if you mostly want relay behavior
- [Restricted-Limits Scenario](../../configuration/restricted-limits.md) if the platform is shared broadly

---

## Next steps

- **Back to:** [Deploy with Helm](../helm.md) — Helm overview and scenario selection.
- **Previous scenario:** [Standalone Durable Service](standalone-durable.md) — colocated but more serious.
- **Next scenario:** [HA Adapter Tier](ha-adapter-tier.md) — keep the separation, then add multi-replica resilience.
- **See also:** [Configuration](../../configuration.md) — runtime config scenarios.
