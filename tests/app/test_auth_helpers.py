from __future__ import annotations

import pytest

from remote_mcp_adapter.app import auth_helpers as ah


class _Telemetry:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.calls = []

    async def record_auth_rejection(self, **kwargs):
        self.calls.append(kwargs)


def test_public_and_oauth_path_helpers_and_route_groups():
    expected_artifact_route = "/artifacts/{server_id}/{session_id}/{artifact_id}/{filename}"

    assert ah.is_public_unprotected_path("/healthz") is True
    assert ah.is_public_unprotected_path("/healthz/x") is True
    assert ah.is_public_unprotected_path("/private") is False

    assert ah.route_group_for_metrics("/artifacts/s/sess/a/f.txt", upload_path_prefix="/upload") == expected_artifact_route
    assert ah.route_group_for_metrics("/upload/s", upload_path_prefix="/upload") == "/upload/{server_id}"
    assert ah.route_group_for_metrics("/upload/s", upload_path_prefix="/upload/") == "/upload/{server_id}"
    assert ah.route_group_for_metrics("/uploadx/s", upload_path_prefix="/upload") == "/uploadx/s"
    assert ah.route_group_for_metrics("/uploadx/s", upload_path_prefix="/upload/") == "/uploadx/s"
    assert ah.route_group_for_metrics("/docs", upload_path_prefix="/upload") == "/docs"
    assert (
        ah.route_group_for_metrics(
            "/.well-known/openid-configuration",
            upload_path_prefix="/upload",
        )
        == "/.well-known/*"
    )
    assert ah.route_group_for_metrics("/x", upload_path_prefix="/upload") == "/x"

    assert ah.is_oauth_discovery_path("/abc/.well-known/oauth-authorization-server") is True
    assert ah.is_oauth_discovery_path("/abc") is False


def test_parse_artifact_download_path_variants():
    assert ah.parse_artifact_download_path("/wrong") is None
    assert ah.parse_artifact_download_path("/artifacts/s/sess/a") is None
    assert ah.parse_artifact_download_path("/artifacts//sess/a/f") is None

    parsed = ah.parse_artifact_download_path("/artifacts/s/sess/a/file.txt")
    assert parsed == ("s", "sess", "a", "file.txt")


@pytest.mark.asyncio
async def test_record_auth_rejection_paths():
    await ah.record_auth_rejection(telemetry=None, route_group="/x", reason="r")

    t_disabled = _Telemetry(enabled=False)
    await ah.record_auth_rejection(telemetry=t_disabled, route_group="/x", reason="r")
    assert t_disabled.calls == []

    t_enabled = _Telemetry(enabled=True)
    await ah.record_auth_rejection(telemetry=t_enabled, route_group="/x", reason="r", server_id="s")
    assert t_enabled.calls == [{"reason": "r", "route_group": "/x", "server_id": "s"}]
