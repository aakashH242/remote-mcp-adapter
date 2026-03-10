from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from remote_mcp_adapter.app import upload_route_helpers as urh


class _Record:
    def __init__(self, upload_id="u1", size=10):
        self.upload_id = upload_id
        self.filename = f"{upload_id}.txt"
        self.mime_type = "text/plain"
        self.size_bytes = size
        self.sha256 = "abcd"


class _Telemetry:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.batch_calls = []
        self.fail_calls = []

    async def record_upload_batch(self, **kwargs):
        self.batch_calls.append(kwargs)

    async def record_upload_failure(self, **kwargs):
        self.fail_calls.append(kwargs)


@pytest.mark.asyncio
async def test_build_save_and_rollback_helpers(monkeypatch):
    payload = urh.build_upload_response_payload(
        server_id="s1",
        session_id="sess",
        uri_scheme="upload://",
        records=[_Record("u1")],
    )
    assert payload["count"] == 1
    assert payload["upload_handle"].startswith("upload://sessions/sess/")

    payload2 = urh.build_upload_response_payload(
        server_id="s1",
        session_id="sess",
        uri_scheme="upload://",
        records=[_Record("u1"), _Record("u2")],
    )
    assert payload2["count"] == 2
    assert "upload" not in payload2

    class _File:
        filename = "name.txt"
        file = object()
        content_type = "text/plain"

    async def persist_upload_stream(**kwargs):
        return {"ok": True}

    saved = await urh.save_one_uploaded_file(
        persist_upload_stream=persist_upload_stream,
        resolved_config=object(),
        session_store=object(),
        server_id="s1",
        session_id="sess",
        uploaded_file=_File(),
        expected_sha256="ab",
    )
    assert saved == {"ok": True}

    calls = []

    class _Store:
        async def remove_uploads(self, **kwargs):
            calls.append(kwargs)
            return 1

    await urh.rollback_successful_uploads(session_store=_Store(), server_id="s1", session_id="sess", records=[])

    await urh.rollback_successful_uploads(
        session_store=_Store(),
        server_id="s1",
        session_id="sess",
        records=[_Record("u1"), _Record("u2")],
    )
    assert calls


def test_normalize_and_validate_sha256_helpers():
    assert urh.normalize_sha256_inputs(None) == []
    assert urh.normalize_sha256_inputs(["  abc  ", "", "   ", "def"]) == ["abc", "def"]

    urh.validate_sha256_count(files_count=2, sha256_values=[])
    urh.validate_sha256_count(files_count=2, sha256_values=["abc", "def"])

    with pytest.raises(HTTPException, match="sha256 count mismatch"):
        urh.validate_sha256_count(files_count=2, sha256_values=["abc"])


@pytest.mark.asyncio
async def test_rollback_and_policy_validation_and_close(monkeypatch):
    class _StoreErr:
        async def remove_uploads(self, **kwargs):
            raise RuntimeError("x")

    logger_events = []
    monkeypatch.setattr(urh.logger, "exception", lambda *args, **kwargs: logger_events.append(("exc", args, kwargs)))
    monkeypatch.setattr(urh.logger, "warning", lambda *args, **kwargs: logger_events.append(("warn", args, kwargs)))

    await urh.rollback_successful_uploads(
        session_store=_StoreErr(),
        server_id="s1",
        session_id="sess",
        records=[_Record("u1")],
    )
    assert any(level == "exc" for level, *_ in logger_events)

    class _StorePartial:
        async def remove_uploads(self, **kwargs):
            return 0

    await urh.rollback_successful_uploads(
        session_store=_StorePartial(),
        server_id="s1",
        session_id="sess",
        records=[_Record("u1")],
    )
    assert any(level == "warn" for level, *_ in logger_events)

    urh.raise_unknown_server_if_missing(server_id="s1", proxy_map={"s1": object()})
    with pytest.raises(HTTPException):
        urh.raise_unknown_server_if_missing(server_id="x", proxy_map={"s1": object()})

    policy_ok = SimpleNamespace(should_reject_stateful_requests=lambda: False)
    urh.raise_fail_closed_if_rejecting(persistence_policy=policy_ok)

    policy_fail = SimpleNamespace(should_reject_stateful_requests=lambda: True)
    with pytest.raises(HTTPException):
        urh.raise_fail_closed_if_rejecting(persistence_policy=policy_fail)

    class _Upload:
        def __init__(self, exc=None):
            self.exc = exc

        async def close(self):
            if self.exc:
                raise self.exc
            return None

    await urh.close_uploaded_files([_Upload(), _Upload(RuntimeError("close"))])
    assert any(level == "warn" for level, *_ in logger_events)


@pytest.mark.asyncio
async def test_upload_metrics_helpers():
    telemetry = _Telemetry(enabled=True)
    records = [_Record("u1", 3), _Record("u2", 4)]

    await urh.record_upload_batch_metrics(telemetry=telemetry, server_id="s1", records=records)
    assert telemetry.batch_calls[-1]["bytes_total"] == 7

    await urh.record_upload_failure_metrics(telemetry=telemetry, server_id="s1", reason="bad")
    assert telemetry.fail_calls[-1]["reason"] == "bad"

    telemetry_off = _Telemetry(enabled=False)
    await urh.record_upload_batch_metrics(telemetry=telemetry_off, server_id="s1", records=records)
    await urh.record_upload_failure_metrics(telemetry=telemetry_off, server_id="s1", reason="bad")
    assert telemetry_off.batch_calls == []
    assert telemetry_off.fail_calls == []

    await urh.record_upload_batch_metrics(telemetry=None, server_id="s1", records=records)
    await urh.record_upload_failure_metrics(telemetry=None, server_id="s1", reason="bad")
