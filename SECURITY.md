# Security Posture

This file tracks the security controls that already exist in this repository.

It is meant to stay close to the implementation so maintainers have one place to check what the adapter actually does today. It is not a product roadmap, and it is not a promise that every future security idea already exists.

For the user-facing explanation, see the docs section under [`docs/security/`](docs/security/).

Current default stance:

- `core.tool_metadata_sanitization.mode = "sanitize"`
- `core.tool_definition_pinning.mode = "warn"`

## What this adapter is protecting

Remote MCP Adapter sits between:

- the MCP client
- the adapter itself
- one or more upstream MCP servers
- shared storage used for uploads and artifacts

That means the security posture is not only about who can reach the HTTP port. It is also about:

- what tool metadata the client is allowed to trust
- how uploads and artifacts are scoped and retrieved
- whether one session can interfere with another
- whether degraded state quietly weakens guarantees

## Trust boundaries

### Client to adapter

This boundary covers route authentication, signed upload and download URLs, and session identity.

Relevant controls:

- `core.auth`
- `core.public_base_url`
- upload and artifact route signature validation

### Adapter to upstream

This boundary covers the model-visible tool surface and the risk that an upstream server changes it after trust has already been established.

Relevant controls:

- `servers[].disabled_tools`
- `core.code_mode_enabled`
- `servers[].code_mode_enabled`
- `core.tool_description_policy`
- `servers[].tool_description_policy`
- `core.tool_metadata_sanitization`
- `servers[].tool_metadata_sanitization`
- `core.tool_definition_pinning`
- `servers[].tool_definition_pinning`

### Adapter to shared storage

This boundary covers staged uploads, captured artifacts, path validation, quotas, and cleanup behavior.

Relevant controls:

- `storage.root`
- `storage.artifact_locator_policy`
- upload and artifact size/count limits
- session-scoped storage bookkeeping

## Implemented controls

### Route authentication and signed URL flows

The adapter can require an auth header on inbound MCP and HTTP routes through `core.auth`.

It also supports short-lived signed upload URLs and optional signed artifact download URLs so browser-assisted flows do not need to expose the main adapter token on every hop.

Important details:

- signed URLs are time-bounded
- signed URLs are session-aware
- signed upload credentials are one-time credentials backed by nonce replay protection
- upload POSTs still require the matching `Mcp-Session-Id`
- HTTP artifact downloads are optional and separate from MCP `resources/read`
- `core.public_base_url` should be treated as required in real deployments behind Docker networking, ingress, proxies, or load balancers

Artifact download auth has two paths today:

- signed query credentials when artifact-download signing is enabled
- session-context checks using the adapter session when signed download auth is not used

MCP `artifact://...` resource reads stay on the MCP session path and do not require enabling HTTP download links.

Signed upload and signed artifact-download flows are kept separate from the main adapter-auth binding path. They are narrow, purpose-built credentials for file movement, not a replacement for the session's main authenticated context.

### Public endpoints and intentionally unauthenticated surfaces

Not every route sits behind `core.auth`.

These are intentionally public today:

- `/healthz`
- `/docs`
- `/redoc`
- `/openapi.json`
- OAuth/OpenID discovery endpoints under `/.well-known/...`

That is deliberate, but it matters operationally. If those endpoints should not be visible to a broader network, hide them with deployment-level controls such as ingress policy, reverse-proxy policy, or network restrictions.

### CORS is a browser compatibility control, not an auth control

`core.cors` only controls which browser origins can make cross-origin requests and which methods or headers the browser is allowed to send.

It does not authenticate callers and it does not make a public adapter private. Treat it as transport compatibility, not access control.

### Session-scoped state and lifecycle

`Mcp-Session-Id` is part of the trust model, not just an operational header.

The adapter uses it to scope:

- staged uploads
- captured artifacts
- quotas
- cleanup
- tool-definition pinning baselines

Expired sessions can be tombstoned and revived within the configured grace window. Security-sensitive decisions such as tool-definition pinning remain scoped to that adapter session.

There is also an adapter-side binding rule on the main authenticated path.

- when a stateful request is authenticated with the adapter auth token, the adapter binds that `(server_id, session_id)` to a stable fingerprint of the token that established it
- later stateful requests for that same adapter session must reuse the same adapter auth context
- a mismatch is rejected with `409 Conflict`

This keeps the adapter from treating `Mcp-Session-Id` as sufficient authority by itself on the main authenticated path.

Important limit:

This is still not true per-user identity binding. In the common shared-token deployment model, every caller presenting that token is still the same principal from the adapter's point of view.

### Tool surface controls

The adapter can reduce the visible tool surface in two different ways.

`servers[].disabled_tools` is the hard deny-list control. If a tool should not be visible or callable in a deployment, disable it explicitly.

Code Mode is a surface-shaping control. It replaces the full tool list with a smaller discovery and execute surface for agent-heavy workflows. It reduces what the client has to reason about, but it is not a substitute for auth or drift protection.

### Tool metadata sanitization

The adapter can apply a conservative sanitization pass to model-visible tool
metadata before it reaches the client.

This control exists for the same reason tool-definition pinning exists:
tool metadata is part of the model-visible attack surface. The adapter should
not blindly forward every upstream text field exactly as it arrived when a
deployment wants tighter trust hygiene.

Current policy surface:

- `core.tool_metadata_sanitization`
- `servers[].tool_metadata_sanitization`

Current modes:

- `off` - forward tool metadata as-is
- `sanitize` - normalize and sanitize visible metadata fields before forwarding
- `block` - hide tools whose visible metadata would have required sanitization

The shipped default is `sanitize`.

The current sanitization scope is intentionally narrow and explicit. It applies
to:

- top-level tool `title`
- top-level tool `description`
- `annotations.title`
- input schema `title`
- input schema `description`
- output schema `title`
- output schema `description`

The current transformation can:

- apply Unicode NFKC normalization
- remove invisible formatting characters
- cap tool title length
- cap tool description length
- cap schema text length

The current implementation also reuses the same normalization helpers for
internal canonicalization in tool-definition pinning. That matters because the
adapter should not treat harmless Unicode-form differences as trust-breaking
drift if the forwarded client-visible surface is already normalized.

There are also deliberate limits to what this control does today.

It does not currently:

- rewrite arbitrary prose into "safer" prose
- sanitize `icons`
- generically sanitize all `_meta` payloads
- infer intent from tool text
- mutate non-text schema structure

That boundary is intentional. The goal is conservative cleanup of clearly
model-visible text fields, not broad schema rewriting.

In `block` mode, tools that would have required sanitization are removed from
the visible tool surface instead of being silently rewritten. This is useful
for stricter environments where upstream metadata should already be clean.

Whenever sanitization changes a forwarded tool, the adapter emits a bounded
structured log event with the server id, tool name, source path, and changed
field markers.

### Tool description policy

The adapter can also control how much description prose from upstream tools is
forwarded at all.

This is a different control from metadata sanitization.

- metadata sanitization cleans suspicious text
- tool description policy decides how much description text is forwarded

Current policy surface:

- `core.tool_description_policy`
- `servers[].tool_description_policy`

Current modes:

- `preserve` - keep descriptions after earlier sanitization passes
- `truncate` - keep only the first configured number of characters
- `strip` - remove description text entirely

The current scope is intentionally limited to description prose:

- top-level tool `description`
- schema `description` fields, including nested property descriptions

It does not currently change titles. Titles are still covered by
`tool_metadata_sanitization`, which is the cleaner place for visible-title
cleanup.

This control is also applied before Code Mode. That matters because Code Mode
discovery and execute tools should inherit the same minimized description
surface as the normal tool catalog. If the policy ran after Code Mode, Code
Mode would become a bypass.

It is also applied before tool-definition pinning. The session should pin the
effective client-visible surface, not a raw upstream surface the client never
actually saw.

The current shipped default remains `preserve`. This is a stronger hardening
feature for deployments that want less model-visible prose from upstream tools,
not a general default for every installation.

### Tool-definition pinning and drift handling

The adapter can pin the effective client-visible tool catalog on first exposure for a given `Mcp-Session-Id`.

Current drift detection covers:

- description changes
- input schema changes
- new tools appearing
- previously visible tools disappearing

Current policy surface:

- `mode: off | warn | block`
- `block_strategy: error | baseline_subset`
- `block_error_session_action: keep | invalidate`

The shipped default is `mode: "warn"`.

Current behavior:

- `warn` allows the current catalog but marks what the client is seeing
- `block + error` fails the request
- `block + error + invalidate` makes the old session unusable and requires a new `Mcp-Session-Id`
- `block + baseline_subset` keeps only the unchanged trusted subset visible and callable

The baseline is session-scoped. There is no separate baseline TTL. Legitimate upstream upgrades are accepted by starting a new adapter session, not by silently re-trusting mid-session changes.

The implementation also does two things that matter for the security story:

- it waits until a server mount is fully wired before first-time baseline pinning, so the adapter does not pin a transient partial tool surface
- it bypasses upstream `list_tools` metadata caching while pinning is enabled, so stale cache entries do not hide catalog drift

### Session invalidation after trust break

When drift policy is configured to invalidate the session, later reuse of that same session is rejected as a session conflict.

This is intentional. Once the pinned trust boundary is broken, the adapter does not keep pretending that the old session is healthy.

Terminal invalidation is stored as a tombstone reason. That means reuse of the same session id stays blocked until the tombstone expires.

### Storage-root and artifact boundaries

Uploads and artifacts are security-sensitive file operations.

Current protections include:

- enforcing a configured shared storage root
- artifact locator policy controls
- session-scoped upload and artifact bookkeeping
- atomic write patterns and cleanup behavior
- quotas on file size, artifact count, and total session size
- optional upload digest requirements through `uploads.require_sha256`

The artifact locator policy matters because it decides whether artifact recovery is limited strictly to managed storage paths or may inspect additional configured roots.

### Persistence posture

The adapter can run with in-memory state, SQLite, or Redis-backed state.

That is not just an operational choice. `state_persistence.unavailable_policy` decides whether the adapter keeps serving in a degraded state or fails closed when durable/shared state is unavailable.

For stricter deployments, this is a real security decision, not only an uptime preference.

There is one more implementation detail worth remembering: if a persistent backend falls back to memory at runtime, upload nonce replay protection also falls back to an in-memory nonce store on that adapter process.

### Health and telemetry

The adapter exposes health signals and telemetry that matter for security operations too.

Current examples:

- auth rejection metrics
- request rejection metrics
- drift detection events and counters
- health reporting for wiring and upstream reachability

These do not replace controls, but they do make it easier to detect when controls are firing or when the adapter is drifting into an unsafe operating mode.

### Log redaction for sensitive headers and tokens

The adapter installs a log redaction filter at startup.

Today it redacts:

- the configured adapter auth header
- configured telemetry headers
- upstream static headers
- passthrough and required client header names
- common bearer/basic/JWT-shaped token patterns in free-form log text

This does not remove the need for careful logging, but it reduces the chance of leaking security-sensitive material in normal logs.

## What this adapter does not guarantee

The adapter does not:

- prove that an upstream tool implementation is safe when the definition stays the same
- make a compromised upstream host trustworthy
- replace network policy, secret management, or host hardening
- automatically decide that a mid-session upstream upgrade is safe enough to trust
- turn Code Mode into a security boundary by itself

If the upstream server is malicious while keeping the same outward tool definition, the adapter cannot cryptographically prove the behavior behind that definition.

## Operator minimums

For any non-local deployment, treat these as baseline expectations:

- enable `core.auth`
- set a real `core.public_base_url`
- keep signed URL TTLs short
- review `servers[].disabled_tools`
- review whether `tool_description_policy` should stay at `preserve`, move to
  `truncate`, or move to `strip` for your upstream trust level
- leave `tool_metadata_sanitization` enabled unless you have a specific reason
  to forward upstream metadata untouched
- leave `tool_definition_pinning` enabled unless you have a specific reason to
  stop drift detection for a deployment
- choose a deliberate `state_persistence.unavailable_policy`
- review whether the intentionally public endpoints are acceptable in your deployment
- treat `core.cors` as browser plumbing, not as protection
- set upload, artifact, and session size limits

## Related docs

- User-facing security section: [`docs/security/`](docs/security/)
- Threat model: [`docs/security/threat-model.md`](docs/security/threat-model.md)
- Current defenses: [`docs/security/current-defenses.md`](docs/security/current-defenses.md)
- Auth and session boundaries: [`docs/security/auth-and-session-boundaries.md`](docs/security/auth-and-session-boundaries.md)
- Tool metadata controls: [`docs/security/tool-metadata-controls.md`](docs/security/tool-metadata-controls.md)
- Tool-definition drift behavior: [`docs/security/tool-definition-pinning.md`](docs/security/tool-definition-pinning.md)
- Config guide: [`docs/configuration.md`](docs/configuration.md)

## Keeping this file current

Update this file whenever a change does any of the following:

- adds or removes a security-relevant control
- changes a trust boundary
- changes what happens after a session trust break
- changes how uploads, artifacts, auth, or storage are protected
- changes the operator-facing security posture of a deployment

If the code changes but this file and the user-facing security docs do not, the security story will drift.
