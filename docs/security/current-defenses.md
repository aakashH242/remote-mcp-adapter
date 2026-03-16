# Current Defenses

Which security controls already exist in the adapter today, what they protect, and which config fields activate or tighten them.

---

## Start with auth, but do not stop there

The first line of defense is still `core.auth`.

It controls:

- whether MCP routes require the adapter auth header
- which header name is used
- which token value is accepted
- which secret is used to sign upload and download URLs

That protects who can reach the adapter. It does not by itself protect what the model sees after the session starts.

It is also worth being explicit about what auth does **not** cover automatically.

Some endpoints are intentionally public today:

- `/healthz`
- `/docs`
- `/redoc`
- `/openapi.json`
- OAuth and OpenID discovery paths under `/.well-known/...`

That is a product choice, not an oversight. If those endpoints should not be visible in your environment, hide them at the ingress, proxy, or network layer.

### CORS is not access control

`core.cors` only controls browser cross-origin behavior.

It can make browser clients work more smoothly, but it does not authenticate callers and it does not turn a public adapter into a private one.

For exact route behavior, see the [Configuration guide](../configuration.md) and the [High-Security Scenario](../configuration/high-security.md).

---

## Signed URLs narrow the exposed HTTP surface

The adapter can keep upload and download flows off the main auth path when that is useful, but only in a bounded way.

Current protections:

- signed upload URLs are short-lived
- they are bound to the session
- signed upload credentials are one-time credentials backed by nonce replay protection
- download URLs can be signed too when artifact downloads are enabled
- invalid or expired signatures are rejected

Two details matter here:

- upload POSTs still require the matching `Mcp-Session-Id`
- HTTP artifact downloads are separate from MCP `artifact://...` resource reads

That second point is easy to miss. You can keep artifact bytes available through MCP `resources/read` without also enabling browser-clickable HTTP download links.

This reduces the need to expose the main adapter auth token in every upload/download hop.

It does not make generated URLs magically safe. In real deployments you still need a correct `core.public_base_url`, short TTLs, and the right decision about whether HTTP artifact downloads should exist at all.

If you want the detailed route and session behavior, read [Auth And Session Boundaries](auth-and-session-boundaries.md).

---

## Session scoping is part of the security model

`Mcp-Session-Id` is not just operational metadata. It is part of the trust boundary.

Today the adapter uses it to:

- scope uploads
- scope artifacts
- scope quotas and cleanup
- scope tool-definition baselines
- decide whether a drift event kills only the current session or everyone

This is why blocked tool-definition drift can invalidate the current session instead of just returning one error and carrying on.

There is also a second rule now on the main authenticated path.

When a stateful request uses the normal adapter auth token, the adapter binds that session to a stable fingerprint of the token that established it. Later stateful requests for the same `(server_id, session_id)` must reuse the same adapter auth context or the adapter rejects the request with `409 Conflict`.

That does **not** turn a shared token into a real user identity. It does make the current trust model more explicit:

- session id routes the state
- auth still has to be valid on each request
- the authenticated context that established the session cannot silently change mid-session

This behavior is covered in more detail in [Auth And Session Boundaries](auth-and-session-boundaries.md).

---

## Tool surface can already be reduced

There are two current controls here.

### Per-server disabled tools

Use `servers[].disabled_tools` to hide tools you do not want exposed at all.

That is the blunt, explicit control. If the tool should not be callable in a given environment, disable it.

### Code Mode

Use `core.code_mode_enabled` or `servers[].code_mode_enabled` to collapse the visible tool surface into discovery and execute tools.

This helps reduce surface area for weaker models and coding-agent workflows. It is useful, but it is a surface-shaping control, not a substitute for auth or tool-definition pinning.

---

## Tool metadata can now be cleaned before it is forwarded

Sometimes the problem is not that the upstream changed tools mid-session. The problem is that the upstream already returned messy or suspicious tool text in the first place.

That is what `core.tool_metadata_sanitization` and `servers[].tool_metadata_sanitization` are for.

This is now on by default in `sanitize` mode. A fresh adapter config already cleans suspicious visible tool text unless you explicitly turn the feature off.

This control can:

- normalize odd Unicode forms
- remove invisible formatting characters
- cap very long tool titles and descriptions
- cap schema text fields that would otherwise dump too much prose into the tool surface

It only touches the parts of the tool definition that clients and models actually read:

- tool `title`
- tool `description`
- `annotations.title`
- input and output schema `title`
- input and output schema `description`

The adapter does not try to rewrite the tool into different prose or guess what the upstream "meant." It keeps the behavior conservative on purpose.

If you choose `mode: "sanitize"`, the adapter forwards the cleaned version and logs that it had to change something.

If you choose `mode: "block"`, the adapter hides tools whose visible metadata would have needed cleanup instead of silently rewriting them.

This works well with tool-definition pinning, but it solves a different problem:

- metadata sanitization cleans what the client sees right now
- tool-definition pinning protects the session if the upstream changes that surface later

For the exact scope, ordering, and tradeoffs, read [Tool Metadata Controls](tool-metadata-controls.md).

---

## Description prose can now be minimized too

Some deployments do not want to forward full upstream tool prose even after cleanup.

That is what `core.tool_description_policy` and `servers[].tool_description_policy` are for.

This control is separate from metadata sanitization:

- metadata sanitization cleans suspicious text
- description policy decides how much description prose should be forwarded at all

The current modes are:

- `preserve`
- `truncate`
- `strip`

The policy applies to:

- top-level tool descriptions
- nested schema `description` fields

It runs before Code Mode. That means Code Mode inherits the same minimized description surface instead of becoming a bypass around the policy.

This is not the default path for every deployment. It is a stronger hardening control for environments where upstream descriptive prose itself is the thing you want to reduce.

For the exact interaction with sanitization, Code Mode, and pinning, read [Tool Metadata Controls](tool-metadata-controls.md).

---

## Tool-definition pinning is now a first-class defense

This is the main answer to the "benign tool at review time, different tool later" problem.

With `core.tool_definition_pinning` enabled, the adapter:

- pins the effective client-visible catalog on first exposure for a session
- detects changed descriptions, changed schemas, new tools, and removed tools
- warns or blocks based on policy
- can invalidate the session immediately in `block + error`

The default is `warn`. That means a fresh deployment already gets drift detection without immediately turning legitimate upstream upgrades into hard failures.

The implementation also tightens two things behind the scenes:

- baseline pinning waits until the server's visible tool surface is fully wired, so the adapter does not pin a transient partial catalog
- upstream `list_tools` caching is bypassed while pinning is enabled, so stale cache entries do not hide real drift

If you care about upstream catalog trust, this control matters more than most people initially expect.

The full behavior lives in [Tool Definition Pinning](tool-definition-pinning.md).

---

## Storage controls are security controls

Uploads and artifacts are file operations. That means storage settings are part of the security posture, not just housekeeping.

Useful current controls:

- `storage.root`
- `storage.max_size`
- `storage.artifact_locator_policy`
- `uploads.max_file_bytes`
- `uploads.require_sha256`
- `artifacts.max_per_session`
- `sessions.max_total_session_size`

Together, these reduce:

- storage exhaustion
- unsafe artifact discovery outside reviewed roots
- accidental acceptance of corrupted uploads
- quiet growth of one session until it crowds out everything else

---

## Persistence policy can fail open or fail closed

When the backing state is unavailable, the adapter can either stay strict or become permissive depending on config.

That makes `state_persistence.unavailable_policy` a security-relevant choice.

If your environment depends on strong session guarantees, shared state, or predictable cleanup, use a fail-closed posture and do not treat degraded state as "good enough."

One implementation detail worth remembering in serious deployments: if a persistent backend falls back to memory at runtime, upload nonce replay protection also falls back to an in-memory nonce store on that adapter process.

---

## Logging also has a security posture

The adapter installs a log redaction filter at startup.

Today it redacts:

- the configured adapter auth header
- configured telemetry headers
- upstream static headers
- passthrough and required client header names
- common bearer/basic/JWT-shaped token patterns in free-form text

That does not make logging risk-free, but it does reduce the chance of leaking tokens or header material through normal application logs.

---

## Where the guardrail knobs live

If you are looking for the config fields behind these defenses, start with:

- `core.auth`
- `core.tool_description_policy`
- `servers[].tool_description_policy`
- `core.tool_metadata_sanitization`
- `servers[].tool_metadata_sanitization`
- `core.tool_definition_pinning`
- `servers[].tool_definition_pinning`
- `servers[].disabled_tools`
- `core.code_mode_enabled`
- `servers[].code_mode_enabled`
- `uploads.require_sha256`
- `uploads.max_file_bytes`
- `artifacts.max_per_session`
- `sessions.max_total_session_size`
- `state_persistence.unavailable_policy`
- `core.allow_artifacts_download`
- `core.public_base_url`

The [Configuration guide](../configuration.md) groups these under a dedicated security-and-guardrails section so operators can find them without already knowing the internal model.

---

## Where to configure this in practice

Once you know which controls matter for your deployment, these are the most useful follow-up pages:

- Read the [Configuration guide](../configuration.md) for the actual field names and precedence rules.
- Read the [High-Security Scenario](../configuration/high-security.md) if you want the strictest security-oriented overlay.
- Read the [Restricted-Limits Scenario](../configuration/restricted-limits.md) if your main concern is containment and fairness under resource pressure.
- Read the [Agent-Optimized Code Mode Scenario](../configuration/agent-optimized-code-mode.md) if you want Code Mode as a surface-shaping choice without confusing it for a primary security boundary.

---

## Next steps

- **Previous topic:** [Threat Model](threat-model.md) - the risks these controls are addressing.
- **Next:** [Auth And Session Boundaries](auth-and-session-boundaries.md) - route auth, signed URLs, and session reuse in detail.
- **Then:** [Tool Metadata Controls](tool-metadata-controls.md) - how visible tool text is cleaned and minimized.
- **See also:** [Configuration](../configuration.md) - where the knobs actually live.
