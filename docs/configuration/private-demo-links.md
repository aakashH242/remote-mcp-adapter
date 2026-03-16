# Private Demo Links Scenario

For guided internal demos behind a VPN or proxy. Balances the need for secure MCP traffic with the reality of humans clicking links in Slack or Jira.

---

## What this scenario is for

This profile is for demos that are **private, but still human-facing**.

Typical examples:

- a sales-engineering demo inside a VPN
- a stakeholder review environment behind a company reverse proxy
- a pre-production environment used for guided walkthroughs
- an internal showcase where links may be opened from chat, docs, tickets, or email

This profile looks a lot like the public-demo profile on paper, but the audience is narrower and the browser story is usually simpler.

---

## What this scenario assumes

A private-demo deployment usually assumes:

- the adapter is still reached through a stable hostname
- auth should remain enabled for normal MCP traffic
- upload and artifact links should be easy for humans to click
- the environment is access-controlled already, but not every person clicking a link is operating through an MCP client
- you want a polished internal experience without treating the service like a public website

This is a very common real-world setup: not public internet, but still not "developer only."

---

## Recommended knobs and values

### Core

```yaml
core:
  public_base_url: "https://mcp-demo.internal.example.com"
  allow_artifacts_download: true
  log_level: "info"
```

`public_base_url` is just as important internally as it is externally when humans click links. `allow_artifacts_download: true` lets returned artifact metadata include usable download links. `log_level: "info"` is a comfortable default for demos that may need quick troubleshooting.

The most common mistake in this kind of environment is assuming the adapter can always guess the right URL because the audience is internal. In practice, internal proxies and split DNS break that assumption all the time.

### Auth and signing

```yaml
core:
  auth:
    enabled: true
    token: "${MCP_ADAPTER_TOKEN}"
    signed_upload_ttl_seconds: 600
    signing_secret: "${MCP_ADAPTER_SIGNING_SECRET}"
```

You still want the adapter protected for ordinary requests. Signed upload URLs make browser-assisted flows easier, and signed download links make it practical for someone to click an artifact URL from a ticket or chat message. A slightly longer signed TTL is often reasonable here because internal demos can be slower and more conversational than public UI flows.

### CORS

Many private demos do **not** need direct browser-origin requests to the adapter.

```yaml
core:
  cors:
    enabled: false
```

If the human is only clicking returned links, CORS may be irrelevant. Keeping it off by default is simpler and safer, and you can still enable it later if you introduce a direct browser client.

### Sessions, uploads, and artifacts

```yaml
sessions:
  max_active: 25
  idle_ttl_seconds: 1800
  max_total_session_size: "500Mi"

uploads:
  enabled: true
  max_file_bytes: "50Mi"
  ttl_seconds: 600
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 3600
  max_per_session: 25
  expose_as_resources: true
```

Private demos often involve fewer people than public showcases, so the limits can be a little less aggressive. A longer upload TTL is often useful when the demo includes human pauses, explanation, or back-and-forth. Artifact links remain available long enough for stakeholders to open them without rushing.

### Topology

This profile works well on top of either:

- a single-node durable deployment, or
- a distributed production deployment

The key point is not the topology. It is that the generated links must be reliable and human-usable.

---

## Full example

```yaml
core:
  public_base_url: "https://mcp-demo.internal.example.com"
  allow_artifacts_download: true
  log_level: "info"

  auth:
    enabled: true
    token: "${MCP_ADAPTER_TOKEN}"
    signed_upload_ttl_seconds: 600
    signing_secret: "${MCP_ADAPTER_SIGNING_SECRET}"

  cors:
    enabled: false

sessions:
  max_active: 25
  idle_ttl_seconds: 1800
  max_total_session_size: "500Mi"

uploads:
  enabled: true
  max_file_bytes: "50Mi"
  ttl_seconds: 600
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 3600
  max_per_session: 25
  expose_as_resources: true
```

---

## Why this profile works well for internal demos

It gives you a nice middle ground:

- the service is still protected
- humans can still click links without learning adapter internals
- internal proxies and hostnames are handled correctly
- the demo can breathe a little more than a public showcase

That combination is often exactly what internal reviews and stakeholder sessions need.

---

## Common private-demo mistakes

!!! warning "Assuming internal means self-explanatory"
  Internal environments can be just as confusing as public ones if the links point at the wrong hostname or expire too quickly.

!!! warning "Keeping signed TTLs too short for guided demos"
  People pause, ask questions, and revisit steps. A very short expiry window can make the demo feel flaky.

!!! warning "Enabling CORS by habit"
  If browsers are only opening returned links, you may not need direct cross-origin requests at all.

!!! warning "Treating private access as a reason to skip auth"
  Private networks reduce exposure. They do not remove it.

---

## Next steps

- **Back to:** [Configuration](../configuration.md) — overview and scenario index.
- **Previous scenario:** [Public Demo Downloads Scenario](public-demo-downloads.md) — broader browser-facing and externally shared demo flow.
- **See also:** [Security](../security/index.md) — auth behavior and signed URL rules.
- **See also:** [Detailed Reference](config-reference.md) — exact fields for `public_base_url`, signed TTLs, and artifact download behavior.
- **Next topic:** [Security](../security/index.md) — move from scenario patterns into route protection, auth headers, and signed credentials.
