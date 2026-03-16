# Troubleshooting

Common failures, why they happen, and how to actually fix them.

---

## Local path upload fails

**Symptom:** Tool call like `browser_file_upload(paths=["/Users/me/report.pdf"])` fails upstream with file-not-found.

**What's going on:** That path exists on your machine, not on the upstream server. Sending it directly has never worked in a remote setup — it just usually fails quietly in a confusing way.

**Fix:** Use the staged upload flow:

1. Call `<server_id>_get_upload_url(...)`.
2. POST your file(s) to the returned URL.
3. Pass the returned `upload://` handle(s) to the tool instead of the local path.

See [Core Concepts](core-concepts.md) for a fuller explanation of how this works.

---

## I do not see `<server_id>_get_upload_url`

**Symptom:** The helper tool is nowhere in the tool list.

**What's going on:** The upload helper only registers when two things are both true:

- that server has at least one `upload_consumer` adapter configured
- `uploads.enabled` is `true`

If either is missing, the helper is silently absent. This is one of the more confusing first-run experiences, especially if you expect the tool to just appear.

**Fix:** Check both conditions in your config, and confirm your agent is connecting to the adapter's `mount_path`, not directly to the upstream server.

---

## Artifact not found / expired

**Symptom:** `resources/read` on an `artifact://` URI returns not found.

**What's going on:** The artifact hit its TTL and the cleanup job swept it. Artifacts are not permanent — they expire.

**Fix:** Read artifacts sooner after they are produced, or extend the TTL:

```yaml
artifacts:
  ttl_seconds: 3600
```

If you need to share or download artifacts before they expire, enable HTTP artifact downloads (`core.allow_artifacts_download`) as a backup path.

---

## Circuit breaker is open

**Symptom:** Calls fail fast; `/healthz` shows `"state": "open"` in the breaker block.

**What's going on:** The upstream failed health pings enough times that the breaker tripped. The adapter is now rejecting calls to that server without even trying to reach it — by design.

**Fix:** Get the upstream healthy first. The breaker probes automatically after `open_cooldown_seconds` and closes itself once upstream starts responding again. There is nothing to manually reset.

---

## Auth rejected (HTTP 403)

**Symptom:** Requests return `403` with an auth error.

**What's going on:** The adapter auth header is missing, wrong, or using the wrong header name.

**Fix:** Send the header configured in `core.auth.header_name` with the correct token value. Check your MCP client config — the header needs to be present on every request, not just the first one.

---

## Upload rejected: missing/invalid signed credential (HTTP 403)

**Symptom:** Upload POST to a signed upload URL returns `403`.

**What's going on:** Signed upload credentials are one-time, session-scoped, and short-lived. If any of those conditions break — expired, wrong session, already used — the server rejects the upload.

**Fix:**

- Call `<server_id>_get_upload_url(...)` again and use the fresh URL immediately.
- Confirm the `Mcp-Session-Id` you are using for the upload matches the one used when you requested the URL.
- If you are on a slow network or doing large uploads, bump the TTL:

```yaml
core:
  auth:
    signed_upload_ttl_seconds: 600
```

---

## Tool list is stale

**Symptom:** Tools or resources are missing or out of date after you changed something upstream.

**What's going on:** The adapter caches upstream tool lists. After an upstream redeploy or tool change, clients keep seeing the cached version until the TTL expires. Default TTL is 5 minutes.

**Fix:** Lower TTL while iterating:

```yaml
core:
  upstream_metadata_cache_ttl_seconds: 10
```

Bump it back up before you ship to production.

---

## Adapter starts degraded

**Symptom:** `/healthz` shows `"status": "degraded"` right after startup.

**What's going on:** The upstream did not become ready within `core.max_start_wait_seconds`. The adapter started anyway but is reporting it could not confirm the upstream was healthy.

**Fix:** Check `docker compose logs <upstream>` or the upstream pod logs first — the upstream container itself may be the slow one. If startup ordering is unavoidably slow, raise the startup wait:

```yaml
core:
  max_start_wait_seconds: 60
```

---

## Multi-replica adapter behaves inconsistently

**Symptom:** Sessions disappear after a pod restart, requests behave differently across replicas, or the service is unstable once traffic spreads across multiple adapter pods.

**What's going on:** Multiple replicas are running without shared state. Each pod has its own session database — so a client that reconnects to a different replica starts fresh. This is the fundamental HA gap.

**Fix:**

- If one adapter pod is enough, this problem does not apply — stay on disk-backed state.
- If you need multiple replicas, move to Redis for `state_persistence`:

```yaml
state_persistence:
  type: redis
  redis:
    host: redis.default.svc.cluster.local
    port: 6379
```

Also confirm every replica can reach the same shared file storage — Redis handles session metadata, but upload and artifact files still live on disk and all replicas need the same disk.

---

## Redis-backed deployment will not come up

**Symptom:** Pods restart, `/healthz` reports persistence problems, or startup fails after switching to Redis.

**What's going on:** Redis is configured but the adapter cannot connect — wrong host, wrong port, wrong password, or a network policy is blocking the path.

**Fix:**

- Confirm the Redis service name resolves from inside the adapter pod (`kubectl exec` and try a `redis-cli ping`)
- Check the configured password and how it is injected (env var vs secret)
- Check whether namespace-level network policies block cross-service traffic
- If Redis is intentionally not available yet, do not configure a Redis-required setup until it is

---

## Next steps

If you are still stuck, the most useful next reads are:

- [Health](health.md) — understand what the health payload is telling you before you dig further
- [Post-Install Verification](deployment/helm/post-install-verification.md) — fastest way to catch wiring problems in a Helm deployment
- [Core Concepts](core-concepts.md) — sessions, upload handles, and artifact URIs from first principles
- [Security](security/index.md) — exact auth and signed URL behavior if you are fighting 403s
