from __future__ import annotations

import pytest

from remote_mcp_adapter.config.schemas.adapters import (
    ArtifactProducerAdapterConfig,
    OutputLocatorConfig,
    UploadConsumerAdapterConfig,
)
from remote_mcp_adapter.config.schemas.common import ToolDefaults, normalize_path, parse_byte_size
from remote_mcp_adapter.config.schemas.core import CoreAuthConfig, CoreConfig
from remote_mcp_adapter.config.schemas.persistence import (
    StatePersistenceDiskConfig,
    StatePersistenceRedisConfig,
    StateReconciliationConfig,
)
from remote_mcp_adapter.config.schemas.server import ServerConfig
from remote_mcp_adapter.config.schemas.storage import ArtifactsConfig, SessionsConfig, StorageConfig, UploadsConfig
from remote_mcp_adapter.config.schemas.telemetry import TelemetryConfig
from remote_mcp_adapter.config.schemas.upstream import UpstreamConfig


def test_tool_defaults_model():
    model = ToolDefaults(tool_call_timeout_seconds=3, allow_raw_output=True)
    assert model.tool_call_timeout_seconds == 3
    assert model.allow_raw_output is True


def test_normalize_path_behaviors():
    assert normalize_path("upload", "f") == "/upload"
    assert normalize_path(" /upload/ ", "f") == "/upload"
    assert normalize_path("/", "f") == "/"
    with pytest.raises(ValueError, match="cannot be blank"):
        normalize_path("   ", "f")


def test_parse_byte_size_variants():
    assert parse_byte_size(None, "f") is None
    assert parse_byte_size(5, "f") == 5
    assert parse_byte_size("10kb", "f") == 10000
    assert parse_byte_size("2MiB", "f") == 2 * 1024 * 1024
    with pytest.raises(ValueError, match="must be >= 0"):
        parse_byte_size(-1, "f")
    with pytest.raises(ValueError, match="Unsupported unit"):
        parse_byte_size("10xyz", "f")
    with pytest.raises(ValueError, match="Invalid byte size"):
        parse_byte_size("bad", "f")


def test_core_auth_validators():
    assert CoreAuthConfig(enabled=False, token=None).enabled is False
    with pytest.raises(ValueError, match="token must be set"):
        CoreAuthConfig(enabled=True, token="   ")
    with pytest.raises(ValueError, match="signing_secret cannot be blank"):
        CoreAuthConfig(enabled=False, token=None, signing_secret="   ")


def test_core_config_validators():
    cfg = CoreConfig(upload_path="uploads", log_level=" INFO ")
    assert cfg.upload_path == "/uploads"
    assert cfg.log_level == "info"
    with pytest.raises(ValueError, match="core.log_level must be one of"):
        CoreConfig(log_level="verbose")
    with pytest.raises(ValueError, match=r"core\.upload_path cannot be '/'"):
        CoreConfig(upload_path="/")


def test_telemetry_validators_and_defaults():
    grpc = TelemetryConfig(transport="grpc", endpoint=None)
    assert grpc.endpoint == "http://localhost:4317"

    http = TelemetryConfig(transport="http", endpoint=None, logs_endpoint=None)
    assert http.endpoint == "http://localhost:4318/v1/metrics"
    assert http.logs_endpoint == "http://localhost:4318/v1/logs"

    custom = TelemetryConfig(
        transport="http",
        endpoint=" http://e ",
        logs_endpoint=" http://l ",
        service_name=" svc ",
        service_namespace="  ",
    )
    assert custom.endpoint == "http://e"
    assert custom.logs_endpoint == "http://l"
    assert custom.service_name == "svc"
    assert custom.service_namespace is None
    assert TelemetryConfig(service_namespace=None).service_namespace is None

    with pytest.raises(ValueError, match="telemetry.endpoint cannot be blank"):
        TelemetryConfig(endpoint="  ")
    with pytest.raises(ValueError, match="telemetry.logs_endpoint cannot be blank"):
        TelemetryConfig(logs_endpoint="  ")
    with pytest.raises(ValueError, match="telemetry.service_name cannot be blank"):
        TelemetryConfig(service_name="  ")


def test_persistence_field_validators():
    assert StatePersistenceDiskConfig(local_path=" /tmp/x ").local_path == "/tmp/x"
    with pytest.raises(ValueError, match="cannot be blank"):
        StatePersistenceDiskConfig(local_path="  ")

    redis = StatePersistenceRedisConfig(host=" localhost ", key_base=" key ")
    assert redis.host == "localhost"
    assert redis.key_base == "key"
    with pytest.raises(ValueError, match="host cannot be blank"):
        StatePersistenceRedisConfig(host="  ")
    with pytest.raises(ValueError, match="key_base cannot be blank"):
        StatePersistenceRedisConfig(key_base="  ")

    recon = StateReconciliationConfig(legacy_server_id=" srv ")
    assert recon.legacy_server_id == "srv"
    with pytest.raises(ValueError, match="legacy_server_id cannot be blank"):
        StateReconciliationConfig(legacy_server_id="  ")


def test_storage_sessions_uploads_artifacts_validators():
    storage = StorageConfig(max_size="10mb", artifact_locator_allowed_roots=[" /a ", "  "])
    assert storage.max_size == 10 * 1000 * 1000
    assert storage.artifact_locator_allowed_roots == ["/a"]

    with pytest.raises(ValueError, match="artifact_locator_allowed_roots must be set"):
        StorageConfig(artifact_locator_policy="allow_configured_roots", artifact_locator_allowed_roots=[])

    sessions = SessionsConfig(max_total_session_size="2MiB")
    assert sessions.max_total_session_size == 2 * 1024 * 1024

    uploads = UploadsConfig(max_file_bytes="1MiB", uri_scheme="UPLOAD://")
    assert uploads.max_file_bytes == 1024 * 1024
    assert uploads.uri_scheme == "upload://"
    with pytest.raises(ValueError, match="must be > 0"):
        UploadsConfig(max_file_bytes=0)
    with pytest.raises(ValueError, match="must end with '://'"):
        UploadsConfig(uri_scheme="upload")

    artifacts = ArtifactsConfig(uri_scheme="ARTIFACT://")
    assert artifacts.uri_scheme == "artifact://"
    with pytest.raises(ValueError, match="must end with '://'"):
        ArtifactsConfig(uri_scheme="artifact")


def test_upstream_and_server_and_adapter_validators():
    up = UpstreamConfig(url=" http://x ")
    assert up.url == "http://x"
    with pytest.raises(ValueError, match="upstream.url is required"):
        UpstreamConfig(url="  ")

    uc = UploadConsumerAdapterConfig(type="upload_consumer", tools=["t"], file_path_argument=" path ")
    assert uc.file_path_argument == "path"
    with pytest.raises(ValueError, match="file_path_argument is required"):
        UploadConsumerAdapterConfig(type="upload_consumer", tools=["t"], file_path_argument="  ")

    ap = ArtifactProducerAdapterConfig(type="artifact_producer", tools=["t"], output_path_argument="x")
    assert ap.output_path_argument == "x"

    with pytest.raises(ValueError, match="output_path_key is required"):
        ArtifactProducerAdapterConfig(
            type="artifact_producer",
            tools=["t"],
            output_path_argument=None,
            output_locator=OutputLocatorConfig(mode="structured", output_path_key="  "),
        )

    ok_without_output_arg = ArtifactProducerAdapterConfig(
        type="artifact_producer",
        tools=["t"],
        output_path_argument=None,
        output_locator=OutputLocatorConfig(mode="regex", output_path_regexes=["x"]),
    )
    assert ok_without_output_arg.output_path_argument is None

    server = ServerConfig(id=" srv ", mount_path="mcp", upstream=UpstreamConfig(url="http://x"))
    assert server.id == "srv"
    assert server.mount_path == "/mcp"
    assert server.disabled_tools == []

    server_with_disabled = ServerConfig(
        id="s",
        mount_path="/mcp",
        upstream=UpstreamConfig(url="http://x"),
        disabled_tools=["exact_tool", "^prefix_.*"],
    )
    assert server_with_disabled.disabled_tools == ["exact_tool", "^prefix_.*"]

    with pytest.raises(ValueError, match=r"servers\[\]\.id is required"):
        ServerConfig(id="  ", mount_path="/m", upstream=UpstreamConfig(url="http://x"))
    with pytest.raises(ValueError, match="cannot be '/'"):
        ServerConfig(id="s", mount_path="/", upstream=UpstreamConfig(url="http://x"))
