# Helm Scenario: Standalone Durable Service

**What you'll learn here:** how to keep the simple standalone pod shape while adding the pieces that make it feel like a real service: persistence, auth, ingress, and stronger defaults.

---

## What this scenario is for

This is the "one serious pod" shape.

Choose it when:

- you still want adapter and upstream together
- you want persistent storage, auth, and ingress
- you are running one real service rather than a demo-only install
- you do not need separate upstream lifecycles yet

Redis is not required for this shape. The example below keeps state on disk because there is still only one adapter pod.

---

## Suggested values

Save this as `values-standalone-durable.yaml`:

If you want a ready-made file from the repository instead of rebuilding it by hand, start from [values-standalone-durable.yaml](../../examples/helm/values-standalone-durable.yaml).

```yaml
deploymentMode: standalone
replicaCount: 1

persistence:
  enabled: true
  size: 20Gi

environment:
  envFromSecret:
    name: remote-mcp-adapter-env
    keys: [MCP_ADAPTER_TOKEN, MCP_ADAPTER_SIGNING_SECRET]

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: mcp-adapter.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: remote-mcp-adapter-tls
      hosts:
        - mcp-adapter.example.com

config:
  config.yaml:
    core:
      public_base_url: https://mcp-adapter.example.com
      allow_artifacts_download: true
      auth:
        enabled: true
        token: ${MCP_ADAPTER_TOKEN}
        signing_secret: ${MCP_ADAPTER_SIGNING_SECRET}
    storage:
      root: /data/shared
      max_size: 10Gi
    state_persistence:
      type: disk
    sessions:
      max_active: 100
      idle_ttl_seconds: 1800
      max_total_session_size: 500Mi
    artifacts:
      max_per_session: 50
```

---

## Commands

```bash
helm repo add remote-mcp-adapter https://aakashh242.github.io/remote-mcp-adapter
helm repo update
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-standalone-durable.yaml
```

---

## What this gives you

- the simplest cluster topology that still feels durable
- one ingress hostname and one persistent volume to reason about
- secret-backed env var injection for auth material
- a clean stepping stone before you separate upstream lifecycle from adapter lifecycle

---

## Pair it with

- [Single-Node Durable Scenario](../../configuration/single-node-durable.md)
- [High-Security Scenario](../../configuration/high-security.md) if the service is shared

---

## Next steps

- **Back to:** [Deploy with Helm](../helm.md) - Helm overview and scenario selection.
- **Previous scenario:** [Standalone Quick Start](standalone-quickstart.md) - simplest colocated setup.
- **Next scenario:** [Distributed Shared Platform](distributed-shared-platform.md) - separate the adapter from upstream service lifecycle.
- **See also:** [Security](../../security/index.md) - auth and signing behavior.




