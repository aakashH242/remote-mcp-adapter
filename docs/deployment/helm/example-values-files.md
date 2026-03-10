# Helm Guide: Example Values Files

**What you'll learn here:** where the checked-in Helm example files live, how to use them without rebuilding YAML by hand, and which files are complete base shapes versus overlays.

This page is a **reference stop**, not part of the strict step-by-step chain. Use it when you want direct copy-paste starter files from the repo after you already understand which base shape and overlays you want.

---

## Why these files exist

The scenario pages already show realistic YAML blocks, but sometimes you just want a real file you can start from.

That is what these checked-in examples are for.

They are not magical production defaults. They are just versioned sample values files that match the Helm scenarios in this section.

---

## Where the files live

All checked-in Helm example files live under:

- `docs/examples/helm/`

The current set is:

- [values-standalone-quickstart.yaml](../../examples/helm/values-standalone-quickstart.yaml)
- [values-standalone-durable.yaml](../../examples/helm/values-standalone-durable.yaml)
- [values-distributed.yaml](../../examples/helm/values-distributed.yaml)
- [values-ha-adapter.yaml](../../examples/helm/values-ha-adapter.yaml)
- [values-browser-facing.yaml](../../examples/helm/values-browser-facing.yaml)
- [values-observability.yaml](../../examples/helm/values-observability.yaml)

---

## Base shapes versus overlays

Some of these files are complete starting points. Others are meant to be layered on top.

### Base shapes

Use these directly:

- [values-standalone-quickstart.yaml](../../examples/helm/values-standalone-quickstart.yaml)
- [values-standalone-durable.yaml](../../examples/helm/values-standalone-durable.yaml)
- [values-distributed.yaml](../../examples/helm/values-distributed.yaml)
- [values-ha-adapter.yaml](../../examples/helm/values-ha-adapter.yaml)

### Overlays

Layer these on top of a base shape with multiple `-f` flags:

- [values-browser-facing.yaml](../../examples/helm/values-browser-facing.yaml)
- [values-observability.yaml](../../examples/helm/values-observability.yaml)

---

## Example commands

Base shape only:

```bash
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-ha-adapter.yaml
```

Base shape plus overlay:

```bash
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-ha-adapter.yaml \
  -f values-browser-facing.yaml
```

Base shape plus two overlays:

```bash
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-ha-adapter.yaml \
  -f values-browser-facing.yaml \
  -f values-observability.yaml
```

---

## A practical note

These files are meant to save time, not replace judgment.

You will still need to edit the obvious environment-specific pieces:

- hostnames
- secret names
- Redis addresses
- PVC names
- upstream URLs

If you use them that way, they are useful. If you treat them as universal defaults, they will disappoint you.

---

## Next steps

- **Previous topic:** [Choose a Shape](choose-a-shape.md) - decide which base story fits first.
- **See also:** [Layered values-file pairs](layered-values-file-pairs.md) - combine base shapes and overlays cleanly.
- **See also:** [Post-Install Verification](post-install-verification.md) - verify the deployment after install or upgrade.
- **Back to:** [Deploy with Helm](../helm.md) - Helm overview and scenario index.
