# Deploy with Helm

**What you'll learn here:** which Kubernetes deployment shapes the chart supports, which one fits your environment, what values to set for each scenario, and the exact `helm repo add`, `helm repo update`, and `helm upgrade --install` flow to use.

---

## Why use this path

Helm is the right path when you want Kubernetes to own the boring parts for you: rollout mechanics, pod restarts, ingress wiring, persistent volumes, and predictable upgrades.

It is a good fit when you want:

- repeatable cluster installs and upgrades
- ingress and service configuration
- persistent or shared storage
- pod-level security and runtime controls
- either colocated upstream sidecars or separately managed upstream services

For end users, the thing to share is the **published Helm repository URL**:

- `https://aakashh242.github.io/remote-mcp-adapter`

That is the GitHub Pages root where Helm reads `index.yaml`. It is the correct URL for `helm repo add`. The source chart folder is useful for contributors, but it is not the main install entry point.

---

## Prerequisites

Before you install the chart, make sure you have:

- a Kubernetes cluster
- Helm 3
- a namespace plan
- a values-file strategy
- storage planned correctly for your chosen shape

Redis is **not** a universal prerequisite for every Helm scenario. The durable standalone and simple distributed examples in this guide use disk-backed state. Redis becomes the real prerequisite once you want multiple adapter replicas sharing session metadata, as in the HA adapter tier.

If users will call `<server_id>_get_upload_url(...)` or open HTTP artifact download links through ingress, plan `core.public_base_url` up front. In Kubernetes that usually means the public hostname exposed by your ingress or load balancer. Without it, helper-generated URLs can point at an internal service or pod address instead of the address your client actually reaches.

Also note the supported Kubernetes range from [charts/remote-mcp-adapter/Chart.yaml](https://github.com/aakashh242/remote-mcp-adapter/blob/main/charts/remote-mcp-adapter/Chart.yaml):

- Kubernetes `>= 1.29.0`
- Kubernetes `< 1.36.0`

---

## How to think about Helm scenarios

The chart has two core topology knobs:

- `deploymentMode: standalone`
- `deploymentMode: distributed`

But that is only the start. In practice, most teams choose a **Kubernetes operating shape**, not just a raw mode flag.

The scenarios below are the ones that tend to matter in the real world:

1. standalone quick start
2. standalone durable service
3. distributed shared platform
4. HA adapter tier
5. browser-facing ingress
6. observability-first production

These are deployment shapes, not replacements for the runtime config scenarios in [Configuration](../configuration.md). The goal here is to show the Helm values you would actually apply in Kubernetes.

There is one important distinction before you pick a page:

- **Base shapes** are complete starting points you can install directly.
- **Overlays** are additive values files you usually layer on top of a base shape with multiple `-f` flags.

In this section:

- **Base shapes:** Standalone Quick Start, Standalone Durable Service, Distributed Shared Platform, HA Adapter Tier
- **Overlays:** Browser-Facing Ingress, Observability-First Production

---

## Helm deployment scenarios

Each deployment shape now has its own dedicated reference page, so you can jump straight to the one that matches your cluster without wading through every other pattern first.

- [Standalone Quick Start](helm/standalone-quickstart.md) — the simplest one-release Kubernetes entry point.
- [Standalone Durable Service](helm/standalone-durable.md) — standalone, but with ingress, persistence, and stronger defaults.
- [Distributed Shared Platform](helm/distributed-shared-platform.md) — separate adapter lifecycle from upstream service lifecycle.
- [HA Adapter Tier](helm/ha-adapter-tier.md) — multiple adapter replicas with Redis-backed state and shared storage.
- [Browser-Facing Ingress](helm/browser-facing-ingress.md) — overlay values for human-clickable links and browser-facing flows.
- [Observability-First Production](helm/observability-first-production.md) — overlay values for telemetry-heavy operated environments.

The pattern is the same on each page:

- what the shape is for
- a realistic `values.yaml` example
- the commands to run with `helm repo add`, `helm repo update`, and `helm upgrade --install`
- links to the matching runtime-config guidance when you need more than chart values

If you want the cleanest reading order, use this:

1. [Choose a Shape](helm/choose-a-shape.md)
2. one base shape page
3. optional overlay pages
4. optional [Example Values Files](helm/example-values-files.md) if you want checked-in copy-paste starters
5. [Layered values-file pairs](helm/layered-values-file-pairs.md)
6. [Post-Install Verification](helm/post-install-verification.md)

If you are unsure where to start, use this shortcut:

- choose [Standalone Quick Start](helm/standalone-quickstart.md) if you want the easiest cluster trial
- choose [Standalone Durable Service](helm/standalone-durable.md) if one pod is still enough, but the service is now real
- choose [Distributed Shared Platform](helm/distributed-shared-platform.md) if upstreams already live elsewhere
- choose [HA Adapter Tier](helm/ha-adapter-tier.md) if you need real adapter resilience
- choose [Browser-Facing Ingress](helm/browser-facing-ingress.md) if humans will click returned links
- choose [Observability-First Production](helm/observability-first-production.md) if telemetry and operations are first-class concerns

---

## When a local chart checkout is useful

Installing from [charts/remote-mcp-adapter](https://github.com/aakashh242/remote-mcp-adapter/tree/main/charts/remote-mcp-adapter) is mainly useful when:

- you are testing unreleased chart changes
- you are contributing to the chart itself
- you want to inspect the chart source before publishing

That is a contributor path, not the primary end-user path.

---

## Common Helm mistakes

!!! warning "Shared storage planned incorrectly"
    In both standalone and distributed shapes, the adapter and any file-producing or file-consuming upstream must agree on the same shared storage path.

!!! warning "Choosing distributed mode without solving file access"
    Shared metadata alone is not enough. Upload and artifact file content still has to live on storage that the relevant services can reach.

!!! warning "Assuming Redis is either always required or never required"
    Redis is scenario-specific. Single-replica shapes can work with disk-backed state, but multi-replica adapter deployments need a shared state backend such as Redis.

!!! warning "Treating chart values as separate from adapter config"
    The chart renders the adapter config into a ConfigMap. The Kubernetes values and the adapter's runtime config are connected, not independent.

!!! warning "Forgetting `public_base_url` once ingress is doing the real routing"
    If clients rely on helper-generated upload URLs or HTTP artifact download links, `core.public_base_url` needs to match the external hostname they actually use.

!!! warning "Forgetting secret-backed environment variables"
    If your config uses `${ENV_VAR}` interpolation, the adapter container needs those variables injected. The chart now supports both explicit `environment.env` values and secret-backed `environment.envFromSecret` loading.

!!! warning "Using local chart installs as the default docs path"
    For end users, the published Helm repository is the simpler and more stable story.

---

## What to do after install

After any scenario install:

- check pod readiness
- check the service or ingress address
- hit `/healthz`
- confirm the configured upstream server shows as healthy

For example:

```bash
kubectl get pods -n remote-mcp-adapter
kubectl get svc -n remote-mcp-adapter
kubectl logs -n remote-mcp-adapter deploy/remote-mcp-adapter
```

---

## Next steps

- **Previous topic:** [Deploy with Docker Compose](compose.md) — the simpler single-machine path.
- **Next:** [Choose a Shape](helm/choose-a-shape.md) — decide which Helm story actually fits before you install.
- **See also:** [Deployment](../deployment.md) — deployment overview and path selection.
- **See also:** [Configuration](../configuration.md) — choose the runtime behavior that fits the cluster shape you picked.

