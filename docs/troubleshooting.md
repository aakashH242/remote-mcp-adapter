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

## Next steps

- **See also:** [Core Concepts](core-concepts.md) - sessions, handles, artifacts.
- **See also:** [Security](security.md) - exact auth and signed URL behavior.
- **See also:** [Health](health.md) - interpreting `/healthz`.
