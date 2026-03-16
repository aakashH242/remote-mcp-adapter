# Helm Scenario: Browser-Facing Ingress

**What you'll learn here:** how to shape a Kubernetes deployment for browser-visible links, why ingress and `public_base_url` matter together, and which values make signed uploads and artifact downloads behave cleanly for humans.

---

## What this scenario is for

This page is best treated as an overlay, not as a full deployment shape by itself.

Use it when you already have a base Helm shape chosen and now need the public-hostname, ingress, and signed-link pieces to behave properly for humans or browser apps.

Choose it when:

- the adapter sits behind ingress with a real hostname
- users or browser-based clients will click returned links
- signed uploads and artifact downloads matter
- `public_base_url` must be correct from the start

Good base shapes to layer this onto:

- [Standalone Durable Service](standalone-durable.md)
- [HA Adapter Tier](ha-adapter-tier.md)

---

## Suggested values

Save this as `values-browser-facing.yaml`:

If you want a ready-made overlay file from the repository, start from [values-browser-facing.yaml](../../examples/helm/values-browser-facing.yaml).

```yaml
deploymentMode: distributed
replicaCount: 2

ingress:
  enabled: true
  className: nginx
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "50m"
  hosts:
    - host: demo.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: demo-example-com-tls
      hosts:
        - demo.example.com

environment:
  envFromSecret:
    name: remote-mcp-adapter-env
    keys: [MCP_ADAPTER_TOKEN, MCP_ADAPTER_SIGNING_SECRET]

config:
  config.yaml:
    core:
      public_base_url: https://demo.example.com
      allow_artifacts_download: true
      auth:
        enabled: true
        token: ${MCP_ADAPTER_TOKEN}
        signing_secret: ${MCP_ADAPTER_SIGNING_SECRET}
      cors:
        enabled: true
        allowed_origins: [https://demo.example.com]
    uploads:
      require_sha256: true
      ttl_seconds: 300
    artifacts:
      ttl_seconds: 1800
```

This values file assumes the base shape already defines the rest of the deployment story, especially:

- `config.config.yaml.servers`
- storage and persistence choices
- replica strategy that matches your topology

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
  -f values-browser-facing.yaml
```

If you are still on a single durable pod, swap `values-ha-adapter.yaml` for `values-standalone-durable.yaml`.

---

## What this gives you

- ingress with a real hostname
- artifact download links that resolve correctly
- secret-backed auth and signing values
- a much better browser-facing experience than relying on cluster-internal addresses
- a clean way to separate public-hostname concerns from the rest of your deployment shape

---

## Pair it with

- [Public Demo Downloads Scenario](../../configuration/public-demo-downloads.md)
- [Private Demo Links Scenario](../../configuration/private-demo-links.md)

---

## Next steps

- **Back to:** [Deploy with Helm](../helm.md) â€” Helm overview and scenario selection.
- **Previous scenario:** [HA Adapter Tier](ha-adapter-tier.md) â€” resilience and scaling first.
- **Next scenario:** [Observability-First Production](observability-first-production.md) â€” add telemetry-forward deployment values.
- **See also:** [Security](../../security/index.md) â€” signed upload and download behavior.



