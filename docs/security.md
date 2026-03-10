# Security

**What you'll learn here:** how to enable bearer token authentication, which endpoints it protects, how signed upload/download URLs work, and what clients must send.

---

## Auth is disabled by default

Out of the box the adapter accepts requests without credentials. This is intentional for local development. Before exposing the adapter to a network, enable auth under `core.auth`.

```yaml
core:
  auth:
    enabled: true
    header_name: "X-Mcp-Adapter-Auth-Token"
    token: "${MCP_ADAPTER_TOKEN}"
```

The `token` field supports `${ENV_VAR}` and `${ENV_VAR:-default}` interpolation. Never commit the token value directly to your config file.

---

## Header auth semantics

When auth is enabled, adapter token auth is an exact header-value match. If the header is missing or wrong, the adapter returns HTTP 403.

There is no role system, no JWT parsing, and no OAuth. It is one shared token per adapter instance.

A correctly authenticated MCP request looks like this:

```http
POST /mcp/playwright HTTP/1.1
Mcp-Session-Id: a3f8c2d1-7b4e-4f9a-85cc-0e3d1f6a9b12
X-Mcp-Adapter-Auth-Token: my-secret-token
Content-Type: application/json
```

---

## Public vs protected endpoints

When auth is enabled, these endpoints are always public:

- `/healthz`
- `/docs`
- `/redoc`
- `/openapi.json`
- `/.well-known/oauth-authorization-server`
- `/.well-known/openid-configuration`

Everything else is protected. In practice:

- `/mcp/<server_id>` requires the configured auth header.
- `/upload/<server_id>` uses signed upload credentials when enabled (below).
- `/artifacts/...` accepts signed download URLs when enabled; otherwise it requires adapter auth and matching session context.

---

## Signed upload URLs

When a client calls `<server_id>_get_upload_url(...)`, the adapter can return a signed, time-limited upload URL.

Signing uses:

- `core.auth.signing_secret` when set
- otherwise `core.auth.token`

URL lifetime is `core.auth.signed_upload_ttl_seconds` (default: `120`).

For signed uploads, clients send:

- `Mcp-Session-Id` header
- signed query params from `<server_id>_get_upload_url(...)`
- multipart `file` fields (and `sha256` fields if required)

The adapter auth header is not required on that upload POST when signed credentials are valid.

If signed credentials are missing, invalid, replayed, or expired, the upload is rejected with HTTP 403.

---

## Signed download URLs

When `core.allow_artifacts_download` is enabled, artifact tool results may include `meta.artifact.download_url`. When signing is active, this URL includes signed query params and can be fetched without the adapter auth header.

Signed download URL TTL is:

- `artifacts.ttl_seconds` when set
- otherwise `core.auth.signed_upload_ttl_seconds`

If signed download params are missing or invalid, the request falls back to normal auth/session checks.

---

## What clients must send

When auth is enabled:

1. Send `X-Mcp-Adapter-Auth-Token: <token>` (or your configured `header_name`) on MCP requests (`/mcp/<server_id>`).
2. For uploads, call `<server_id>_get_upload_url(...)`, then POST to the returned URL with `Mcp-Session-Id` and signed query params.
3. For artifact HTTP downloads without a valid signed URL, send adapter auth plus matching session context (`Mcp-Session-Id` header or `session_id` query param).

Example GitHub Copilot config:

```json
{
  "servers": {
    "playwright": {
      "url": "http://localhost:8932/mcp/playwright",
      "type": "http",
      "headers": {
        "X-Mcp-Adapter-Auth-Token": "${input:mcp-adapter-token}"
      }
    }
  },
  "inputs": [
    {
      "type": "promptString",
      "id": "mcp-adapter-token",
      "description": "Enter the authentication token for the MCP adapter",
      "password": true
    }
  ]
}
```

---

## Next steps

- **Previous topic:** [Private Demo Links Scenario](configuration/private-demo-links.md) - protected internal demo flow with human-clickable links.
- **Next:** [Telemetry](telemetry.md) - see what the adapter emits once it is running.
- **See also:** [Configuration](configuration.md) - practical config examples.
- **See also:** [Config Reference](configuration/config-reference.md) - full `core.auth` reference.
