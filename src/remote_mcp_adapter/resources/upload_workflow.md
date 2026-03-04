# Upload workflow for upload_consumer tools

The upload flow is two-step:

1. Stage file(s) on the adapter server.
2. Pass returned `upload://...` handles into upload_consumer tools.

## Steps

1. Call the server-prefixed helper tool (for example `playwright_get_upload_url`).
2. Use the returned `upload_url` and `headers` (includes `Mcp-Session-Id`) to POST multipart/form-data.
   Repeat the `file` form field to upload multiple files in one request.
   If helper output says `sha256_required=true`, include one `sha256` form field per `file` form field in the same order.
3. Read `upload_handles` from the upload response (`upload_handle` is also present for single-file uploads).
4. Call the configured upload_consumer tool and pass the required `upload://...` handle(s) in the configured path argument.

## Important

- Determine the platform first.
- Use curl.exe on Windows, curl on Linux.
- Do not pass local filesystem paths directly to upload_consumer tools.
- Upload handles are session-scoped and must match the active `Mcp-Session-Id`.
- Staging a file does not upload it to the target website; the tool call does that.
