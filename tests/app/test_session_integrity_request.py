"""Tests for session_integrity/request.py covering uncovered branches."""

from __future__ import annotations

from types import SimpleNamespace

from remote_mcp_adapter.session_integrity.request import (
    build_adapter_auth_trust_candidate,
    _resolve_session_target,
)


def _config(*, auth_enabled: bool = True, header_name: str = "x-adapter-auth") -> SimpleNamespace:
    return SimpleNamespace(
        core=SimpleNamespace(
            auth=SimpleNamespace(
                enabled=auth_enabled,
                header_name=header_name,
                token="secret",
            )
        )
    )


def _request(
    path: str = "/mcp/s1/tools",
    headers: dict | None = None,
    *,
    artifact_download_signed_auth: bool = False,
    upload_signed_auth: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        headers=headers if headers is not None else {"x-adapter-auth": "secret", "mcp-session-id": "sess-1"},
        state=SimpleNamespace(
            artifact_download_signed_auth=artifact_download_signed_auth,
            upload_signed_auth=upload_signed_auth,
        ),
    )


def test_returns_none_when_auth_disabled():
    candidate = build_adapter_auth_trust_candidate(
        request=_request(),
        config=_config(auth_enabled=False),
        mount_path_to_server_id={"/mcp/s1": "s1"},
        upload_path_prefix="/upload",
    )
    assert candidate is None


def test_returns_none_when_artifact_download_signed_auth():
    candidate = build_adapter_auth_trust_candidate(
        request=_request(artifact_download_signed_auth=True),
        config=_config(),
        mount_path_to_server_id={"/mcp/s1": "s1"},
        upload_path_prefix="/upload",
    )
    assert candidate is None


def test_returns_none_when_upload_signed_auth():
    candidate = build_adapter_auth_trust_candidate(
        request=_request(upload_signed_auth=True),
        config=_config(),
        mount_path_to_server_id={"/mcp/s1": "s1"},
        upload_path_prefix="/upload",
    )
    assert candidate is None


def test_returns_none_when_no_auth_token():
    candidate = build_adapter_auth_trust_candidate(
        request=_request(headers={"mcp-session-id": "sess-1"}),
        config=_config(),
        mount_path_to_server_id={"/mcp/s1": "s1"},
        upload_path_prefix="/upload",
    )
    assert candidate is None


def test_returns_none_when_path_not_session_scoped():
    candidate = build_adapter_auth_trust_candidate(
        request=_request(path="/healthz"),
        config=_config(),
        mount_path_to_server_id={"/mcp/s1": "s1"},
        upload_path_prefix="/upload",
    )
    assert candidate is None


def test_returns_none_when_no_session_id_in_header():
    candidate = build_adapter_auth_trust_candidate(
        request=_request(path="/healthz", headers={"x-adapter-auth": "secret"}),
        config=_config(),
        mount_path_to_server_id={"/mcp/s1": "s1"},
        upload_path_prefix="/upload",
    )
    assert candidate is None


def test_returns_candidate_for_mounted_path():
    candidate = build_adapter_auth_trust_candidate(
        request=_request(path="/mcp/s1/tools"),
        config=_config(),
        mount_path_to_server_id={"/mcp/s1": "s1"},
        upload_path_prefix="/upload",
    )
    assert candidate is not None
    assert candidate.server_id == "s1"
    assert candidate.session_id == "sess-1"
    assert candidate.trust_context.binding_kind == "adapter_auth_token"
    assert len(candidate.trust_context.fingerprint) == 64


def test_resolve_session_target_upload_path_branch():
    req = _request(
        path="/upload/my-server/some/file.bin",
        headers={"mcp-session-id": "sess-upload"},
    )
    server_id, session_id = _resolve_session_target(
        request=req,
        mount_path_to_server_id={},
        upload_path_prefix="/upload",
    )
    assert server_id == "my-server"
    assert session_id == "sess-upload"


def test_resolve_session_target_upload_path_empty_server_id():
    req = _request(
        path="/upload/",
        headers={"mcp-session-id": "sess-upload"},
    )
    server_id, session_id = _resolve_session_target(
        request=req,
        mount_path_to_server_id={},
        upload_path_prefix="/upload",
    )
    assert server_id is None
    assert session_id is None


def test_resolve_session_target_artifact_download_path():
    req = _request(
        path="/artifacts/my-server/sess-123/artifact-id/file.txt",
        headers={"mcp-session-id": "sess-header"},
    )
    server_id, session_id = _resolve_session_target(
        request=req,
        mount_path_to_server_id={},
        upload_path_prefix="/upload",
    )
    assert server_id == "my-server"
    assert session_id == "sess-123"


def test_resolve_session_target_unmatched_path():
    req = _request(path="/docs", headers={})
    server_id, session_id = _resolve_session_target(
        request=req,
        mount_path_to_server_id={},
        upload_path_prefix="/upload",
    )
    assert server_id is None
    assert session_id is None
