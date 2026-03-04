from __future__ import annotations

import logging

from remote_mcp_adapter.config import AdapterConfig
from remote_mcp_adapter.log_redaction import (
    SensitiveLogFilter,
    collect_sensitive_log_keys,
    install_log_redaction_filter,
)


def _build_config() -> AdapterConfig:
    return AdapterConfig(
        core={
            "auth": {
                "enabled": True,
                "header_name": "X-Adapter-Auth",
                "token": "adapter-secret-token",
                "signing_secret": "adapter-signing-secret",
            }
        },
        telemetry={"headers": {"Authorization": "Bearer telemetry-secret"}},
        servers=[
            {
                "id": "playwright",
                "mount_path": "/mcp/playwright",
                "upstream": {
                    "url": "http://example.invalid/mcp",
                    "static_headers": {
                        "X-Upstream-Token": "upstream-secret-token",
                    },
                    "client_headers": {
                        "required": ["X-Client-Required-Token"],
                        "passthrough": ["X-Client-Pass-Through-Auth"],
                    },
                },
                "adapters": [],
            }
        ],
    )


def test_collect_sensitive_log_keys_includes_configured_header_names():
    sensitive_keys = collect_sensitive_log_keys(config=_build_config())

    assert "x_adapter_auth" in sensitive_keys
    assert "authorization" in sensitive_keys
    assert "x_upstream_token" in sensitive_keys
    assert "x_client_required_token" in sensitive_keys
    assert "x_client_pass_through_auth" in sensitive_keys


def test_sensitive_log_filter_redacts_structured_headers_and_token_fields():
    redaction_filter = SensitiveLogFilter(sensitive_key_names={"x_client_pass_through_auth"})
    record = logging.makeLogRecord(
        {
            "name": "tests.redaction",
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "upstream call failed",
            "args": (),
            "headers": {
                "Authorization": "Bearer should-not-appear",
                "X-Client-Pass-Through-Auth": "pass-through-secret",
            },
            "token": "inline-token-value",
        }
    )

    redaction_filter.filter(record)

    assert record.__dict__["token"] == "<redacted>"
    assert record.__dict__["headers"]["Authorization"] == "<redacted>"
    assert record.__dict__["headers"]["X-Client-Pass-Through-Auth"] == "<redacted>"


def test_sensitive_log_filter_redacts_message_token_patterns():
    redaction_filter = SensitiveLogFilter(sensitive_key_names={"x_client_pass_through_auth"})
    record = logging.makeLogRecord(
        {
            "name": "tests.redaction",
            "levelno": logging.WARNING,
            "levelname": "WARNING",
            "msg": (
                "Authorization: Bearer should-not-appear "
                "x_client_pass_through_auth=header-secret "
                "token=raw-token-value "
                "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.TnZnP2P6B1xzzzzzz"
            ),
            "args": (),
        }
    )

    redaction_filter.filter(record)
    rendered = record.getMessage()

    assert "should-not-appear" not in rendered
    assert "header-secret" not in rendered
    assert "raw-token-value" not in rendered
    assert "eyJhbGciOiJIUzI1NiJ9" not in rendered
    assert "<redacted>" in rendered


def test_install_log_redaction_filter_attaches_to_existing_handlers():
    logger_name = "tests.log_redaction_attach"
    test_logger = logging.getLogger(logger_name)
    test_logger.handlers.clear()
    test_logger.filters.clear()
    handler = logging.StreamHandler()
    test_logger.addHandler(handler)
    try:
        redaction_filter = install_log_redaction_filter(config=_build_config())
    finally:
        test_logger.handlers.clear()
        test_logger.filters.clear()

    assert redaction_filter in handler.filters
