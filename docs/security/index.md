# Security

The adapter already has a real security posture - it is not just "auth on or off." This section explains the threat model, the controls that exist today, and the deeper pages for auth and session handling, metadata controls, and tool-definition pinning.

The default stance is deliberately conservative, but not maximal:

- visible tool metadata is sanitized before it is forwarded
- tool-definition drift is surfaced as a warning by default

That gives a new deployment some real protection without forcing every installation into a strict block-only posture on day one.

---

## Read this section in order

This section is split deliberately.

- Start with [Threat Model](threat-model.md) if you want the "what can go wrong?" view first.
- Read [Current Defenses](current-defenses.md) for the controls that exist today and how they fit together.
- Read [Auth And Session Boundaries](auth-and-session-boundaries.md) if you want the route-auth, signed-URL, and session-integrity details.
- Read [Tool Metadata Controls](tool-metadata-controls.md) if your main concern is what visible tool text reaches the client.
- Read [Tool Definition Pinning](tool-definition-pinning.md) if your main concern is mid-session catalog drift or upstream tool-definition rug pulls.

---

## What this section covers

Today, the relevant controls include:

- adapter auth and signed URL flows
- session-scoped uploads and artifacts
- session binding on the authenticated path
- tool-surface reduction and per-server hiding
- optional description minimization or stripping for upstream tools
- metadata sanitization for model-visible tool text
- storage-path and artifact-locator boundaries
- quota and lifecycle controls
- fail-closed persistence choices
- tool-definition pinning and session invalidation on drift

This section explains those controls as a system instead of leaving them scattered across config snippets.

---

## Where the details live

Use the docs in this order:

- read this section for the trust boundaries, risks, and protections
- use the [Configuration guide](../configuration.md) when you need the exact fields and defaults
- use the scenario pages linked from [Configuration](../configuration.md) when you want a complete deployment shape

That way you can start with the security model, then move into the config and deployment pattern that fits your environment.

If you want the maintainer-facing snapshot of what is implemented right now in the repo, see the root [SECURITY.md](https://github.com/aakashH242/remote-mcp-adapter/blob/main/SECURITY.md) on GitHub.

---

## Where to start

Use the path that matches your question:

- If you are exposing the adapter to a network, start with [Current Defenses](current-defenses.md).
- If you want the exact auth, signed-upload, signed-download, and session-reuse rules, read [Auth And Session Boundaries](auth-and-session-boundaries.md).
- If you are deciding how much upstream tool prose should reach the model, read [Tool Metadata Controls](tool-metadata-controls.md).
- If you are tightening a real deployment, read the [High-Security Scenario](../configuration/high-security.md) alongside this section.
- If you are worried about an upstream MCP server changing what the model sees after trust has already been established, jump straight to [Tool Definition Pinning](tool-definition-pinning.md).

---

## Next steps

- **Previous topic:** [Configuration](../configuration.md) - practical config guide and scenario entry points.
- **Next:** [Threat Model](threat-model.md) - the concrete risks this section is trying to address.
- **Then:** [Auth And Session Boundaries](auth-and-session-boundaries.md) - how auth, signed URLs, and session reuse are enforced.
- **See also:** [High-Security Scenario](../configuration/high-security.md) - opinionated hardening profile.
