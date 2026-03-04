# Telemetry

**What you'll learn here:** what observability signals the adapter emits, how to enable them, and what to look for once your collector is receiving data.

---

## Signals

The adapter emits two types of OpenTelemetry signals:

- **Metrics** — counters, histograms, and gauges tracking requests, tool calls, uploads, artifacts, circuit breaker state, session lifecycle, and cleanup activity. These are always available when telemetry is enabled.
- **Logs** — optionally, application log records can be forwarded as OTel log records to your collector. This is controlled separately by `telemetry.emit_logs`. It requires HTTP transport when using a dedicated `logs_endpoint`.

Distributed traces are not currently emitted. Do not configure a trace exporter — there is nothing to receive.

---

## Enabling telemetry

Telemetry is off by default. To enable it, add a `telemetry` section to your `config.yaml` pointing at your OTLP collector:

```yaml
telemetry:
  enabled: true
  transport: "grpc"
  endpoint: "http://otel-collector:4317"
  insecure: true
  service_name: "remote-mcp-adapter"
  export_interval_seconds: 15
```

For HTTP transport (useful with managed observability platforms that accept OTLP/HTTP):

```yaml
telemetry:
  enabled: true
  transport: "http"
  endpoint: "https://otel.example.com/v1/metrics"
  insecure: false
  headers:
    Authorization: "Bearer ${OTEL_API_TOKEN}"
  emit_logs: true
  logs_endpoint: "https://otel.example.com/v1/logs"
```

The adapter lazy-imports the OpenTelemetry SDK at startup. If the SDK is not installed, telemetry is silently disabled at runtime with a log warning. Make sure `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc` (or `-http`), and `opentelemetry-api` are in your environment.

---

## What to look for

Once data is flowing, these metrics give you the most useful operational picture:

**Request throughput and latency**

`adapter_http_requests_total` counts every HTTP request handled by the adapter, labelled by server, HTTP method, route group, and response status class. `adapter_http_request_duration_seconds` is the matching latency histogram. Sudden spikes in 5xx responses or elevated p99 latency here usually point to an upstream problem.

**Upstream tool call performance**

`adapter_upstream_tool_calls_total` counts proxied calls by server, tool name, and outcome (`ok` or the error type). `adapter_upstream_tool_call_duration_seconds` histograms the round-trip time. Use these to find slow or flaky tools on specific upstream servers.

**Circuit breaker state**

`adapter_upstream_circuit_breaker_state` is a gauge per server (0 = closed, 1 = half-open, 2 = open). An `open` state means the adapter is rejecting all calls to that server without trying to reach it. Alert on this metric if you need to know when an upstream becomes unavailable.

`adapter_upstream_ping_total` counts health pings by result. A rising `failure` count is an early warning before the breaker opens.

**Upload activity**

`adapter_upload_batches_total`, `adapter_upload_files_total`, and `adapter_upload_bytes_total` track staged file volume. `adapter_upload_failures_total` counts rejections by reason (size exceeded, expired nonce, etc.).

**Artifact downloads**

`adapter_artifact_downloads_total` counts resource-read and HTTP download requests for artifacts. `adapter_artifact_download_bytes_total` and `adapter_artifact_download_duration_seconds` give volume and latency.

**Cleanup**

`adapter_cleanup_cycles_total` and `adapter_cleanup_removed_records_total` confirm that the background cleanup loop is running and removing expired records. A stalled cleanup loop (no cycles for several minutes) usually means the process is overloaded.

**Session lifecycle**

`adapter_sessions_lifecycle_total` counts session create, expire, and revival transitions. Use this to understand session churn in multi-user deployments.

---

## Metric catalog

The following table lists every metric name emitted by the adapter.

!!! note
    This catalog must be kept in sync with `src/remote_mcp_adapter/telemetry/otel_bootstrap.py`. If the code changes, this table may become stale.

| Metric | Type | Description |
|---|---|---|
| `adapter_http_requests_total` | Counter | Total HTTP requests by server, method, route group, and status class |
| `adapter_http_request_duration_seconds` | Histogram | HTTP request latency |
| `adapter_upload_batches_total` | Counter | Upload batches accepted |
| `adapter_upload_files_total` | Counter | Files accepted by upload endpoint |
| `adapter_upload_bytes_total` | Counter | Total bytes persisted by upload endpoint |
| `adapter_auth_rejections_total` | Counter | Auth-related rejections by reason and route group |
| `adapter_upstream_tool_calls_total` | Counter | Proxied upstream tool calls by tool name and outcome |
| `adapter_upstream_tool_call_duration_seconds` | Histogram | Upstream tool call latency |
| `adapter_upstream_ping_total` | Counter | Active upstream pings by result |
| `adapter_upstream_ping_latency_seconds` | Histogram | Upstream ping latency |
| `adapter_upstream_circuit_breaker_state` | Gauge | Circuit breaker state per server (0=closed, 1=half_open, 2=open) |
| `adapter_persistence_policy_transitions_total` | Counter | Persistence policy transitions by action and source |
| `adapter_nonce_operations_total` | Counter | Upload nonce operations by backend and result |
| `adapter_upload_credentials_total` | Counter | Signed upload credential issue/validate outcomes |
| `adapter_artifact_downloads_total` | Counter | Artifact download attempts by result |
| `adapter_artifact_download_bytes_total` | Counter | Total bytes served by artifact download endpoint |
| `adapter_artifact_download_duration_seconds` | Histogram | Artifact download latency |
| `adapter_upload_failures_total` | Counter | Upload endpoint failures by reason |
| `adapter_request_rejections_total` | Counter | Non-auth rejections by reason and route group |
| `adapter_adapter_wiring_runs_total` | Counter | Adapter wiring pass outcomes |
| `adapter_adapter_wiring_not_ready_servers` | Gauge | Number of servers not yet wired after last wiring run |
| `adapter_cleanup_cycles_total` | Counter | Completed cleanup cycles by outcome |
| `adapter_cleanup_removed_records_total` | Counter | Records/files removed per cleanup cycle by bucket |
| `adapter_sessions_lifecycle_total` | Counter | Session lifecycle transitions |

---

## Next steps

- **See also:** [Configuration](configuration.md) — add the telemetry block to your config.
- **See also:** [Config Reference](config-reference.md) — all `telemetry.*` fields.
- **See also:** [Health](health.md) — the health endpoint for operational diagnostics.
