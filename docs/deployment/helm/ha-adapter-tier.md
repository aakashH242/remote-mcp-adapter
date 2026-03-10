# Helm Scenario: HA Adapter Tier

**What you'll learn here:** what the chart needs for a genuinely resilient adapter tier, how Redis and shared storage fit into that story, and which values matter when you stop treating the adapter as a single fragile pod.

---

## What this scenario is for

This is the real high-availability story for the adapter itself.

Choose it when:

- you want multiple adapter replicas
- session metadata must survive pod replacement
- you want rolling updates without treating the adapter as a single fragile pod
- you have shared storage and Redis available

Redis is a real prerequisite for this scenario. Once multiple adapter replicas are serving traffic, they need a shared state backend for session metadata instead of node-local disk.

The shared volume also needs to be a real multi-writer option for your cluster, not just a disk that happens to work for one pod. If your upstream tools rely on `upload_consumer` or `artifact_producer` adapters, those external upstream services must still mount the same shared filesystem at the same effective path. Redis solves shared metadata; it does not make the files themselves magically reachable.

---

## Suggested values

Save this as `values-ha-adapter.yaml`:

If you want a ready-made file from the repository instead of rebuilding it by hand, start from [values-ha-adapter.yaml](../../examples/helm/values-ha-adapter.yaml).

```yaml
deploymentMode: distributed
replicaCount: 3

autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 6
  targetCPUUtilizationPercentage: 80

strictAntiAffinity: true

podDisruptionBudget:
  maxUnavailable: 1

persistence:
  enabled: true
  existingClaim: remote-mcp-adapter-rwx

environment:
  envFromSecret:
    name: remote-mcp-adapter-env
    keys: [MCP_ADAPTER_TOKEN, REDIS_PASSWORD]

config:
  config.yaml:
    core:
      public_base_url: https://mcp-gateway.example.com
      auth:
        enabled: true
        token: ${MCP_ADAPTER_TOKEN}
    storage:
      root: /data/shared
      lock_mode: redis
    state_persistence:
      type: redis
      unavailable_policy: exit
      redis:
        host: redis.default.svc.cluster.local
        port: 6379
        password: ${REDIS_PASSWORD}
    sessions:
      allow_revival: true
      idle_ttl_seconds: 1800
```

---

## Commands

```bash
helm repo add remote-mcp-adapter https://aakashh242.github.io/remote-mcp-adapter
helm repo update
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-ha-adapter.yaml
```

---

## What this gives you

- multiple adapter replicas
- safer rolling updates and disruption handling
- Redis-backed session metadata
- a cluster shape that actually matches the phrase “high availability”

---

## Pair it with

- [Distributed Production Scenario](../../configuration/distributed-production.md)
- [Restricted-Limits Scenario](../../configuration/restricted-limits.md) for shared usage ceilings

---

## Next steps

- **Back to:** [Deploy with Helm](../helm.md) — Helm overview and scenario selection.
- **Previous scenario:** [Distributed Shared Platform](distributed-shared-platform.md) — separate adapter and upstream lifecycle first.
- **Next scenario:** [Browser-Facing Ingress](browser-facing-ingress.md) — configure ingress for human-clickable links and browser-facing flows.
- **See also:** [Health](../../health.md) — degraded states become more important once replicas exist.
