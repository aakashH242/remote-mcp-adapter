# Helm Guide: Choose a Shape

**What you'll learn here:** how to pick the right Helm deployment shape for your environment, which questions matter first, and when a page in this section is a base install versus an overlay.

---

## Start with these two questions

Before you look at any values file, answer these first:

1. Do you want the adapter and upstream in the same pod, or managed separately?
2. Are you choosing a full deployment shape, or just layering extra concerns like ingress or observability on top?

That split matters more than most of the individual settings.

---

## Base shape or overlay?

In this Helm section, pages fall into two groups.

### Base shapes

These are complete starting points. You can install them directly.

- [Standalone Quick Start](standalone-quickstart.md)
- [Standalone Durable Service](standalone-durable.md)
- [Distributed Shared Platform](distributed-shared-platform.md)
- [HA Adapter Tier](ha-adapter-tier.md)

### Overlays

These are additive values files. They are usually layered on top of a base shape with multiple `-f` flags.

- [Browser-Facing Ingress](browser-facing-ingress.md)
- [Observability-First Production](observability-first-production.md)

If a page changes public hostname behavior, ingress behavior, or telemetry behavior without defining the full server and storage story, treat it as an overlay.

---

## Quick decision matrix

| If your real need is... | Start here | Then layer this if needed |
| --- | --- | --- |
| Fastest possible cluster trial | [Standalone Quick Start](standalone-quickstart.md) | Nothing yet |
| One real service, still one pod | [Standalone Durable Service](standalone-durable.md) | [Browser-Facing Ingress](browser-facing-ingress.md) or [Observability-First Production](observability-first-production.md) |
| Adapter separate from upstream lifecycle | [Distributed Shared Platform](distributed-shared-platform.md) | [Browser-Facing Ingress](browser-facing-ingress.md) or [Observability-First Production](observability-first-production.md) |
| Multi-replica adapter tier | [HA Adapter Tier](ha-adapter-tier.md) | [Browser-Facing Ingress](browser-facing-ingress.md), [Observability-First Production](observability-first-production.md), or both |
| Humans will click upload or artifact links | Pick the right base shape first | Then add [Browser-Facing Ingress](browser-facing-ingress.md) |
| On-call ownership and heavy telemetry | Pick the right base shape first | Then add [Observability-First Production](observability-first-production.md) |

---

## A simpler rule of thumb

If you are unsure, use this sequence:

- start with [Standalone Quick Start](standalone-quickstart.md) for a cluster trial
- move to [Standalone Durable Service](standalone-durable.md) if one pod is still enough but the service is becoming real
- move to [Distributed Shared Platform](distributed-shared-platform.md) when upstream services should have their own lifecycle
- move to [HA Adapter Tier](ha-adapter-tier.md) when the adapter itself must stop being a single fragile pod

Then decide whether you also need:

- [Browser-Facing Ingress](browser-facing-ingress.md)
- [Observability-First Production](observability-first-production.md)

---

## Two mistakes to avoid

!!! warning "Treating overlays as complete installs"
	That usually leaves you with a values file that looks polished but still depends on chart defaults or missing server definitions.

!!! warning "Treating Redis as a file-sharing solution"
	Redis shares state, not files. If uploads or artifacts are involved, the adapter and the upstreams still need a shared filesystem path they can both reach.

---

## Next steps

- **Back to:** [Deploy with Helm](../helm.md) - Helm overview and scenario index.
- **Next:** [Example values files](example-values-files.md) - checked-in copy-paste starters if you want real files instead of rebuilding YAML by hand.
- **See also:** [Standalone Quick Start](standalone-quickstart.md) - the lightest base shape.
- **See also:** [Layered values-file pairs](layered-values-file-pairs.md) - practical base-plus-overlay combinations once you have picked a base direction.
