from __future__ import annotations

from types import SimpleNamespace

from remote_mcp_adapter.telemetry import otel_bootstrap as bootstrap


class _FakeExporter:
	def __init__(self, **kwargs):
		self.kwargs = kwargs


class _FakeMeter:
	def __init__(self) -> None:
		self.calls: list[tuple[str, str, str, str]] = []

	def create_counter(self, name: str, *, unit: str, description: str):
		self.calls.append(("counter", name, unit, description))
		return ("counter", name)

	def create_histogram(self, name: str, *, unit: str, description: str):
		self.calls.append(("histogram", name, unit, description))
		return ("histogram", name)

	def create_gauge(self, name: str, *, unit: str, description: str):
		self.calls.append(("gauge", name, unit, description))
		return ("gauge", name)


class _FakeMetricsApi:
	def __init__(self, meter: _FakeMeter) -> None:
		self._meter = meter
		self.provider = None

	def set_meter_provider(self, provider) -> None:
		self.provider = provider

	def get_meter(self, name: str, version: str):
		return self._meter


class _FakeResource:
	@staticmethod
	def create(attributes):
		return {"resource_attributes": attributes}


class _FakeMeterProvider:
	def __init__(self, *, resource, metric_readers):
		self.resource = resource
		self.metric_readers = metric_readers


class _FakeReader:
	def __init__(self, exporter, *, export_interval_millis: int, export_timeout_millis: int):
		self.exporter = exporter
		self.export_interval_millis = export_interval_millis
		self.export_timeout_millis = export_timeout_millis


class _FakeLoggerProvider:
	def __init__(self, *, resource):
		self.resource = resource
		self.processors = []

	def add_log_record_processor(self, processor) -> None:
		self.processors.append(processor)


class _FakeBatchProcessor:
	def __init__(self, exporter, **kwargs):
		self.exporter = exporter
		self.kwargs = kwargs


class _FakeLoggingHandler:
	def __init__(self, *, level, logger_provider):
		self.level = level
		self.logger_provider = logger_provider


class _FakeRootLogger:
	def __init__(self) -> None:
		self.handlers = []

	def addHandler(self, handler) -> None:
		self.handlers.append(handler)


def _config(**overrides):
	base = dict(
		headers={"Authorization": "Bearer token"},
		endpoint="https://collector.example.com/v1/metrics",
		logs_endpoint="https://collector.example.com/v1/logs",
		export_timeout_seconds=4,
		export_interval_seconds=3,
		transport="http",
		insecure=False,
		service_name="remote-mcp-adapter",
		service_namespace="mcp",
		log_batch_max_queue_size=100,
		log_batch_max_export_batch_size=10,
		log_batch_schedule_delay_millis=200,
		log_batch_export_timeout_millis=300,
	)
	base.update(overrides)
	return SimpleNamespace(**base)


def test_metric_and_log_exporters_plus_backend_initialization(monkeypatch) -> None:
	fake_meter = _FakeMeter()
	fake_metrics_api = _FakeMetricsApi(fake_meter)
	log_api_calls: list[object] = []

	modules = {
		"opentelemetry.exporter.otlp.proto.http.metric_exporter": SimpleNamespace(OTLPMetricExporter=_FakeExporter),
		"opentelemetry.exporter.otlp.proto.grpc.metric_exporter": SimpleNamespace(OTLPMetricExporter=_FakeExporter),
		"opentelemetry.metrics": fake_metrics_api,
		"opentelemetry.sdk.resources": SimpleNamespace(
			SERVICE_NAME="service.name",
			SERVICE_NAMESPACE="service.namespace",
			Resource=_FakeResource,
		),
		"opentelemetry.sdk.metrics": SimpleNamespace(MeterProvider=_FakeMeterProvider),
		"opentelemetry.sdk.metrics.export": SimpleNamespace(PeriodicExportingMetricReader=_FakeReader),
		"opentelemetry.exporter.otlp.proto.http._log_exporter": SimpleNamespace(OTLPLogExporter=_FakeExporter),
		"opentelemetry.exporter.otlp.proto.grpc._log_exporter": SimpleNamespace(OTLPLogExporter=_FakeExporter),
		"opentelemetry._logs": SimpleNamespace(set_logger_provider=lambda provider: log_api_calls.append(provider)),
		"opentelemetry.sdk._logs": SimpleNamespace(LoggerProvider=_FakeLoggerProvider, LoggingHandler=_FakeLoggingHandler),
		"opentelemetry.sdk._logs.export": SimpleNamespace(BatchLogRecordProcessor=_FakeBatchProcessor),
	}
	monkeypatch.setattr(bootstrap.importlib, "import_module", modules.__getitem__)

	http_metric_exporter = bootstrap.build_metric_exporter(_config())
	grpc_metric_exporter = bootstrap.build_metric_exporter(_config(transport="grpc", insecure=True))
	assert http_metric_exporter.kwargs["endpoint"].endswith("/metrics")
	assert grpc_metric_exporter.kwargs["insecure"] is True

	metrics_api, meter_provider, resource, meter = bootstrap.initialize_metrics_backend(config=_config())
	assert metrics_api is fake_metrics_api
	assert meter_provider.metric_readers[0].export_interval_millis == 3000
	assert resource["resource_attributes"]["service.namespace"] == "mcp"
	assert meter is fake_meter

	instrument_map = bootstrap.create_metric_instruments(meter=fake_meter)
	assert instrument_map["_http_requests_total"] == ("counter", "adapter_http_requests_total")
	assert instrument_map["_circuit_breaker_state"] == ("gauge", "adapter_upstream_circuit_breaker_state")
	assert len(fake_meter.calls) >= 10

	http_log_exporter = bootstrap.build_log_exporter(_config())
	grpc_log_exporter = bootstrap.build_log_exporter(_config(transport="grpc", insecure=True))
	assert http_log_exporter.kwargs["endpoint"].endswith("/logs")
	assert grpc_log_exporter.kwargs["insecure"] is True

	root_logger = _FakeRootLogger()
	logger_provider, handler = bootstrap.setup_log_export(config=_config(), resource=resource, root_logger=root_logger)
	assert logger_provider.processors[0].kwargs["max_queue_size"] == 100
	assert root_logger.handlers == [handler]
	assert log_api_calls == [logger_provider]


def test_initialize_metrics_backend_omits_empty_namespace(monkeypatch) -> None:
	fake_meter = _FakeMeter()
	fake_metrics_api = _FakeMetricsApi(fake_meter)
	modules = {
		"opentelemetry.exporter.otlp.proto.http.metric_exporter": SimpleNamespace(OTLPMetricExporter=_FakeExporter),
		"opentelemetry.metrics": fake_metrics_api,
		"opentelemetry.sdk.resources": SimpleNamespace(
			SERVICE_NAME="service.name",
			SERVICE_NAMESPACE="service.namespace",
			Resource=_FakeResource,
		),
		"opentelemetry.sdk.metrics": SimpleNamespace(MeterProvider=_FakeMeterProvider),
		"opentelemetry.sdk.metrics.export": SimpleNamespace(PeriodicExportingMetricReader=_FakeReader),
	}
	monkeypatch.setattr(bootstrap.importlib, "import_module", modules.__getitem__)

	_, _, resource, _ = bootstrap.initialize_metrics_backend(config=_config(service_namespace=""))
	assert "service.namespace" not in resource["resource_attributes"]
