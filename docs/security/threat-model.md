# Threat Model

This project sits between an MCP client and one or more upstream MCP servers. The attack surface is not just "can someone reach the HTTP port?" - there are a few less obvious risks worth naming explicitly.

---

## The threats that matter here

The adapter has to think about at least these classes of failure:

- an unauthenticated or weakly authenticated caller reaching MCP routes
- an operator assuming every adapter route is protected when some endpoints are intentionally public
- an upstream server hiding instructions or junk inside tool titles, descriptions, or schema text
- a malicious or compromised upstream server changing the model-visible tool surface after the session has already trusted it
- replayed, leaked, or overly broad signed upload and download URLs
- file-path abuse, storage-root escape, or overbroad artifact lookup
- degraded persistence or routing behavior that quietly weakens session guarantees

That is why the security story spans auth, session state, storage, and tool metadata together.

---

## The most important trust boundaries

There are three that matter most.

### Client to adapter

This is where route auth, signed URLs, and session identity live. If this boundary is weak, everything behind it is exposed.

### Adapter to upstream

This is where the adapter decides whether to trust the upstream tool catalog for the current session. Tool-definition pinning exists because a model can be attacked through metadata, not just through tool output.

### Adapter to shared storage

Uploads and artifacts become security-sensitive the moment they touch disk. The adapter needs to enforce path boundaries, quotas, and cleanup so storage does not become a quiet exfiltration or denial-of-service layer.

---

## What the adapter can defend

The adapter can do meaningful things here:

- require auth before requests are forwarded
- issue short-lived signed upload and download URLs
- add one-time nonce replay protection to signed upload URLs
- scope uploads and artifacts to one `Mcp-Session-Id`
- bind stateful requests to the authenticated context that established the session
- normalize or block suspicious model-visible tool metadata before it reaches the client
- limit or remove description prose before it reaches the client
- block or warn on mid-session tool-definition drift
- invalidate a session once its pinned trust boundary is broken
- reduce visible tool surface with `disabled_tools` or Code Mode
- enforce storage-root checks, quotas, and artifact locator policy
- fail closed when persistence guarantees matter more than convenience

Those are meaningful controls. They are not cosmetic.

---

## What the adapter cannot defend

It is just as important to be explicit about the limits.

The adapter cannot:

- prove that an upstream tool implementation is safe if the upstream lies while keeping the same definition
- make an unsafe upstream host safe after compromise
- protect secrets already present on the upstream machine
- replace proper network policy, secret management, or host hardening
- make intentionally public endpoints private by itself
- turn CORS into authentication or authorization
- automatically decide that a legitimate upstream upgrade is safe enough to trust mid-session

That last point is why tool-definition pinning forces a new adapter session after blocked drift instead of silently absorbing the change.

---

## The practical reading of this threat model

If you are running the adapter only on localhost for your own tools, the main concern is usually convenience and correctness.

If you are running it for a team, behind a hostname, or in shared infrastructure, the adapter becomes part of the trust boundary whether you planned for that or not.

That is the point where these controls matter most:

- route auth and signed URLs
- session integrity
- metadata cleanup and minimization
- drift detection
- storage and persistence posture

---

## Next steps

- **Previous topic:** [Security Overview](index.md) - what this section covers and how to read it.
- **Next:** [Current Defenses](current-defenses.md) - the controls that exist today.
- **Then:** [Auth And Session Boundaries](auth-and-session-boundaries.md) - where the route and session trust boundary is enforced.
- **See also:** [Tool Definition Pinning](tool-definition-pinning.md) - the most specific defense against mid-session catalog drift.
