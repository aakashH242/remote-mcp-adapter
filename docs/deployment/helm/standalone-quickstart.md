# Helm Scenario: Standalone Quick Start

**What you'll learn here:** when the simplest single-release Kubernetes shape is enough, which values matter for a first Helm install, and how to get one adapter plus one colocated upstream running quickly.

---

## What this scenario is for

This is the easiest Kubernetes entry point for the chart.

Choose it when:

- you want one release to bring up the adapter and an upstream together
- you are fine with one pod owning both pieces
- you want the simplest shared-volume story
- you are evaluating the chart rather than building a full platform immediately

This is the closest Kubernetes equivalent to the local Compose path.

---

## Suggested values

Save this as `values-standalone-quickstart.yaml`:

If you want a ready-made file from the repository instead of rebuilding it by hand, start from [values-standalone-quickstart.yaml](../../examples/helm/values-standalone-quickstart.yaml).

```yaml
deploymentMode: standalone
replicaCount: 1

persistence:
  enabled: true
  size: 10Gi

ingress:
  enabled: false

config:
  config.yaml:
    core:
      public_base_url: null
      allow_artifacts_download: true
    storage:
      root: /data/shared
    state_persistence:
      type: disk
    servers:
      - id: playwright
        mount_path: /mcp/playwright
        upstream:
          transport: streamable_http
          url: http://localhost:8931/mcp
        adapters:
          - type: upload_consumer
            tools: [browser_file_upload]
            file_path_argument: paths
          - type: artifact_producer
            tools: [browser_take_screenshot, browser_pdf_save]
            output_path_argument: filename
            output_locator:
              mode: regex

upstreamServers:
  - name: playwrightMCP
    startCommand: ["node"]
    args:
      - cli.js
      - --headless
      - --browser
      - chromium
      - --port
      - "8931"
      - --host
      - 0.0.0.0
      - --output-dir
      - /data/shared
    image:
      repository: mcr.microsoft.com/playwright/mcp
      tag: v0.0.68
```

---

## Commands

```bash
helm repo add remote-mcp-adapter https://aakashh242.github.io/remote-mcp-adapter
helm repo update
helm upgrade --install remote-mcp-adapter remote-mcp-adapter/remote-mcp-adapter \
  --namespace remote-mcp-adapter \
  --create-namespace \
  -f values-standalone-quickstart.yaml
```

---

## What this gives you

- one Helm release
- one adapter pod with the upstream beside it
- the least complicated shared-storage setup the chart supports
- a clean first step before you decide whether Kubernetes is the right long-term home

---

## Pair it with

- [Local Dev Scenario](../../configuration/local-dev.md) if this is still mostly evaluation
- [Single-Node Durable Scenario](../../configuration/single-node-durable.md) if you want real limits and auth next

`core.public_base_url: null` is fine here only while clients reach the service through `kubectl port-forward`, a direct cluster-local address, or another simple dev-only path. Once a real hostname, ingress, or load balancer is involved, set `core.public_base_url` to that external address so upload and download links are usable.

---

## Next steps

- **Back to:** [Deploy with Helm](../helm.md) — Helm overview and scenario selection.
- **Previous topic:** [Choose a Shape](choose-a-shape.md) — decide whether you need a base shape or an overlay story first.
- **Next scenario:** [Standalone Durable Service](standalone-durable.md) — keep the same topology, but make it production-friendlier.
- **See also:** [Configuration](../../configuration.md) — runtime behavior and config scenarios.
