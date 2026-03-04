# How It Works

**What you'll learn here:** how tool categories are wired from config, and what request/response flow looks like for uploads and artifacts.

---

## Tool categories

Tool behavior is defined explicitly by `servers[].adapters[]`. Nothing is inferred from tool names.

### Upload consumer

`upload_consumer` tools receive `upload://` handles in the configured argument path. Before forwarding upstream, the adapter resolves each handle to the staged filesystem path for that same session.

### Artifact producer

`artifact_producer` tools are called upstream, then the adapter locates output using `output_locator.mode`:

- `regex`
- `structured`
- `embedded`
- `none` (pre-allocated output path via `output_path_argument`)

After capture, the adapter stores artifact metadata and exposes an `artifact://` URI in `meta.artifact.artifact_uri` (with optional `download_url` when enabled).

### Passthrough

Any tool not listed in an adapter entry is passthrough and is proxied without modification.

---

## Adapter wiring

At startup, the adapter fetches upstream tools (`list_tools`) and applies wrappers for configured entries.

- Upload helper tools are added only when that server has `upload_consumer` adapters and `uploads.enabled: true`.
- If a configured tool is missing upstream, wiring remains incomplete and `/healthz` reports `adapter_wiring_incomplete`.

See [Configuration](configuration.md) and [Config Reference](config-reference.md) for exact fields.

---

## Request flow

```mermaid
sequenceDiagram
  participant C as MCP Client (Agent)
  participant R as Remote MCP Adapter
  participant U as Upstream MCP Server
  participant S as Shared Storage

  Note over C,U: 1) Normal tool call path
  C->>R: MCP tool call
  alt Tool is NOT configured as artifact_producer
    R->>U: Forward call unchanged
    U-->>R: Tool result
    R-->>C: Result unchanged
  else Tool IS configured as artifact_producer
    R->>U: Forward call (arg rewrite only if configured)
    U-->>R: Tool result (may include path text or embedded bytes)
    R->>S: Locate/copy/materialize output into /shared/artifacts/...
    R->>S: Register artifact metadata (session scoped)
    R-->>C: Result + artifact://sessions/{sid}/{artifact_id}/{filename}
    C->>R: MCP resource read artifact://...
    R->>S: Read artifact bytes
    R-->>C: Resource contents (optional signed download URL in metadata)
  end

  Note over C,U: 2) Upload staging path (only for upload_consumer tools)
  C->>R: MCP tool call <server_id>_get_upload_url(...)
  R-->>C: Signed upload URL + required headers + expiry
  C->>R: HTTP multipart upload (one or many files)
  R->>S: Persist staged file(s) under /shared/uploads/...
  R-->>C: upload://sessions/{sid}/{upload_id} handle(s)
  C->>R: upload_consumer tool call with upload:// handles
  R->>S: Resolve handles to /shared/uploads/... (session scoped validation)
  R->>U: Forward tool call with rewritten file path args
  U-->>R: Tool result
  R-->>C: Tool result (unchanged)
```

---

## Session mapping note

The adapter keeps an upstream session per client session. If upstream session termination occurs, the adapter retries according to `sessions.upstream_session_termination_retries`.

---

## Next steps

- **See also:** [Core Concepts](core-concepts.md) - user-facing model.
- **See also:** [Configuration](configuration.md) - practical setup.
- **See also:** [Config Reference](config-reference.md) - complete field reference.
