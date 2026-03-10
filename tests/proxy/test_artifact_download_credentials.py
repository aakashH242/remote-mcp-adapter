from __future__ import annotations

from types import SimpleNamespace

from remote_mcp_adapter.proxy import artifact_download_credentials as adc


def _config(
    *,
    allow_download: bool = True,
    auth_enabled: bool = True,
    signing_secret: str = "",
    token: str = "",
    auth_ttl: int = 60,
    artifact_ttl=None,
):
    return SimpleNamespace(
        core=SimpleNamespace(
            allow_artifacts_download=allow_download,
            auth=SimpleNamespace(
                enabled=auth_enabled,
                signing_secret=signing_secret,
                token=token,
                signed_upload_ttl_seconds=auth_ttl,
            ),
        ),
        artifacts=SimpleNamespace(ttl_seconds=artifact_ttl),
    )


def test_signature_payload_has_expected_canonical_format():
    payload = adc._signature_payload(
        server_id="s",
        session_id="sess",
        artifact_id="a1",
        filename="f.txt",
        expires_at=123,
    )
    assert payload == b"s\nsess\na1\nf.txt\n123"


def test_resolve_ttl_seconds_prefers_positive_artifact_ttl_and_falls_back_to_auth_ttl():
    cfg_artifact = _config(artifact_ttl=90, auth_ttl=30)
    cfg_zero = _config(artifact_ttl=0, auth_ttl=30)
    cfg_negative = _config(artifact_ttl=-1, auth_ttl=30)
    cfg_none = _config(artifact_ttl=None, auth_ttl=30)

    assert adc._resolve_ttl_seconds(cfg_artifact) == 90
    assert adc._resolve_ttl_seconds(cfg_zero) == 30
    assert adc._resolve_ttl_seconds(cfg_negative) == 30
    assert adc._resolve_ttl_seconds(cfg_none) == 30


def test_init_enforces_minimum_ttl_and_properties():
    manager = adc.ArtifactDownloadCredentialManager(enabled=True, secret="sec", ttl_seconds=0)
    assert manager.enabled is True
    assert manager.ttl_seconds == 1


def test_from_config_enabling_and_secret_resolution_paths():
    cfg_secret = _config(allow_download=True, auth_enabled=True, signing_secret="  s1 ", token="tok")
    cfg_token = _config(allow_download=True, auth_enabled=True, signing_secret="", token="tok")
    cfg_disabled_allow = _config(allow_download=False, auth_enabled=True, signing_secret="s")
    cfg_disabled_auth = _config(allow_download=True, auth_enabled=False, signing_secret="s")
    cfg_disabled_secret = _config(allow_download=True, auth_enabled=True, signing_secret="   ", token="")

    assert adc.ArtifactDownloadCredentialManager.from_config(cfg_secret).enabled is True
    assert adc.ArtifactDownloadCredentialManager.from_config(cfg_token).enabled is True
    assert adc.ArtifactDownloadCredentialManager.from_config(cfg_disabled_allow).enabled is False
    assert adc.ArtifactDownloadCredentialManager.from_config(cfg_disabled_auth).enabled is False
    assert adc.ArtifactDownloadCredentialManager.from_config(cfg_disabled_secret).enabled is False


def test_issue_returns_empty_when_disabled():
    manager = adc.ArtifactDownloadCredentialManager(enabled=False, secret="sec", ttl_seconds=10)
    assert manager.issue(server_id="s", session_id="sess", artifact_id="a1", filename="f.txt") == {}


def test_issue_returns_signed_payload_when_enabled(monkeypatch):
    manager = adc.ArtifactDownloadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    monkeypatch.setattr(adc.time, "time", lambda: 100)

    params = manager.issue(server_id="s", session_id="sess", artifact_id="a1", filename="f.txt")
    expected_signature = manager._sign(server_id="s", session_id="sess", artifact_id="a1", filename="f.txt", expires_at=110)

    assert params == {"mcp_artifact_exp": "110", "mcp_artifact_sig": expected_signature}


def test_validate_returns_false_when_disabled():
    manager = adc.ArtifactDownloadCredentialManager(enabled=False, secret="sec", ttl_seconds=10)
    result = manager.validate(
        server_id="s",
        session_id="sess",
        artifact_id="a1",
        filename="f.txt",
        query_params={"mcp_artifact_exp": "110", "mcp_artifact_sig": "sig"},
    )
    assert result is False


def test_validate_returns_false_for_missing_fields():
    manager = adc.ArtifactDownloadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    assert (
        manager.validate(
            server_id="s",
            session_id="sess",
            artifact_id="a1",
            filename="f.txt",
            query_params={"mcp_artifact_exp": "110"},
        )
        is False
    )


def test_validate_returns_false_for_invalid_expiry():
    manager = adc.ArtifactDownloadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    assert (
        manager.validate(
            server_id="s",
            session_id="sess",
            artifact_id="a1",
            filename="f.txt",
            query_params={"mcp_artifact_exp": "abc", "mcp_artifact_sig": "sig"},
        )
        is False
    )


def test_validate_returns_false_for_expired(monkeypatch):
    manager = adc.ArtifactDownloadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    monkeypatch.setattr(adc.time, "time", lambda: 200)

    assert (
        manager.validate(
            server_id="s",
            session_id="sess",
            artifact_id="a1",
            filename="f.txt",
            query_params={"mcp_artifact_exp": "199", "mcp_artifact_sig": "sig"},
        )
        is False
    )


def test_validate_accepts_when_expiry_equals_current_time(monkeypatch):
    manager = adc.ArtifactDownloadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    monkeypatch.setattr(adc.time, "time", lambda: 200)
    signature = manager._sign(server_id="s", session_id="sess", artifact_id="a1", filename="f.txt", expires_at=200)

    assert (
        manager.validate(
            server_id="s",
            session_id="sess",
            artifact_id="a1",
            filename="f.txt",
            query_params={"mcp_artifact_exp": "200", "mcp_artifact_sig": signature},
        )
        is True
    )


def test_validate_returns_false_for_bad_signature(monkeypatch):
    manager = adc.ArtifactDownloadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    monkeypatch.setattr(adc.time, "time", lambda: 100)

    assert (
        manager.validate(
            server_id="s",
            session_id="sess",
            artifact_id="a1",
            filename="f.txt",
            query_params={"mcp_artifact_exp": "110", "mcp_artifact_sig": "wrong"},
        )
        is False
    )


def test_validate_returns_false_when_artifact_identity_changes(monkeypatch):
    manager = adc.ArtifactDownloadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    monkeypatch.setattr(adc.time, "time", lambda: 100)
    signature = manager._sign(server_id="s", session_id="sess", artifact_id="a1", filename="f.txt", expires_at=110)

    assert (
        manager.validate(
            server_id="s",
            session_id="sess",
            artifact_id="a2",
            filename="f.txt",
            query_params={"mcp_artifact_exp": "110", "mcp_artifact_sig": signature},
        )
        is False
    )


def test_validate_returns_true_for_valid_signature_and_not_expired(monkeypatch):
    manager = adc.ArtifactDownloadCredentialManager(enabled=True, secret="sec", ttl_seconds=10)
    monkeypatch.setattr(adc.time, "time", lambda: 100)
    signature = manager._sign(server_id="s", session_id="sess", artifact_id="a1", filename="f.txt", expires_at=110)

    assert (
        manager.validate(
            server_id="s",
            session_id="sess",
            artifact_id="a1",
            filename="f.txt",
            query_params={"mcp_artifact_exp": "110", "mcp_artifact_sig": signature},
        )
        is True
    )
