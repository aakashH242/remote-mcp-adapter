# Helm Guide: Post-Install Verification

**What you'll learn here:** what to check right after a Helm install or upgrade, how to confirm the adapter is actually reachable and healthy, and how to catch the most common wiring mistakes before users do.

---

## Why this matters

A Helm install that finishes successfully is only the beginning.

The adapter can still be unusable if:

- the upstream is not healthy yet
- shared storage is mounted wrongly
- `core.public_base_url` points at the wrong hostname
- Redis is missing in a multi-replica deployment
- ingress is serving traffic, but helper-generated URLs still point somewhere internal

That is why a short verification pass is worth doing every time.

---

## Step 1: Check pods and rollout

```bash
kubectl get pods -n remote-mcp-adapter
kubectl rollout status deploy/remote-mcp-adapter -n remote-mcp-adapter
```

What you want to see:

- all adapter pods in `Running`
- rollout completed successfully
- no crash loops

If you are using the standalone shape, also confirm the colocated upstream container is healthy inside the pod.

---

## Step 2: Check service or ingress reachability

If you are using a ClusterIP service, port-forward first:

```bash
kubectl port-forward -n remote-mcp-adapter svc/remote-mcp-adapter 8932:8932
```

If you are using ingress, confirm the hostname resolves and points where you expect.

---

## Step 3: Check `/healthz`

```bash
curl -s http://localhost:8932/healthz | jq
```

Or, through ingress:

```bash
curl -s https://your-public-hostname.example.com/healthz | jq
```

What you want to see:

- overall `status: ok`
- configured upstreams listed under `servers`
- persistence backend status matching your intended setup

If you are on an HA shape, a degraded persistence section usually means Redis or shared-state wiring is wrong.

---

## Step 4: Check that helper-generated URLs look right

This step matters if users will call `<server_id>_get_upload_url(...)` or click HTTP artifact download links.

What you are checking:

- URLs use the real external hostname
- URLs do not point at a pod IP, service DNS name, or internal container hostname
- signed URLs work before expiry

If they are wrong, the first thing to check is `core.public_base_url`.

---

## Step 5: Run one upload flow end to end

For any server with an `upload_consumer` adapter:

1. call `<server_id>_get_upload_url(...)`
2. upload a small test file
3. confirm you get back an `upload://...` handle
4. call the wrapped tool with that handle

If the tool still fails upstream with file-not-found or access errors, the usual problem is shared storage reachability, not the helper tool itself.

---

## Step 6: Run one artifact flow end to end

For any server with an `artifact_producer` adapter:

1. call a tool that produces an artifact
2. confirm the response includes artifact metadata or a download link when configured
3. if resources are enabled, read the `artifact://...` resource
4. if HTTP downloads are enabled, open the generated download URL

If this fails, check:

- shared storage path on both sides
- `core.public_base_url` for external links
- auth and signing configuration for download routes
- artifact TTL if you waited too long

---

## Step 7: Check logs once while the system is quiet

```bash
kubectl logs -n remote-mcp-adapter deploy/remote-mcp-adapter --tail=200
```

You are looking for obvious startup problems such as:

- upstream connection failures
- Redis connection failures
- signed upload or download validation errors
- storage-path mismatches
- persistence fallback or degraded-health messages

---

## A practical pass/fail checklist

Treat the deployment as ready only when all of these are true:

- pods are healthy
- `/healthz` is healthy or degraded only for an understood reason
- one real upload flow works if upload adapters are configured
- one real artifact flow works if artifact adapters are configured
- generated URLs point at the real client-facing hostname

---

## Next steps

- **Previous topic:** [Layered values-file pairs](layered-values-file-pairs.md) - combine base shapes and overlays cleanly.
- **Back to:** [Deploy with Helm](../helm.md) - Helm overview and scenario index.
- **See also:** [Health](../../health.md) - understand degraded states and backend status.
- **See also:** [Troubleshooting](../../troubleshooting.md) - common failures and fixes.
