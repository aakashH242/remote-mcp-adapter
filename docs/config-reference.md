# Config Reference

**What you'll learn here:** every configuration field, its default, and when you actually need to change it.

---

## How to read this page

Most fields have safe defaults and you can ignore them until you hit a specific need. Each section below starts with a short paragraph explaining when that section matters and which knobs people most commonly change. Use the tables for the details.

The full template with inline comments lives in [`config.yaml.template`](https://github.com/aakashH242/remote-mcp-adapter/blob/main/config.yaml.template) in the repository.

---

## `core`

The `core` section controls how the adapter process itself behaves: what address it binds to, what log level it uses, how long it waits for upstreams at startup, and a few global feature flags. Most deployments only need to change `port`, `log_level`, and `allow_artifacts_download`.

| Field | Default | Purpose |
|---|---|---|
| `host` | `0.0.0.0` | IP address to bind to. Use `127.0.0.1` behind a reverse proxy. |
| `port` | `8932` | TCP port. |
| `log_level` | `warning` | One of `debug`, `info`, `warning`, `error`, `critical`. |
| `max_start_wait_seconds` | `60` | Startup wait for upstreams before degraded mode. Set `0` to skip. |
| `cleanup_interval_seconds` | `60` | How often expired uploads, artifacts, and idle sessions are removed. |
| `public_base_url` | `null` | Override the external URL used in upload endpoint responses. Set this when behind a reverse proxy. |
| `allow_artifacts_download` | `false` | Register `GET /artifacts/...` download endpoint. Required if clients need direct HTTP download links. |
| `upload_path` | `/upload` | Base path for the multipart file upload endpoint. |
| `upstream_metadata_cache_ttl_seconds` | `300` | How long `list_tools` and `list_resources` results are cached. Lower this if your upstream adds tools dynamically. |

---

### `core.auth`

Auth is disabled by default. When you enable it, MCP requests require the configured header/token. Upload and artifact download endpoints can also use signed URL credentials when enabled. See [Security](security.md) for exact route behavior.

| Field | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Enforce bearer token auth. |
| `header_name` | `X-Mcp-Adapter-Auth-Token` | HTTP header clients must include. |
| `token` | `null` | The secret token value. Use `${ENV_VAR}` — never commit the value. |
| `signed_upload_ttl_seconds` | `120` | Lifetime of HMAC-signed upload URLs in seconds. |
| `signing_secret` | `null` | Separate HMAC secret for signing upload URLs. Falls back to `token` if not set. |

---

### `core.cors`

CORS is only needed when a browser-based client makes direct HTTP requests to the adapter. Most agent or IDE use cases do not require it.

| Field | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Add CORS headers. |
| `allowed_origins` | `[]` | Origins allowed for cross-origin requests. |
| `allowed_methods` | `["POST","GET","OPTIONS"]` | HTTP methods permitted in CORS pre-flight. |
| `allowed_headers` | `["*"]` | Headers permitted in cross-origin requests. |
| `allow_credentials` | `false` | Allow cookies/credentials in cross-origin requests. |

---

### `core.defaults`

These are the global defaults for tool call behavior. Any value here can be overridden per-server in `servers[].tool_defaults` or per-adapter in `adapters[].overrides`.

| Field | Default | Purpose |
|---|---|---|
| `tool_call_timeout_seconds` | `60` | Global timeout for upstream tool calls. |
| `allow_raw_output` | `false` | Include base64-encoded file bytes in artifact tool responses. |

---

### `core.upstream_ping`

The adapter runs an active health ping loop for each upstream server. When a server fails `failure_threshold` consecutive pings, the circuit breaker opens and the adapter returns errors immediately rather than waiting for the upstream timeout. You can tune the defaults here and override per-server.

| Field | Default | Purpose |
|---|---|---|
| `enabled` | `true` | Run active health ping loop per upstream. |
| `interval_seconds` | `15` | Seconds between consecutive pings. |
| `timeout_seconds` | `5` | Ping response timeout. |
| `failure_threshold` | `3` | Consecutive failures to trip the circuit breaker. |
| `open_cooldown_seconds` | `30` | Seconds the breaker stays open before half-open probes. |
| `half_open_probe_allowance` | `2` | Successful probes needed to close the breaker. |

---

## `storage`

The `storage` section controls where files live on disk. You almost always need to change `root` to point at a mounted volume. The other defaults are safe for single-node use. For multi-worker deployments, review `lock_mode`.

| Field | Default | Purpose |
|---|---|---|
| `root` | `/data/shared` | Root directory for uploads and artifacts. Mount a persistent volume here. |
| `max_size` | `null` | Global storage cap (e.g. `50Gi`). |
| `atomic_writes` | `true` | Write to temp, fsync, then rename. Prevents torn files on crash. |
| `lock_mode` | `auto` | Write concurrency: `none` / `process` / `file` / `redis` / `auto`. `auto` picks `file` unless Redis is configured. |
| `orphan_sweeper_enabled` | `true` | Clean up files that have no matching metadata record. |
| `orphan_sweeper_grace_seconds` | `300` | Minimum file age before orphan deletion (avoids racing with in-flight writes). |
| `artifact_locator_policy` | `storage_only` | Where artifact files can be sourced from. Change to `allow_configured_roots` only with explicit `artifact_locator_allowed_roots`. |
| `artifact_locator_allowed_roots` | `[]` | Additional filesystem roots the artifact locator may read (required when policy is `allow_configured_roots`). |

---

## `sessions`

Sessions keep per-client state isolated. The defaults impose no hard limits — all quota fields are `null` by default. Change them when you are running a shared multi-tenant deployment where you need to cap resource usage.

| Field | Default | Purpose |
|---|---|---|
| `max_active` | `null` | Max concurrent sessions. HTTP 429 when exceeded. |
| `max_in_flight_per_session` | `null` | Max simultaneous tool calls per session. |
| `idle_ttl_seconds` | `null` | Inactivity timeout before session expiry. |
| `allow_revival` | `true` | Keep expired session tombstones for transparent reconnect. |
| `tombstone_ttl_seconds` | `86400` | How long tombstoned session metadata is kept. |
| `upstream_session_termination_retries` | `1` | Auto-retry count when upstream terminates the session (0–5). |
| `max_total_session_size` | `null` | Per-session storage quota (uploads + artifacts combined). |
| `eviction_policy` | `lru_uploads_then_artifacts` | What gets evicted first when session quota is hit. |

---

## `uploads`

These fields control how staged upload files are validated and how long they are kept. The defaults are suitable for normal interactive use.

| Field | Default | Purpose |
|---|---|---|
| `enabled` | `true` | Master switch for upload functionality. |
| `max_file_bytes` | `10Mi` | Max size per uploaded file. Accepts `10M`, `50Ki`, etc. |
| `ttl_seconds` | `120` | How long staged files are kept before auto-cleanup. |
| `require_sha256` | `false` | Require clients to provide `sha256` multipart form fields (one per uploaded `file` part, same order) and verify each digest before accepting the upload. |
| `uri_scheme` | `upload://` | Scheme used in upload handles returned to clients. |

---

## `artifacts`

These fields control how tool output files are stored and exposed. `ttl_seconds` is the most commonly changed field.

| Field | Default | Purpose |
|---|---|---|
| `enabled` | `true` | Master switch for artifact capture. |
| `ttl_seconds` | `600` | How long artifacts are kept before auto-cleanup. |
| `max_per_session` | `null` | Cap on artifacts per session. |
| `expose_as_resources` | `true` | Register artifacts as standard MCP resources with `artifact://` URIs. |
| `uri_scheme` | `artifact://` | Scheme used in artifact resource URIs. |

---

## `state_persistence`

Session, upload, and artifact **metadata** (not the files themselves) are stored here. The default `disk` backend uses SQLite and is suitable for a single process or single node. Use `redis` for multi-replica setups.

| Field | Default | Purpose |
|---|---|---|
| `type` | `disk` | Backend: `memory`, `disk` (SQLite), or `redis`. |
| `refresh_on_startup` | `false` | Discard saved metadata and start fresh. |
| `snapshot_interval_seconds` | `30` | How often in-memory state is saved to disk (only for `type: memory`). |
| `unavailable_policy` | `fail_closed` | What to do when backend is unreachable: `fail_closed`, `exit`, or `fallback_memory`. |

### `state_persistence.disk`

| Field | Default | Purpose |
|---|---|---|
| `local_path` | auto | SQLite file path. Defaults to `{storage.root}/state/adapter_state.sqlite3`. |
| `wal.enabled` | `true` | SQLite WAL mode. Disable only on filesystems that do not support it. |

### `state_persistence.redis`

| Field | Default | Purpose |
|---|---|---|
| `host` | `null` | Redis hostname (required when `type: redis`). |
| `port` | `6379` | Redis TCP port. |
| `db` | `0` | Redis logical database index. |
| `username` | `null` | Redis ACL username (Redis 6+). |
| `password` | `null` | Redis AUTH password. |
| `tls_insecure` | `false` | Skip TLS certificate verification (dev only). |
| `key_base` | `mcp_remote_adapter` | Key namespace prefix. |
| `ping_seconds` | `5` | Health-check ping interval. |

### `state_persistence.reconciliation`

When the adapter starts, it can reconcile storage-root files against the metadata store. This is useful after a crash or migration.

| Field | Default | Purpose |
|---|---|---|
| `mode` | `if_empty` | When to reconcile: `disabled`, `if_empty`, or `always`. |
| `legacy_server_id` | `null` | Server ID to attribute ambiguous legacy files to. |

---

## `telemetry`

All telemetry is off by default. When you enable it, set `endpoint` to your OTLP collector. See [Telemetry](telemetry.md) for a walkthrough.

| Field | Default | Purpose |
|---|---|---|
| `enabled` | `false` | Master switch. |
| `transport` | `grpc` | OTLP protocol: `grpc` or `http`. |
| `endpoint` | auto | OTLP endpoint. Defaults to `localhost:4317` for gRPC. |
| `logs_endpoint` | `null` | Dedicated OTLP log endpoint (HTTP transport only). |
| `insecure` | `true` | Plaintext gRPC. Set `false` for TLS. |
| `headers` | `{}` | Extra OTLP headers (e.g. vendor auth tokens). |
| `emit_logs` | `false` | Export application logs as OTel log records. |
| `service_name` | `remote-mcp-adapter` | OTel `service.name` resource attribute. |
| `service_namespace` | `null` | Optional `service.namespace` attribute. |
| `export_interval_seconds` | `15` | Metrics collection and export cadence. |
| `export_timeout_seconds` | `10` | OTLP exporter operation timeout. |
| `max_queue_size` | `5000` | Internal async event queue size. |
| `queue_batch_size` | `256` | Max events processed per worker drain cycle. |
| `periodic_flush_seconds` | `5` | Force-flush cadence. |
| `shutdown_drain_timeout_seconds` | `10` | Grace period for draining queued events during shutdown. |
| `drop_on_queue_full` | `true` | Drop events instead of blocking when queue is full. |
| `flush_on_shutdown` | `true` | Force flush during normal shutdown. |
| `flush_on_terminate` | `true` | Best-effort drain on interpreter termination. |
| `log_batch_max_queue_size` | `null` | OTel log batch processor queue size (SDK default when null). |
| `log_batch_max_export_batch_size` | `null` | Max log records per OTel batch export (SDK default when null). |
| `log_batch_schedule_delay_millis` | `null` | Delay between scheduled log batch exports (SDK default when null). |
| `log_batch_export_timeout_millis` | `null` | Timeout per log export operation (SDK default when null). |

---

## `servers[]`

This is the only required section. Each entry defines one upstream MCP server and the adapter rules for its tools.

| Field | Default | Purpose |
|---|---|---|
| `id` | *(required)* | Unique slug for this server. Used in URLs, logs, storage paths, and the injected upload tool name. |
| `mount_path` | *(required)* | HTTP path where the adapter accepts MCP requests for this server. |

### `servers[].upstream`

| Field | Default | Purpose |
|---|---|---|
| `url` | *(required)* | URL of the upstream MCP server. |
| `transport` | `streamable_http` | MCP transport: `streamable_http` or `sse`. |
| `insecure_tls` | `false` | Skip TLS cert verification for this upstream (dev only). |
| `static_headers` | `{}` | Static headers injected into every upstream request. |
| `client_headers.required` | `[]` | Client headers that must be present (HTTP 400 if missing). |
| `client_headers.passthrough` | `[]` | Client headers forwarded to upstream when present. |

### `servers[].tool_defaults`

Per-server overrides that apply to all tools on this server (inherits from `core.defaults` when not set).

| Field | Default | Purpose |
|---|---|---|
| `tool_call_timeout_seconds` | `null` | Per-server tool call timeout. |
| `allow_raw_output` | `null` | Per-server raw output override. |

### `servers[].upstream_ping`

Per-server circuit breaker overrides. All fields are `null` by default and inherit from `core.upstream_ping`.

| Field | Default | Purpose |
|---|---|---|
| `enabled` | `null` | Override whether ping monitoring is active for this server. |
| `interval_seconds` | `null` | Override ping interval. |
| `timeout_seconds` | `null` | Override ping timeout. |
| `failure_threshold` | `null` | Override failure count to trip the breaker. |
| `open_cooldown_seconds` | `null` | Override open-state cooldown. |
| `half_open_probe_allowance` | `null` | Override probes needed to close the breaker. |

### `servers[].adapters[]`

Each entry wraps one or more upstream tools. A tool may appear in at most one adapter entry (first match wins).

**`upload_consumer`**

| Field | Default | Purpose |
|---|---|---|
| `tools` | *(required)* | Tool names to intercept. |
| `file_path_argument` | *(required)* | Argument name that carries the `upload://` URI. Supports dot-path notation (e.g. `options.file`). |
| `uri_scheme` | `upload://` | Expected URI scheme in argument values. |
| `uri_prefix` | `null` | When `true`, converts the resolved path to a `file://` URI before forwarding. |
| `overrides.tool_call_timeout_seconds` | `null` | Per-adapter timeout override. |
| `overrides.allow_raw_output` | `null` | Per-adapter raw output override. |

**`artifact_producer`**

| Field | Default | Purpose |
|---|---|---|
| `tools` | *(required)* | Tool names to intercept. |
| `output_path_argument` | `null` | Argument to inject the pre-allocated output path into before calling upstream. |
| `output_locator.mode` | `none` | How to find the output file: `structured`, `regex`, `embedded`, or `none`. |
| `output_locator.output_path_key` | `null` | Dot-path into structured result (required for `structured` mode). |
| `output_locator.output_path_regexes` | `[]` | Custom regex patterns for `regex` mode. Built-in defaults used when empty. |
| `persist` | `true` | Store the file in the artifact store. |
| `expose_as_resource` | `true` | Register as a session-scoped MCP resource. |
| `allow_raw_output` | `null` | Embed base64 file bytes in the tool response. |
| `overrides.tool_call_timeout_seconds` | `null` | Per-adapter timeout override. |
| `overrides.allow_raw_output` | `null` | Per-adapter raw output override. |

---

## Next steps

- **See also:** [Configuration](configuration.md) — practical guide with examples.
