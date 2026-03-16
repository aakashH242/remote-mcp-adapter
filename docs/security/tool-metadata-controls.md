# Tool Metadata Controls

How the adapter cleans visible tool metadata, how it can reduce or remove description prose entirely, and why both controls run before Code Mode and pinning.

---

## There are two separate controls here

The adapter now has two metadata-facing controls that are related, but not interchangeable.

### Tool metadata sanitization

This control cleans suspicious or messy model-visible text before it is forwarded.

Use it when you want the adapter to normalize what the client sees without changing the overall shape of the tool surface.

### Tool description policy

This control decides how much description prose should be forwarded at all.

Use it when the problem is not that the text is dirty, but that the model should not see this much upstream prose in the first place.

The easiest way to remember the difference is:

- sanitization cleans text
- description policy limits text

---

## What sanitization touches today

`tool_metadata_sanitization` is intentionally conservative.

It applies to:

- top-level tool `title`
- top-level tool `description`
- `annotations.title`
- input schema `title`
- input schema `description`
- output schema `title`
- output schema `description`

It does **not** currently try to sanitize everything in sight. In particular, it does not generically rewrite:

- `icons`
- arbitrary `_meta`
- non-text schema structure

That boundary is intentional. The goal is to clean the visible text fields the client and model actually read, not to become a broad schema rewriter.

---

## What sanitization can do

Current sanitization behavior can:

- normalize Unicode into a stable form
- remove invisible formatting characters
- cap tool title length
- cap tool description length
- cap schema text length

The current modes are:

- `off`
- `sanitize`
- `block`

The shipped default is `sanitize`.

That means a fresh adapter config already does some meaningful cleanup before visible tool text reaches the client.

In `sanitize` mode:

- the cleaned version is forwarded
- the adapter logs that the visible metadata had to be changed

In `block` mode:

- tools that would have required cleanup are hidden instead of being silently rewritten

That stricter mode is useful when you expect upstream metadata to already be clean and want to treat deviations as a problem in themselves.

---

## What description policy touches today

`tool_description_policy` is narrower in one sense and stronger in another.

It focuses only on description prose:

- top-level tool `description`
- nested schema `description` fields

The current modes are:

- `preserve`
- `truncate`
- `strip`

The shipped default is `preserve`.

That is deliberate. This is a stronger hardening control, not a universally safe default for every deployment.

### `preserve`

Keep the upstream descriptions after earlier cleanup passes.

### `truncate`

Keep only the first configured number of characters.

### `strip`

Remove description prose entirely.

This applies to nested schema descriptions on purpose. If it only changed the top-level tool description, an upstream could move the same persuasive or poisoned prose into schema property descriptions and bypass the policy.

---

## Why both controls exist

These controls solve different problems and are meant to layer together.

Example layering:

1. sanitize messy Unicode and invisible characters
2. then preserve, truncate, or strip the remaining description prose
3. then pin the resulting visible catalog for the session

That gives a much cleaner trust story than asking one feature to do everything.

In practical terms:

- sanitization helps with obviously dirty or suspicious text
- description policy helps when you want a smaller or less persuasive text surface
- pinning helps once the session has already accepted the visible surface

---

## These controls run before Code Mode

This detail matters.

The adapter applies metadata sanitization and description policy before Code Mode.

That means:

- the normal tool catalog sees the cleaned or minimized metadata
- Code Mode discovery tools also see the cleaned or minimized metadata
- Code Mode does not become a bypass around the metadata controls

If these controls ran after Code Mode, the synthetic discovery path could expose a different metadata surface from the normal path. That would be a confusing and weaker design.

---

## These controls also affect pinning

They run before tool-definition pinning for the same reason.

The session should pin the effective client-visible catalog, not a raw upstream catalog the client never actually saw.

That gives a cleaner trust boundary:

- first the adapter shapes the visible metadata
- then the session pins that visible shape
- later drift is measured against what the client was actually shown

Without that ordering, harmless normalization differences could look like trust-breaking drift or, worse, the session could pin a surface different from the one the client reviewed.

---

## When to use which policy

Use the default path when:

- you want better hygiene without making tool catalogs harder to read

That usually means:

- `tool_metadata_sanitization.mode: "sanitize"`
- `tool_description_policy.mode: "preserve"`

Use a stricter path when:

- the upstream is only semi-trusted
- the model should not see long descriptive prose
- you want tighter control over the visible tool surface

That usually means some combination of:

- `tool_metadata_sanitization.mode: "block"`
- `tool_description_policy.mode: "truncate"`
- `tool_description_policy.mode: "strip"`

`strip` is the strongest option, but it is also the hardest on usability. It removes helpful context for both humans and models. Use it when the reduced attack surface matters more than convenience.

---

## Where to configure this

The main knobs behind this page are:

- `core.tool_metadata_sanitization`
- `servers[].tool_metadata_sanitization`
- `core.tool_description_policy`
- `servers[].tool_description_policy`

For the exact field descriptions and defaults, use the [Configuration guide](../configuration.md) and the [Detailed Reference](../configuration/config-reference.md).

For a stricter end-to-end profile, read the [High-Security Scenario](../configuration/high-security.md).

---

## Next steps

- **Previous topic:** [Auth And Session Boundaries](auth-and-session-boundaries.md) - how the adapter treats auth, signed URLs, and session reuse.
- **Next:** [Tool Definition Pinning](tool-definition-pinning.md) - how the session protects itself from mid-session catalog drift.
- **See also:** [High-Security Scenario](../configuration/high-security.md) - a practical hardening overlay.
