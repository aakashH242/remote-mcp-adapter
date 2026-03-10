# Troubleshooting

**What you'll learn here:** common failures, likely causes, and practical fixes.

---

## Local path upload fails

**Symptom:** Tool call like `browser_file_upload(paths=["/Users/me/report.pdf"])` fails upstream with file-not-found.

**Cause:** The path exists on the client machine, not on the upstream server machine.

**Fix:** Use staged upload flow:

1. Call `<server_id>_get_upload_url(...)`.
2. POST file(s) to returned URL.
3. Pass returned `upload://` handle(s) to upload tools.

See [Core Concepts](core-concepts.md).

---

## I do not see `<server_id>_get_upload_url`

**Symptom:** No helper tool appears in tool list.

**Cause:** Helper tool is registered only when:

- that server has at least one `upload_consumer` adapter
- `uploads.enabled` is `true`

**Fix:** Verify both config conditions and confirm client is connected to adapter mount path (not directly to upstream).

---

## Artifact not found / expired

**Symptom:** `resources/read` on an `artifact://` URI returns not found.

**Cause:** Artifact exceeded `artifacts.ttl_seconds` and was cleaned up.

**Fix:** Read sooner, or increase TTL:

```yaml
artifacts:
  ttl_seconds: 3600
```

If needed, enable HTTP artifact downloads (`core.allow_artifacts_download`) and fetch before expiry.

---

## Circuit breaker is open

**Symptom:** Calls fail fast; `/healthz` shows breaker state `open`.

**Cause:** Upstream failed health pings enough times to open breaker.

**Fix:** Recover upstream first. Breaker probes automatically after `open_cooldown_seconds` and closes on successful probes.

---

## Auth rejected (HTTP 403)

**Symptom:** Requests return `403` with auth error.

**Cause:** Missing or incorrect adapter auth header/token for protected routes.

**Fix:** Send configured `core.auth.header_name` with configured token value in MCP client settings.

---

## Upload rejected: missing/invalid signed credential (HTTP 403)

**Symptom:** Upload POST to signed upload URL returns `403`.

**Cause:** Signed upload params are missing, invalid, replayed, or expired.

**Fix:**

- Call `<server_id>_get_upload_url(...)` again and use the new URL immediately.
- Ensure `Mcp-Session-Id` matches the same session used to issue the URL.
- Increase `core.auth.signed_upload_ttl_seconds` for slower environments.

```yaml
core:
  auth:
    signed_upload_ttl_seconds: 600
```

---

## Tool list is stale

**Symptom:** Missing or outdated tools/resources after upstream changes.

**Cause:** Metadata cache TTL (`core.upstream_metadata_cache_ttl_seconds`) has not expired.

**Fix:** Lower TTL in development:

```yaml
core:
  upstream_metadata_cache_ttl_seconds: 10
```

---

## Adapter starts degraded

**Symptom:** `/healthz` shows `status: degraded` at startup.

**Cause:** Upstream did not become ready within `core.max_start_wait_seconds`.

**Fix:** Increase startup wait, or improve upstream startup ordering/readiness checks.

---

## Multi-replica adapter behaves inconsistently

**Symptom:** sessions disappear after a pod restart, requests behave differently across replicas, or the service is unstable once traffic starts spreading across multiple adapter pods.

**Cause:** Multiple adapter replicas are running without a shared state backend. Node-local disk is fine for one adapter pod, but it is not enough once session metadata must be shared across replicas.

**Fix:**

- if you only need one adapter pod, stay on disk-backed state
- if you want multiple adapter replicas, configure Redis for `state_persistence`
- make sure every adapter replica can also reach the same shared file storage when uploads or artifacts are involved

```yaml
state_persistence:
  type: redis
  redis:
    host: redis.default.svc.cluster.local
    port: 6379
```

---

## Redis-backed deployment will not come up

**Symptom:** pods restart, `/healthz` reports persistence problems, or startup fails after switching to Redis-backed state.

**Cause:** Redis is configured as mandatory state, but the adapter cannot connect to it because the host, port, password, network policy, or secret wiring is wrong.

**Fix:**

- confirm the Redis service name resolves from the adapter pod
- verify the configured password and injected secret values
- check whether network policies or namespace boundaries block access
- if Redis is intentionally unavailable, do not use a Redis-required HA shape yet

---

## Next steps

- **Previous topic:** [Health](health.md) - understand degraded states and probe behavior.
- **Next:** [API Reference](api/index.md) - public entry points first, then internals.
- **See also:** [Post-Install Verification](deployment/helm/post-install-verification.md) - the quickest way to catch deployment wiring problems before users do.
- **See also:** [Core Concepts](core-concepts.md) - sessions, handles, artifacts.
- **See also:** [Security](security.md) - exact auth and signed URL behavior.
