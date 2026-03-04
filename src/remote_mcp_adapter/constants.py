"""Shared constants used across adapter modules."""

from __future__ import annotations

GLOBAL_SERVER_ID = "global"
UNKNOWN_SERVER_ID = "unknown"
MCP_SESSION_ID_HEADER = "mcp-session-id"
DEFAULT_ADAPTER_AUTH_HEADER = "X-Mcp-Adapter-Auth-Token"
ARTIFACT_PATH_PREFIX = "/artifacts/"
REDACTED_LOG_VALUE = "<redacted>"

# Conservative key fragments and exact names commonly used for secrets.
SENSITIVE_LOG_KEY_FRAGMENTS = (
    "authorization",
    "token",
    "secret",
    "password",
    "cookie",
    "api_key",
    "apikey",
    "api-key",
    "private_key",
    "client_secret",
)
SENSITIVE_LOG_KEY_NAMES = (
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "secret",
    "signing_secret",
    "password",
    "cookie",
    "set-cookie",
    "signature",
    "sig",
)
