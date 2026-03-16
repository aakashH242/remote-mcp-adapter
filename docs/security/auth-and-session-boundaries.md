# Auth And Session Boundaries

How the adapter handles route auth, signed upload and download URLs, session scoping, and session reuse once it starts keeping state for uploads, artifacts, and drift protection.

---

## Start with the boundary in front of everything else

`core.auth` is the first control most operators reach for, and it matters. It decides:

- whether MCP and adapter-owned HTTP routes require the adapter auth header
- which header name is accepted
- which token value is valid
- which secret is used to sign upload and artifact-download URLs

That is the first trust boundary between the client and the adapter.

It is still worth being explicit about what that does **not** mean:

- auth does not automatically make every route private
- auth does not make CORS a security boundary
- auth does not make `Mcp-Session-Id` trustworthy by itself

Those details are where most confusion starts.

---

## Some endpoints are intentionally public

Even when `core.auth.enabled` is true, a small set of routes is intentionally left public:

- `/healthz`
- `/docs`
- `/redoc`
- `/openapi.json`
- OAuth and OpenID discovery endpoints under `/.well-known/...`

That is deliberate. These routes support health checks, docs, and discovery flows.

It also means the adapter should not be treated as private by default just because `core.auth` is enabled. If those routes should not be visible to the wider network in your environment, hide them at the ingress, proxy, or network layer.

---

## CORS helps browsers, not trust

`core.cors` only controls browser cross-origin behavior.

It can make browser-based clients work, but it does not:

- authenticate the caller
- authorize the caller
- turn a public adapter into a private one

Treat it as transport compatibility, not access control.

---

## Signed URLs narrow the HTTP surface

The adapter supports short-lived signed URLs for uploads and, optionally, for HTTP artifact downloads.

That matters because it lets browser-assisted or human-assisted flows work without exposing the main adapter token on every upload and download hop.

Current signed-upload behavior:

- the signed credential is short-lived
- it is bound to a specific session
- it is one-time, with nonce replay protection
- the upload POST still requires the matching `Mcp-Session-Id`

Current artifact-download behavior:

- signed download links are optional
- they are separate from MCP `artifact://...` resource reads
- MCP `resources/read` can stay enabled even if HTTP downloads are disabled

This is the practical split:

- use signed HTTP routes when a browser or another non-MCP client needs to move bytes
- use MCP resources when the MCP client itself should read the artifact

In any real deployment behind Docker networking, ingress, or a reverse proxy, set `core.public_base_url` so generated URLs point at the externally reachable address instead of an internal container or pod hostname.

---

## Sessions are part of the security model

`Mcp-Session-Id` is not just routing metadata in this project.

The adapter uses it to scope:

- staged uploads
- captured artifacts
- cleanup and quotas
- tool-definition baselines
- session invalidation after a trust break

That means session handling is part of the security posture, not just an implementation detail.

If the adapter kept state but treated session existence as enough proof of authority, it would be too easy to attach to the wrong flow or reuse an old session in the wrong context.

---

## Session id is not enough on the main auth path

When `core.auth` is enabled and a stateful request uses the main adapter auth token path, the adapter now binds that session to the auth context that established it.

Today that means:

- the adapter stores a stable fingerprint of the adapter auth token
- that fingerprint is bound to `(server_id, session_id)`
- later stateful requests for that same adapter session must present the same adapter-auth context
- a mismatch is rejected with `409 Conflict`

This hardens the current trust model in an important way:

- `Mcp-Session-Id` still identifies the session
- auth still has to be valid on every request
- the authenticated context that established the session cannot silently change mid-session

Important limit:

This is not a full per-user identity system. In a shared-token deployment, every caller who knows that token is still the same principal from the adapter's point of view. The binding still matters, but it is only as strong as the auth model in front of it.

---

## Signed flows do not overwrite the main auth binding

Signed upload and signed artifact-download routes are treated separately from the main adapter-auth path.

That matters because they solve a different problem:

- the main auth path is the normal trusted MCP and HTTP path
- signed URLs are narrow, short-lived credentials for specific file flows

The adapter does not let those signed flows quietly replace the session's main auth-context binding.

That keeps the trust model cleaner:

- normal stateful requests stay tied to the auth context that established the session
- signed flows stay narrow and purpose-built

---

## Invalidated sessions stay invalid

Some security events are treated as trust-boundary breaks, not just ordinary request failures.

The clearest example is tool-definition drift when pinning is configured to invalidate the session.

In that case:

- the adapter stores the invalidation reason as a terminal tombstone
- reuse of that same session id stays blocked until the tombstone expires

This is deliberate. Once the pinned trust boundary is broken, the adapter should not keep pretending the old session is still healthy.

---

## Persistence still matters here

Session integrity depends on state.

If your deployment depends on:

- shared session guarantees
- nonce replay protection
- predictable invalidation behavior

then `state_persistence.unavailable_policy` is part of the security story too.

The practical rule is simple:

- if you care more about strict guarantees than temporary convenience, prefer fail-closed behavior

One implementation detail is worth remembering in stricter environments:

- if a persistent backend falls back to memory at runtime, upload nonce replay protection also falls back to an in-memory nonce store on that adapter process

That may be acceptable in local development. It is usually not the posture you want in a serious shared deployment.

---

## Where to configure this

The main knobs behind this page are:

- `core.auth`
- `core.public_base_url`
- `core.allow_artifacts_download`
- `sessions.idle_ttl_seconds`
- `sessions.tombstone_ttl_seconds`
- `state_persistence.unavailable_policy`

For complete field descriptions, use the [Configuration guide](../configuration.md) and the [Detailed Reference](../configuration/config-reference.md).

If you want a stricter end-to-end profile, pair this page with the [High-Security Scenario](../configuration/high-security.md).

---

## Next steps

- **Previous topic:** [Current Defenses](current-defenses.md) - the full control set at a glance.
- **Next:** [Tool Metadata Controls](tool-metadata-controls.md) - how the adapter shapes visible tool text before the client sees it.
- **See also:** [High-Security Scenario](../configuration/high-security.md) - an opinionated hardening profile.
