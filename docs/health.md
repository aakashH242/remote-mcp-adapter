# Health & Diagnostics

**What you'll learn here:** how to read `/healthz`, what `200` vs `503` means, and how to interpret degraded states.

---

## Endpoint

```http
GET /healthz
```

`/healthz` is always public, even when `core.auth.enabled: true`.

Response status:

- `200`: overall healthy
- `503`: one or more components degraded

---

## Payload shape

Top-level fields include:

- `status`: `ok` or `degraded`
- `servers`: per-upstream health snapshots
- `persistence`: backend health + configured/effective backend info
- `adapter_wiring`: readiness of configured adapter wiring
- `startup` and `startup_reconciliation` when available
- `degraded_reason` when status is `degraded`

When upstream ping is disabled for a server (`core.upstream_ping.enabled: false`), that server entry reports:

- `status: "ok"`
- `detail: "upstream_ping_disabled"`

It does not include the normal `ping` object.

---

## `degraded_reason`

Common values:

- `upstream_unhealthy`
- `adapter_wiring_incomplete`
- persistence-policy reasons such as:
  - `persistence_unavailable_during_<phase>`
  - `fallback_memory_activated_during_<phase>`
  - `persistence_unavailable_via_<component>`

So `degraded_reason` is not limited to only three fixed strings.

---

## Example payload

```json
{
  "status": "degraded",
  "degraded_reason": "upstream_unhealthy",
  "servers": [
    {
      "server_id": "playwright",
      "mount_path": "/mcp/playwright",
      "upstream_url": "http://playwright:8931/mcp",
      "status": "degraded",
      "breaker": {
        "state": "open"
      },
      "ping": {
        "last_latency_ms": 5012.1,
        "last_error": "timeout"
      },
      "detail": "upstream_unhealthy"
    }
  ],
  "persistence": {
    "status": "ok",
    "configured_type": "disk",
    "effective_type": "disk",
    "fallback_active": false
  },
  "adapter_wiring": {
    "ready": true
  }
}
```

---

## Deployment use

Compose healthcheck:

```yaml
services:
  remote-mcp-adapter:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8932/healthz"]
      interval: 15s
      timeout: 5s
      retries: 3
```

Kubernetes probe:

```yaml
livenessProbe:
  httpGet:
    path: /healthz
    port: 8932
  initialDelaySeconds: 10
  periodSeconds: 15
```

---

## Next steps

- **Previous topic:** [Post-Install Verification](deployment/helm/post-install-verification.md) - confirm the Helm deployment is actually healthy before reading deeper diagnostics.
- **Next:** [Troubleshooting](troubleshooting.md) - common failures and fixes.
- **See also:** [Configuration](configuration.md) - upstream ping and startup settings.
- **See also:** [Security](security.md) - public vs protected routes.
