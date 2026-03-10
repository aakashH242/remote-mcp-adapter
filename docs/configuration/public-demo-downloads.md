# Public Demo Downloads Scenario

**What you'll learn here:** how to configure the adapter for a browser-facing or externally shared demo, why `public_base_url` and signed links matter together, and which settings make artifact links easy for humans to click without turning the whole service into an anonymous free-for-all.

---

## What this scenario is for

This profile is for demos where people outside your immediate operator circle need to **open links and see results easily**.

Typical examples:

- a public product demo with a browser front end
- a hosted showcase environment for prospects or evaluators
- a demo page where screenshot or PDF results need to open directly in a browser
- a live environment where a human may click artifact links from a UI rather than through an MCP client

This scenario is about balancing convenience and containment. The service is meant to feel smooth to the audience, but it still needs deliberate access control.

---

## What this scenario assumes

A public-demo deployment usually assumes:

- the adapter is reachable through a stable public URL
- MCP calls should still require auth
- upload and artifact links need to be externally routable
- humans may click returned links directly in a browser
- signed URLs are preferable to asking browsers or demo viewers to supply the adapter auth header manually

If people are going to click links from a demo page, a chat transcript, or a presentation flow, this profile is the right mental model.

---

## Recommended knobs and values

### Core

```yaml
core:
  public_base_url: "https://demo.example.com"
  allow_artifacts_download: true
  log_level: "info"
```

Why:

- `public_base_url` is non-negotiable here because returned upload and download links must point at the real public address
- `allow_artifacts_download: true` enables HTTP artifact links in tool results
- `log_level: "info"` makes demo issues easier to diagnose without living in debug logs

If you leave `public_base_url` unset in this kind of deployment, the adapter may generate links that point at an internal hostname, container IP, or some other address the audience cannot reach.

### Auth and signed URLs

```yaml
core:
  auth:
    enabled: true
    token: "${MCP_ADAPTER_TOKEN}"
    signed_upload_ttl_seconds: 300
    signing_secret: "${MCP_ADAPTER_SIGNING_SECRET}"
```

Why:

- `auth.enabled: true` keeps MCP and normal API access protected
- signed upload URLs let demo clients upload files without exposing the main auth token in browser flows
- when artifact download signing is active, returned `download_url` links can be clicked directly without manually attaching the adapter auth header
- a separate `signing_secret` makes rotation and operational cleanup easier

This is one of the biggest advantages of the adapter for demo environments: the service can stay protected while the human-facing links stay usable.

### CORS

If a browser app talks directly to the adapter, be explicit.

```yaml
core:
  cors:
    enabled: true
    allowed_origins:
      - "https://demo.example.com"
    allowed_methods: ["POST", "GET", "OPTIONS"]
    allowed_headers: ["*"]
    allow_credentials: false
```

Why:

- browser-facing demos often need CORS even when ordinary agent integrations do not
- it is safer to list the demo origin explicitly than to use `*`
- this keeps the browser story intentional rather than accidental

### Uploads and artifacts

```yaml
uploads:
  enabled: true
  max_file_bytes: "25Mi"
  ttl_seconds: 300
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 1800
  max_per_session: 20
  expose_as_resources: true
```

Why:

- demo users still need real uploads and artifact results
- `require_sha256: true` is worth keeping in a public-facing environment
- moderate TTLs strike a decent balance between convenience and cleanup
- `max_per_session` keeps a popular demo from quietly filling the disk

### Sessions and limits

```yaml
sessions:
  max_active: 50
  idle_ttl_seconds: 900
  max_total_session_size: "250Mi"
```

Why:

- public demos can attract bursty usage
- finite limits matter more in an environment where you do not fully control user behavior
- shorter idle TTLs help abandoned sessions disappear quickly

---

## Full example

```yaml
core:
  public_base_url: "https://demo.example.com"
  allow_artifacts_download: true
  log_level: "info"

  auth:
    enabled: true
    token: "${MCP_ADAPTER_TOKEN}"
    signed_upload_ttl_seconds: 300
    signing_secret: "${MCP_ADAPTER_SIGNING_SECRET}"

  cors:
    enabled: true
    allowed_origins:
      - "https://demo.example.com"
    allowed_methods: ["POST", "GET", "OPTIONS"]
    allowed_headers: ["*"]
    allow_credentials: false

sessions:
  max_active: 50
  idle_ttl_seconds: 900
  max_total_session_size: "250Mi"

uploads:
  enabled: true
  max_file_bytes: "25Mi"
  ttl_seconds: 300
  require_sha256: true

artifacts:
  enabled: true
  ttl_seconds: 1800
  max_per_session: 20
  expose_as_resources: true
```

---

## Why this profile feels better for demos

Compared with a generic production config, this profile makes a demo behave more like people expect:

- links open at the right public hostname
- uploaded files do not require teaching the audience about raw auth headers
- artifact downloads can be clicked directly from returned results
- the demo still has finite limits and cleanup behavior

In other words, it feels polished without becoming reckless.

---

## Common public-demo mistakes

!!! warning "Turning on artifact downloads without setting `public_base_url`"
  The links may exist, but they will point somewhere useless.

!!! warning "Making the whole demo anonymous because links need to be clickable"
  Signed URLs already solve most of that problem. You usually do not need to drop auth entirely.

!!! warning "Using wildcard CORS for convenience"
  Public demos are exactly where you should be more explicit, not less.

!!! warning "Forgetting that popularity changes capacity pressure"
  A nice public demo can attract more uploads and artifacts than you expect. Set finite session and storage limits.

---

## Next steps

- **Back to:** [Configuration](../configuration.md) — overview and scenario index.
- **Previous scenario:** [Agent-Optimized Code Mode Scenario](agent-optimized-code-mode.md) — compact discovery surface for coding agents.
- **See also:** [Security](../security.md) — how signed upload and download URLs behave.
- **See also:** [Detailed Reference](config-reference.md) — exact fields for `public_base_url`, auth signing, CORS, and artifact downloads.
- **Next scenario:** [Private Demo Links Scenario](private-demo-links.md) — similar human-friendly links, but for a more controlled internal audience.
