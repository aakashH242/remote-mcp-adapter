# Agent-Optimized Code Mode Scenario

A compact discovery surface for coding agents and smaller models. Reduces tool-list overload while preserving upload and artifact behavior.

---

## What this scenario is for

This profile is for deployments where the main problem is **tool-surface overload**.

Typical examples:

- coding agents that need to choose from many tools across several upstream servers
- smaller models that perform poorly when every raw tool is exposed directly
- environments where faster tool discovery matters more than showing the entire tool list up front
- mixed deployments where only one or two servers should use Code Mode
- teams experimenting with a more compact, agent-first interaction model

This profile is not about security or topology. It is about shaping the MCP surface so agents can reason about it more reliably.

---

## What this scenario assumes

A typical Code Mode deployment assumes:

- at least one upstream server has a tool catalog large enough to be noisy
- your clients are agents, not humans browsing tool lists manually
- you are comfortable with discovery happening through meta-tools such as search and execute
- shorter descriptions may help the model stay focused
- you still want normal adapter features like upload and artifact wrapping when needed

If your client is a coding agent that gets lost in a huge direct tool list, this is one of the most valuable optional profiles in the adapter.

---

## What Code Mode changes

With normal mode, clients see the direct tool list.

With Code Mode, clients instead see a compact discovery surface built around server-prefixed tools such as:

- `<server_id>_search`
- `<server_id>_get_schema`
- `<server_id>_list_tools`
- `<server_id>_tags`
- `<server_id>_execute`

That means the model discovers tools on demand instead of trying to reason over everything all at once.

This tends to help when:

- the upstream catalog is large
- tool names are similar to each other
- the model is weak at initial selection but fine once it has narrowed the candidate set

---

## Recommended knobs and values

### Global Code Mode

```yaml
core:
  code_mode_enabled: true
  shorten_descriptions: true
  short_description_max_tokens: 16
```

`code_mode_enabled: true` enables the compact agent-facing surface globally. `shorten_descriptions: true` keeps upload-consumer descriptions from becoming bloated. `short_description_max_tokens: 16` is a reasonable default for preserving just enough semantic signal.

This is the easiest option when most or all upstream servers benefit from the same agent-oriented presentation.

### Per-server override

If you only want Code Mode on some servers, keep the global default off and enable it selectively.

```yaml
core:
  code_mode_enabled: false
  shorten_descriptions: false

servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    code_mode_enabled: true
    shorten_descriptions: true
    short_description_max_tokens: 20
    upstream:
      url: "http://playwright.internal:8931/mcp"

  - id: "fetch"
    mount_path: "/mcp/fetch"
    code_mode_enabled: false
    upstream:
      url: "http://fetch.internal:8080/mcp"
```

This is the safer choice in mixed environments. Some upstreams have huge tool sets and some do not. It lets you keep human-friendlier direct tools for simpler servers while compressing only the noisy ones.

### Description shaping

```yaml
core:
  shorten_descriptions: true
  short_description_max_tokens: 16
```

This only affects adapter-wrapped upload-consumer descriptions. It helps reduce prompt clutter for models that are easily distracted by long workflow instructions. It pairs naturally with Code Mode, but it can also be useful on its own.

Do not turn this on blindly for every environment. If humans or stronger models rely on detailed upstream descriptions, full descriptions may still be the better choice.

### Upload and artifact behavior

Code Mode does **not** remove adapter features.

- upload-consumer tools still resolve `upload://` handles
- artifact-producer tools still capture files
- tool overrides still win according to the normal precedence rules

That is an important detail: this profile changes the **presentation layer**, not the underlying adapter behavior.

---

## Full example

This example enables Code Mode globally for an agent-heavy deployment.

```yaml
core:
  log_level: "info"
  code_mode_enabled: true
  shorten_descriptions: true
  short_description_max_tokens: 16

  auth:
    enabled: true
    token: "${MCP_ADAPTER_TOKEN}"

servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    upstream:
      transport: "streamable_http"
      url: "http://playwright.internal:8931/mcp"

    adapters:
      - type: "upload_consumer"
        tools: ["browser_file_upload"]
        file_path_argument: "paths"

      - type: "artifact_producer"
        tools: ["browser_take_screenshot", "browser_pdf_save"]
        output_path_argument: "filename"
        output_locator:
          mode: "regex"
```

Here is the more selective version for mixed deployments:

```yaml
core:
  code_mode_enabled: false
  shorten_descriptions: false

servers:
  - id: "playwright"
    mount_path: "/mcp/playwright"
    code_mode_enabled: true
    shorten_descriptions: true
    short_description_max_tokens: 20
    upstream:
      transport: "streamable_http"
      url: "http://playwright.internal:8931/mcp"

  - id: "fetch"
    mount_path: "/mcp/fetch"
    upstream:
      transport: "streamable_http"
      url: "http://fetch.internal:8080/mcp"
```

---

## When this profile works especially well

This profile is a strong fit when:

- the client is a coding agent rather than a person clicking through tools
- the model is small enough that raw tool sprawl hurts tool selection quality
- several upstream tools have overlapping names or overlapping purposes
- you want agents to search, inspect schema, and execute intentionally instead of guessing

It is particularly helpful when the agent is good at iterative discovery but poor at one-shot selection from a huge menu.

---

## When this profile is a bad fit

It is probably the wrong choice when:

- your users are humans manually browsing tools in a UI
- the upstream has a very small and already-clear tool set
- you are debugging tool registration and want to see the direct surface exactly as exposed
- detailed upstream descriptions are more useful than compact summaries

---

## Common Code Mode mistakes

!!! warning "Turning it on everywhere without checking the client"
  Some clients and operators genuinely prefer a direct tool list. Use Code Mode because it helps the client, not because it sounds more advanced.

!!! warning "Expecting description shortening to rewrite everything"
  Description shaping mainly affects adapter-wrapped upload-consumer tools. It is not a universal summarizer for every tool description in the system.

!!! warning "Using it to hide a badly organized deployment"
  Code Mode can reduce overload, but it does not fix ambiguous mount paths, duplicate upstreams, or unclear server boundaries.

!!! warning "Forgetting per-server overrides exist"
  Global enablement is convenient, but mixed deployments often benefit from more selective use.

---

## Next steps

- **Back to:** [Configuration](../configuration.md) — overview and scenario index.
- **Previous scenario:** [High-Observability Scenario](high-observability.md) — visibility-first production overlay.
- **See also:** [Detailed Reference](config-reference.md) — exact fields for `core.code_mode_enabled`, `servers[].code_mode_enabled`, and description shaping.
- **See also:** [How It Works](../how-it-works.md) — understand what the adapter is changing and what it is not.
- **Next scenario:** [Public Demo Downloads Scenario](public-demo-downloads.md) — shape the service for browser-facing demo flows and human-clickable artifact links.
