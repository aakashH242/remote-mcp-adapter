# Helm Guide: Layered Values-File Pairs

**What you'll learn here:** how to combine a base Helm shape with one or more overlays, which file pairs make sense in practice, and what the final `helm upgrade --install` command usually looks like.

---

## Why this page exists

Several pages in this Helm section are overlays on purpose.

That is a good thing for operators because it lets you keep concerns separate:

- base topology in one file
- browser-facing hostname and link behavior in another
- observability-heavy settings in another

The trick is making that layering obvious.

---

## Pair 1: HA adapter tier + browser-facing ingress

Use this when:

- the adapter has multiple replicas
- users or browser clients will open upload or artifact links directly
- ingress hostname and signed link behavior matter

Files:

- `values-ha-adapter.yaml`
- `values-browser-facing.yaml`

Command:

```bash
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-ha-adapter.yaml \
  -f values-browser-facing.yaml
```

Reading order:

1. [HA Adapter Tier](ha-adapter-tier.md)
2. [Browser-Facing Ingress](browser-facing-ingress.md)

---

## Pair 2: HA adapter tier + observability-first production

Use this when:

- the adapter has multiple replicas
- Redis-backed shared state is already part of the design
- telemetry and operations are first-class concerns

Files:

- `values-ha-adapter.yaml`
- `values-observability.yaml`

Command:

```bash
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-ha-adapter.yaml \
  -f values-observability.yaml
```

Reading order:

1. [HA Adapter Tier](ha-adapter-tier.md)
2. [Observability-First Production](observability-first-production.md)

---

## Pair 3: HA adapter tier + browser-facing ingress + observability

Use this when:

- the adapter is a real shared service
- humans will click links
- on-call ownership and telemetry are both non-negotiable

Files:

- `values-ha-adapter.yaml`
- `values-browser-facing.yaml`
- `values-observability.yaml`

Command:

```bash
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-ha-adapter.yaml \
  -f values-browser-facing.yaml \
  -f values-observability.yaml
```

In this order, the later files keep the public-hostname and telemetry settings clearly separate from the base topology.

---

## Pair 4: Standalone durable + browser-facing ingress

Use this when:

- one pod is still enough
- the service is no longer just a private cluster test
- users will click upload or artifact links through a real hostname

Files:

- `values-standalone-durable.yaml`
- `values-browser-facing.yaml`

Command:

```bash
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-standalone-durable.yaml \
  -f values-browser-facing.yaml
```

This is often the cleanest path when Kubernetes is real, but the adapter is not yet an HA tier.

---

## Pair 5: Distributed shared platform + observability

Use this when:

- upstream services already live elsewhere
- the adapter is a platform boundary, not a sidecar pod
- observability matters more than browser-facing links

Files:

- `values-distributed.yaml`
- `values-observability.yaml`

Command:

```bash
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-distributed.yaml \
  -f values-observability.yaml
```

---

## What should stay in the base file?

Keep these in the base file whenever possible:

- `deploymentMode`
- `replicaCount`
- persistence and shared storage choices
- `config.config.yaml.servers`
- state backend choices

Keep these in overlays when you want cleaner separation:

- ingress hostname and TLS
- `core.public_base_url`
- signing and browser-facing settings
- telemetry exporter settings

---

## Next steps

- **Previous topic:** [Choose a Shape](choose-a-shape.md) - decide which base story fits first.
- **See also:** [Example values files](example-values-files.md) - the checked-in YAML files that match these examples.
- **Next:** [Post-Install Verification](post-install-verification.md) - check that the deployment actually behaves the way the values imply.
- **See also:** [Deploy with Helm](../helm.md) - Helm overview and scenario index.
