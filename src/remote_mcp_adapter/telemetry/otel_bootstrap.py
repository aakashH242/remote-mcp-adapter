"""OpenTelemetry bootstrap helpers for AdapterTelemetry."""

from __future__ import annotations

import importlib
import logging
from typing import Any


def build_metric_exporter(config):
    """Build OTLP metric exporter from telemetry config.

    Args:
        config: Telemetry configuration section.
    """
    headers = dict(config.headers)
    timeout = config.export_timeout_seconds
    if config.transport == "http":
        exporter_mod = importlib.import_module("opentelemetry.exporter.otlp.proto.http.metric_exporter")
        otlp_metric_exporter = getattr(exporter_mod, "OTLPMetricExporter")
        return otlp_metric_exporter(endpoint=config.endpoint, headers=headers, timeout=timeout)

    exporter_mod = importlib.import_module("opentelemetry.exporter.otlp.proto.grpc.metric_exporter")
    otlp_metric_exporter = getattr(exporter_mod, "OTLPMetricExporter")
    return otlp_metric_exporter(
        endpoint=config.endpoint,
        headers=headers,
        timeout=timeout,
        insecure=config.insecure,
    )


def initialize_metrics_backend(*, config):
    """Initialize metrics modules, resource, meter provider, and meter.

    Args:
        config: Telemetry configuration section.

    Returns:
        Tuple of ``(metrics_api, meter_provider, resource, meter)``.
    """
    metrics_api = importlib.import_module("opentelemetry.metrics")
    resources_mod = importlib.import_module("opentelemetry.sdk.resources")
    metrics_sdk_mod = importlib.import_module("opentelemetry.sdk.metrics")
    metrics_export_mod = importlib.import_module("opentelemetry.sdk.metrics.export")

    service_name_attr = getattr(resources_mod, "SERVICE_NAME")
    service_namespace_attr = getattr(resources_mod, "SERVICE_NAMESPACE")
    resource_attributes = {service_name_attr: config.service_name}
    if config.service_namespace:
        resource_attributes[service_namespace_attr] = config.service_namespace
    resource = resources_mod.Resource.create(resource_attributes)

    metric_exporter = build_metric_exporter(config)
    metric_reader = metrics_export_mod.PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=config.export_interval_seconds * 1000,
        export_timeout_millis=config.export_timeout_seconds * 1000,
    )
    meter_provider = metrics_sdk_mod.MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics_api.set_meter_provider(meter_provider)
    meter = metrics_api.get_meter("remote_mcp_adapter", "0.1.0")
    return metrics_api, meter_provider, resource, meter


def create_metric_instruments(*, meter) -> dict[str, Any]:
    """Create all telemetry instruments and return by manager attribute name.

    Args:
        meter: OTel ``Meter`` for creating instruments.

    Returns:
        Dict mapping private attribute names to instrument instances.
    """
    return {
        "_http_requests_total": meter.create_counter(
            "adapter_http_requests_total",
            unit="1",
            description="Total HTTP requests handled by the adapter.",
        ),
        "_http_request_duration": meter.create_histogram(
            "adapter_http_request_duration_seconds",
            unit="s",
            description="HTTP request latency measured at the adapter boundary.",
        ),
        "_upload_batches_total": meter.create_counter(
            "adapter_upload_batches_total",
            unit="1",
            description="Number of upload batches accepted by upload endpoint.",
        ),
        "_upload_files_total": meter.create_counter(
            "adapter_upload_files_total",
            unit="1",
            description="Number of files accepted by upload endpoint.",
        ),
        "_upload_bytes_total": meter.create_counter(
            "adapter_upload_bytes_total",
            unit="By",
            description="Total uploaded bytes persisted by adapter.",
        ),
        "_auth_rejections_total": meter.create_counter(
            "adapter_auth_rejections_total",
            unit="1",
            description="Auth-related request rejections by reason.",
        ),
        "_upstream_tool_calls_total": meter.create_counter(
            "adapter_upstream_tool_calls_total",
            unit="1",
            description="Total proxied upstream tool calls by outcome.",
        ),
        "_upstream_tool_call_duration": meter.create_histogram(
            "adapter_upstream_tool_call_duration_seconds",
            unit="s",
            description="Latency of upstream tool calls made by adapter overrides.",
        ),
        "_upstream_ping_total": meter.create_counter(
            "adapter_upstream_ping_total",
            unit="1",
            description="Total active upstream pings by result.",
        ),
        "_upstream_ping_latency": meter.create_histogram(
            "adapter_upstream_ping_latency_seconds",
            unit="s",
            description="Latency of active upstream ping probes.",
        ),
        "_circuit_breaker_state": meter.create_gauge(
            "adapter_upstream_circuit_breaker_state",
            unit="1",
            description="Synchronous gauge of circuit breaker state (0=closed,1=half_open,2=open).",
        ),
        "_persistence_policy_transitions_total": meter.create_counter(
            "adapter_persistence_policy_transitions_total",
            unit="1",
            description="Persistence policy transitions by action and source.",
        ),
        "_nonce_operations_total": meter.create_counter(
            "adapter_nonce_operations_total",
            unit="1",
            description="Upload nonce reserve/consume outcomes by backend.",
        ),
        "_upload_credentials_total": meter.create_counter(
            "adapter_upload_credentials_total",
            unit="1",
            description="Signed upload credential issue/validate outcomes.",
        ),
        "_artifact_downloads_total": meter.create_counter(
            "adapter_artifact_downloads_total",
            unit="1",
            description="Artifact download attempts by result and auth mode.",
        ),
        "_artifact_download_bytes_total": meter.create_counter(
            "adapter_artifact_download_bytes_total",
            unit="By",
            description="Total bytes served by artifact download endpoint.",
        ),
        "_artifact_download_duration": meter.create_histogram(
            "adapter_artifact_download_duration_seconds",
            unit="s",
            description="Artifact download request latency.",
        ),
        "_upload_failures_total": meter.create_counter(
            "adapter_upload_failures_total",
            unit="1",
            description="Upload endpoint failures by reason.",
        ),
        "_request_rejections_total": meter.create_counter(
            "adapter_request_rejections_total",
            unit="1",
            description="Non-auth request rejections by reason and route group.",
        ),
        "_adapter_wiring_runs_total": meter.create_counter(
            "adapter_adapter_wiring_runs_total",
            unit="1",
            description="Adapter wiring pass outcomes.",
        ),
        "_adapter_wiring_not_ready_servers": meter.create_gauge(
            "adapter_adapter_wiring_not_ready_servers",
            unit="1",
            description="Synchronous gauge of not-ready server count after last adapter wiring run.",
        ),
        "_cleanup_cycles_total": meter.create_counter(
            "adapter_cleanup_cycles_total",
            unit="1",
            description="Completed cleanup cycles by outcome.",
        ),
        "_cleanup_removed_records_total": meter.create_counter(
            "adapter_cleanup_removed_records_total",
            unit="1",
            description="Records/files removed by cleanup bucket.",
        ),
        "_sessions_lifecycle_total": meter.create_counter(
            "adapter_sessions_lifecycle_total",
            unit="1",
            description="Session lifecycle transitions observed by adapter.",
        ),
        "_tool_definition_drift_total": meter.create_counter(
            "adapter_tool_definition_drift_total",
            unit="1",
            description="Tool-definition drift events detected for pinned sessions.",
        ),
    }


def build_log_exporter(config):
    """Build OTLP log exporter from telemetry config.

    Args:
        config: Telemetry configuration section.
    """
    headers = dict(config.headers)
    timeout = config.export_timeout_seconds
    if config.transport == "http":
        exporter_mod = importlib.import_module("opentelemetry.exporter.otlp.proto.http._log_exporter")
        otlp_log_exporter = getattr(exporter_mod, "OTLPLogExporter")
        return otlp_log_exporter(endpoint=config.logs_endpoint, headers=headers, timeout=timeout)

    exporter_mod = importlib.import_module("opentelemetry.exporter.otlp.proto.grpc._log_exporter")
    otlp_log_exporter = getattr(exporter_mod, "OTLPLogExporter")
    return otlp_log_exporter(
        endpoint=config.endpoint,
        headers=headers,
        timeout=timeout,
        insecure=config.insecure,
    )


def setup_log_export(*, config, resource: Any, root_logger: logging.Logger) -> tuple[Any | None, Any | None]:
    """Set up OTel log exporter and return providers.

    Args:
        config: Telemetry configuration section.
        resource: OTel ``Resource`` for log attribution.
        root_logger: Python root logger to attach the OTel handler to.

    Returns:
        Tuple of ``(logger_provider, otel_log_handler)``.
    """
    logs_api_mod = importlib.import_module("opentelemetry._logs")
    logs_sdk_mod = importlib.import_module("opentelemetry.sdk._logs")
    logs_export_mod = importlib.import_module("opentelemetry.sdk._logs.export")

    log_exporter = build_log_exporter(config)
    logger_provider = logs_sdk_mod.LoggerProvider(resource=resource)
    processor_kwargs: dict[str, int] = {}
    if config.log_batch_max_queue_size is not None:
        processor_kwargs["max_queue_size"] = int(config.log_batch_max_queue_size)
    if config.log_batch_max_export_batch_size is not None:
        processor_kwargs["max_export_batch_size"] = int(config.log_batch_max_export_batch_size)
    if config.log_batch_schedule_delay_millis is not None:
        processor_kwargs["schedule_delay_millis"] = int(config.log_batch_schedule_delay_millis)
    if config.log_batch_export_timeout_millis is not None:
        processor_kwargs["export_timeout_millis"] = int(config.log_batch_export_timeout_millis)

    logger_provider.add_log_record_processor(logs_export_mod.BatchLogRecordProcessor(log_exporter, **processor_kwargs))
    logs_api_mod.set_logger_provider(logger_provider)
    otel_log_handler = logs_sdk_mod.LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
    root_logger.addHandler(otel_log_handler)
    return logger_provider, otel_log_handler
