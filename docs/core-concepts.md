# Core Concepts

Three things underpin everything the adapter does: sessions, upload handles, and artifact URIs. Once you have a mental model for these, the rest of the config and behavior makes a lot more sense.

---

## Sessions

Every connection is scoped by `Mcp-Session-Id`. The adapter treats that session ID as the isolation boundary for uploads, artifacts, and quotas.

If a session expires (idle TTL or teardown), its staged uploads and artifacts are cleaned up by background cleanup.

Example request header:

```http
POST /mcp/playwright HTTP/1.1
Mcp-Session-Id: a3f8c2d1-7b4e-4f9a-85cc-0e3d1f6a9b12
Content-Type: application/json
```

---

## File inputs: `upload://` handles

### Why local paths fail remotely

A path like `/Users/me/report.pdf` exists on the client machine, not on the upstream MCP server machine. Remote upstream tools cannot read it directly, and there is no automatic magic that copies the file across.

The fix is to stage the file first, then pass an `upload://` handle to the tool.

### Upload workflow

If a server has at least one `upload_consumer` adapter and `uploads.enabled: true`, the adapter registers a helper tool named `<server_id>_get_upload_url` (for example `playwright_get_upload_url`).

The client flow is:

1. Call `<server_id>_get_upload_url(...)`.
2. POST file(s) to the returned upload URL.
3. Receive `upload://` handle(s).
4. Pass those handles in the configured tool argument.

Handle format:

```text
upload://sessions/<session-id>/<upload-id>
```

Example:

```text
upload://sessions/a3f8c2d1-7b4e-4f9a-85cc-0e3d1f6a9b12/7f3c81a0-2b4d-4e6f
```

---

## File outputs: `artifact://` URIs

### What artifact capture does

For tools configured as `artifact_producer`, the adapter captures produced files into session-scoped artifact storage and registers them as MCP resources.

The adapter returns artifact metadata under `meta.artifact` (including `artifact_uri`). It does not depend on rewriting arbitrary text in the tool output.

Artifact URI format:

```text
artifact://sessions/<session-id>/<artifact-id>/<filename>
```

Example:

```text
artifact://sessions/a3f8c2d1-7b4e-4f9a-85cc-0e3d1f6a9b12/c91d3e7f-5a2b-4c8d/screenshot.png
```

Clients can call `resources/read` on that URI to fetch bytes. If `core.allow_artifacts_download` is enabled, artifact metadata may also include `download_url`.

---

## Passthrough tools

Any tool not listed in `upload_consumer` or `artifact_producer` config is passthrough. The adapter forwards arguments and returns results unchanged. Most tools fall into this bucket.

---

## Next steps

- **Next:** [How It Works](how-it-works.md) — internal request and wiring flow, with a full sequence diagram.
- **See also:** [Configuration](configuration.md) — how to map tools to adapter types.
