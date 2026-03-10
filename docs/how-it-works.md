# How It Works

**What you'll learn here:** how tool categories are wired from config, and what request/response flow looks like for uploads and artifacts.

---

## Tool categories

Tool behavior is defined explicitly by `servers[].adapters[]`. Nothing is inferred from tool names.

### Upload consumer

`upload_consumer` tools receive `upload://` handles in the configured argument path. Before forwarding upstream, the adapter resolves each handle to the staged filesystem path for that same session.

### Artifact producer

`artifact_producer` tools have two possible capture paths:

- If `output_path_argument` is configured, the adapter allocates a session-scoped artifact path before the upstream call and injects that path into the tool arguments.
- If the tool cannot write directly to a supplied path, or ignores it, the adapter falls back to post-call recovery using `output_locator.mode`:
  - `regex`
  - `structured`
  - `embedded`
  - `none`

After capture, the adapter finalizes artifact metadata and exposes an `artifact://` URI in `meta.artifact.artifact_uri`. When HTTP downloads are enabled, the same tool result also includes `meta.artifact.download_url`.

### Passthrough

Any tool not listed in an adapter entry is passthrough and is proxied without modification.

---

## Adapter wiring

At startup, the adapter fetches upstream tools (`list_tools`) and applies wrappers for configured entries.

- Upload helper tools are added only when that server has `upload_consumer` adapters and `uploads.enabled: true`.
- If a configured tool is missing upstream, wiring remains incomplete and `/healthz` reports `adapter_wiring_incomplete`.

See [Configuration](configuration.md) and [Config Reference](configuration/config-reference.md) for exact fields.

---

## Request flow

```mermaid
sequenceDiagram
  participant C as MCP Client (Agent)
  participant R as Remote MCP Adapter
  participant U as Upstream MCP Server
  participant S as Shared Storage

  Note over C,U: 1) Passthrough tool call
  C->>R: MCP tool call
  alt Tool is not adapter-wrapped
    R->>U: Forward call unchanged
    U-->>R: Tool result
    R-->>C: Result unchanged
  end

  Note over C,U: 2) Upload staging path (only when uploads are enabled and the server has upload_consumer tools)
  C->>R: MCP tool call <server_id>_get_upload_url()
  R-->>C: upload_url + Mcp-Session-Id header + TTL + examples
  C->>R: HTTP POST multipart upload (one or many files)
  R->>S: Persist staged file(s) under /shared/uploads/...
  alt Any file in the batch fails
    R->>S: Roll back successful files from the same batch
    R-->>C: HTTP error
  else Batch succeeds
    R-->>C: upload:// handle(s) + per-file metadata
  end

  Note over C,U: 3) upload_consumer tool call
  C->>R: upload_consumer tool call with upload:// handles
  R->>S: Resolve handles to /shared/uploads/... (session scoped validation)
  R->>U: Forward tool call with rewritten file path args
  U-->>R: Tool result
  R-->>C: Tool result unchanged by upload_consumer logic

  Note over C,U: 4) artifact_producer tool call
  C->>R: artifact_producer tool call
  alt output_path_argument is configured
    R->>S: Allocate session-scoped artifact path under /shared/artifacts/...
    R->>U: Forward call with injected output path
  else No output_path_argument
    R->>U: Forward call unchanged
  end

  U-->>R: Tool result (may include path text, structured path, or embedded bytes)

  alt Artifact file already exists at injected path
    R->>S: Finalize artifact metadata
  else Adapter must recover or materialize the file
    R->>S: Locate, copy, or materialize file under /shared/artifacts/...
    R->>S: Finalize artifact metadata
  end

  R-->>C: Tool result + meta.artifact.artifact_uri
  opt allow_raw_output=true
    R-->>C: Extra raw artifact content block
  end
  opt allow_artifacts_download=true
    R-->>C: meta.artifact.download_url + plain-text download link
  end

  opt artifact resources are enabled globally and for this adapter
    C->>R: MCP resource read artifact://sessions/{sid}/{artifact_id}/{filename}
    R->>S: Read artifact bytes or text with session validation
    R-->>C: Resource contents
  end
```

---

## Session mapping note

The adapter keeps an upstream session per client session. If upstream session termination occurs, the adapter retries according to `sessions.upstream_session_termination_retries`.

---

## Next steps

- **Next:** [Configuration](configuration.md) - move from concepts to actual config structure.
- **See also:** [Core Concepts](core-concepts.md) - user-facing model.
- **See also:** [Config Reference](configuration/config-reference.md) - complete field reference.
