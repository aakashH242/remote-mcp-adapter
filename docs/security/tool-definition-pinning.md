# Tool Definition Pinning

What the adapter pins, when the baseline is established, how drift is detected, and what clients should expect when the upstream catalog changes mid-session.

---

## Why this exists

MCP tool metadata is model-visible input.

That means a compromised or sloppy upstream can change the attack surface without changing the tool name. A description can shift from harmless documentation to hidden instructions. A schema can add a suspicious field. A new tool can appear after the session has already reviewed and trusted the original catalog.

Tool-definition pinning exists to stop that from becoming an invisible mid-session change.

If your concern is the text the client sees on the very first catalog read, pair this with [Tool Metadata Controls](tool-metadata-controls.md). Pinning protects the session boundary after trust is established. Metadata controls shape what the client sees before that trust decision is made.

---

## What gets pinned

The adapter pins the effective client-visible tool catalog for one `Mcp-Session-Id`.

In practice that means:

- the first real catalog exposure becomes the session baseline
- later catalog reads are compared against that baseline
- the same protection applies whether the client is using the normal tool surface or Code Mode discovery

The baseline is session-scoped. It does not become a global trust decision for unrelated sessions.

The adapter also waits until the server's visible tool surface is fully wired before first-time baseline pinning. That avoids pinning a temporary partial catalog while helper tools and overrides are still being registered.

---

## What counts as drift

The adapter currently treats all of these as drift:

- tool description changes
- tool input schema changes
- new tools appearing
- previously visible tools disappearing

That is intentionally strict. The adapter does not try to guess whether a change is probably harmless.

---

## The config surface

Global default:

```yaml
core:
  tool_definition_pinning:
    mode: "warn"
    block_strategy: "error"
    block_error_session_action: "invalidate"
```

Per-server override:

```yaml
servers:
  - id: "playwright"
    tool_definition_pinning:
      mode: "block"
```

The three key choices are:

- `mode: off | warn | block`
- `block_strategy: error | baseline_subset`
- `block_error_session_action: keep | invalidate`

This sits alongside, not instead of, metadata cleanup and description policy. They solve different parts of the same upstream-metadata problem.

---

## What each mode means

### `off`

No baseline is pinned. The adapter does not try to detect catalog drift.

### `warn`

The adapter keeps serving the current catalog, but annotates what the session is seeing so the client is no longer making decisions on silent drift.

### `block`

The adapter treats drift as a trust-boundary event.

With `block_strategy: "error"`:

- the current request fails
- with `block_error_session_action: "invalidate"`, the session becomes unusable and the client must start a new `Mcp-Session-Id`

With `block_strategy: "baseline_subset"`:

- the session keeps only the unchanged trusted subset
- drifted, new, or removed tools disappear from the visible surface for that session

---

## Legitimate upstream upgrades

This is the part operators usually care about most.

If the upstream server is upgraded mid-session and the tool catalog genuinely changes, the adapter still treats that as drift. That is the correct security behavior.

The current trust model is:

- same adapter session means same pinned baseline
- a legitimate upstream upgrade should be accepted through a fresh adapter session
- in other words, start a new `Mcp-Session-Id` to trust the new catalog

That is stricter than automatic re-pinning, but it avoids quietly weakening the protection.

When pinning is enabled, the adapter also bypasses upstream `list_tools` metadata caching so drift is checked against the current upstream catalog rather than a stale cached response.

---

## What a client sees today

In the strict path:

- the first drifting request is blocked with a message telling the client to start a new `Mcp-Session-Id`
- if session invalidation is enabled, reuse of that same session is rejected

That rejection is surfaced as a session conflict rather than being mislabeled as an in-flight request limit.

When invalidation is enabled, the adapter stores the reason as a terminal tombstone. Reuse of that same session id stays blocked until that tombstone expires.

---

## What this does not do

Tool-definition pinning is important, but it is not magic.

It does not:

- decide whether a drift is malicious or benign
- prove that the upstream tool implementation is safe when the definition stays the same
- carry trust across unrelated sessions
- replace real auth, storage controls, or route protection

It is one defense in the larger trust model, not the whole model.

---

## Next steps

- **Previous topic:** [Tool Metadata Controls](tool-metadata-controls.md) - the controls that shape the visible tool text before a session pins it.
- **Next:** [High-Security Scenario](../configuration/high-security.md) - recommended hardened deployment profile.
- **See also:** [Configuration](../configuration.md) - the full config surface for pinning mode, block strategy, and invalidation behavior.
