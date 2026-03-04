from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastmcp.exceptions import ToolError

from remote_mcp_adapter.app import route_registration as rr
from remote_mcp_adapter.core import PersistenceUnavailableError
from remote_mcp_adapter.core.storage.artifact_access import (
    ArtifactFileMissingError,
    ArtifactFilenameMismatchError,
    ArtifactNotFoundError,
)


class _DummyApp:
    def __init__(self):
        self.get_routes = []
        self.post_routes = []

    def get(self, path: str, **kwargs):
        def decorator(fn):
            self.get_routes.append((path, fn.__name__, fn))
            return fn

        return decorator

    def post(self, path: str, **kwargs):
        def decorator(fn):
            self.post_routes.append((path, fn.__name__, fn))
            return fn

        return decorator


class _Runtime:
    backend_type = "memory"

    async def health_snapshot(self):
        return {"ok": True}


class _SessionStore:
    def __init__(self):
        self.ensure_calls = []
        self.ensure_exc = None

    async def ensure_session(self, server_id, session_id):
        self.ensure_calls.append((server_id, session_id))
        if self.ensure_exc:
            raise self.ensure_exc


class _Telemetry:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.downloads = []

    async def record_artifact_download(self, **kwargs):
        self.downloads.append(kwargs)


class _UploadFile:
    def __init__(self, name):
        self.filename = name


class _UploadRecord:
    def __init__(self, name="f.txt"):
        self.upload_id = f"id-{name}"
        self.filename = name
        self.uri = f"upload://{name}"
        self.sha256 = "abcd"
        self.size_bytes = 4
        self.mime_type = "text/plain"


class _ArtifactRecord:
    def __init__(self):
        self.abs_path = __file__
        self.mime_type = "text/plain"
        self.filename = "artifact.txt"
        self.size_bytes = 11


def _request(path="/x", headers=None, query=None, signed=False):
    return SimpleNamespace(
        url=SimpleNamespace(path=path),
        headers=headers or {},
        query_params=query or {},
        state=SimpleNamespace(artifact_download_signed_auth=signed),
    )


def _context(allow_artifacts=True):
    return SimpleNamespace(
        app=_DummyApp(),
        resolved_config=SimpleNamespace(
            core=SimpleNamespace(allow_artifacts_download=allow_artifacts),
            uploads=SimpleNamespace(uri_scheme="upload"),
        ),
        proxy_map={"s1": object()},
        upstream_health={"s1": object()},
        persistence_policy=SimpleNamespace(),
        runtime_ref={"current": _Runtime()},
        session_store=_SessionStore(),
        upload_route="/uploads/{server_id}",
        telemetry=_Telemetry(enabled=True),
        build_memory_persistence_runtime=lambda: object(),
        save_upload_stream=object(),
    )


@pytest.mark.asyncio
async def test_raise_upload_http_exception_for_failure_branches(monkeypatch):
    ctx = _context()
    rollback_calls = []
    failure_metrics = []

    async def rollback_successful_uploads(**kwargs):
        rollback_calls.append(kwargs)

    async def record_upload_failure_metrics(**kwargs):
        failure_metrics.append(kwargs)

    monkeypatch.setattr(rr, "rollback_successful_uploads", rollback_successful_uploads)
    monkeypatch.setattr(rr, "record_upload_failure_metrics", record_upload_failure_metrics)

    with pytest.raises(HTTPException) as p_unavailable:
        await rr._raise_upload_http_exception_for_failure(
            error=PersistenceUnavailableError("down"),
            records=[_UploadRecord()],
            context=ctx,
            server_id="s1",
            session_id="sess",
        )
    assert p_unavailable.value.status_code == 503
    assert rollback_calls
    assert failure_metrics[-1]["reason"] == "persistence_unavailable"

    with pytest.raises(HTTPException) as p_tool:
        await rr._raise_upload_http_exception_for_failure(
            error=ToolError("bad"),
            records=[],
            context=ctx,
            server_id="s1",
            session_id="sess",
        )
    assert p_tool.value.status_code == 400
    assert failure_metrics[-1]["reason"] == "tool_error"

    async def switched_true(**kwargs):
        return True

    monkeypatch.setattr(rr, "apply_runtime_failure_policy_if_persistent_backend", switched_true)

    def raise_backend_failed(*, cause):
        raise HTTPException(status_code=503, detail="backend")

    monkeypatch.setattr(rr, "raise_persistence_backend_operation_failed_http_exception", raise_backend_failed)
    with pytest.raises(HTTPException) as p_switch:
        await rr._raise_upload_http_exception_for_failure(
            error=RuntimeError("x"),
            records=[],
            context=ctx,
            server_id="s1",
            session_id="sess",
        )
    assert p_switch.value.status_code == 503
    assert failure_metrics[-1]["reason"] == "persistence_backend_failure"

    async def switched_false(**kwargs):
        return False

    monkeypatch.setattr(rr, "apply_runtime_failure_policy_if_persistent_backend", switched_false)
    with pytest.raises(HTTPException) as p_other:
        await rr._raise_upload_http_exception_for_failure(
            error=RuntimeError("y"),
            records=[],
            context=ctx,
            server_id="s1",
            session_id="sess",
        )
    assert p_other.value.status_code == 400
    assert failure_metrics[-1]["reason"] == "upload_processing_error"


@pytest.mark.asyncio
async def test_record_artifact_download_metrics_and_upload_validation(monkeypatch):
    ctx = _context()

    await rr._record_artifact_download_metrics(
        context=ctx,
        server_id="s1",
        result="success",
        auth_mode="session_context",
        started_at=0.0,
        size_bytes=12,
    )
    assert ctx.telemetry.downloads

    ctx.telemetry = _Telemetry(enabled=False)
    await rr._record_artifact_download_metrics(
        context=ctx,
        server_id="s1",
        result="success",
        auth_mode="session_context",
        started_at=0.0,
        size_bytes=12,
    )
    assert ctx.telemetry.downloads == []

    validation_calls = []

    async def raise_validation(**kwargs):
        validation_calls.append(kwargs)
        raise HTTPException(status_code=kwargs["status_code"], detail=kwargs["detail"])

    monkeypatch.setattr(rr, "_raise_upload_validation_http_exception", raise_validation)

    def unknown_server(**kwargs):
        raise HTTPException(status_code=404, detail="unknown")

    monkeypatch.setattr(rr, "raise_unknown_server_if_missing", unknown_server)
    with pytest.raises(HTTPException):
        await rr._validate_upload_request_inputs(
            context=ctx,
            server_id="sx",
            request=_request(headers={"mcp-session-id": "sess"}),
            file=[_UploadFile("a")],
            sha256=None,
        )

    monkeypatch.setattr(rr, "raise_unknown_server_if_missing", lambda **kwargs: None)
    with pytest.raises(HTTPException):
        await rr._validate_upload_request_inputs(
            context=ctx,
            server_id="s1",
            request=_request(headers={}),
            file=[_UploadFile("a")],
            sha256=None,
        )

    def fail_closed(**kwargs):
        raise HTTPException(status_code=503, detail="closed")

    monkeypatch.setattr(rr, "raise_fail_closed_if_rejecting", fail_closed)
    with pytest.raises(HTTPException):
        await rr._validate_upload_request_inputs(
            context=ctx,
            server_id="s1",
            request=_request(headers={"mcp-session-id": "sess"}),
            file=[_UploadFile("a")],
            sha256=None,
        )

    monkeypatch.setattr(rr, "raise_fail_closed_if_rejecting", lambda **kwargs: None)
    with pytest.raises(HTTPException):
        await rr._validate_upload_request_inputs(
            context=ctx,
            server_id="s1",
            request=_request(headers={"mcp-session-id": "sess"}),
            file=[],
            sha256=None,
        )

    def bad_sha_count(**kwargs):
        raise HTTPException(status_code=400, detail="mismatch")

    monkeypatch.setattr(rr, "validate_sha256_count", bad_sha_count)
    with pytest.raises(HTTPException):
        await rr._validate_upload_request_inputs(
            context=ctx,
            server_id="s1",
            request=_request(headers={"mcp-session-id": "sess"}),
            file=[_UploadFile("a")],
            sha256=["aa"],
        )

    monkeypatch.setattr(rr, "validate_sha256_count", lambda **kwargs: None)
    monkeypatch.setattr(rr, "normalize_sha256_inputs", lambda values: values)
    session_id, files, hashes = await rr._validate_upload_request_inputs(
        context=ctx,
        server_id="s1",
        request=_request(headers={"mcp-session-id": "sess"}),
        file=[_UploadFile("a"), None],
        sha256=["aa"],
    )
    assert session_id == "sess"
    assert len(files) == 1
    assert hashes == ["aa"]
    assert validation_calls


@pytest.mark.asyncio
async def test_raise_upload_validation_http_exception(monkeypatch):
    ctx = _context()
    metric_calls = []

    async def record_upload_failure_metrics(**kwargs):
        metric_calls.append(kwargs)

    monkeypatch.setattr(rr, "record_upload_failure_metrics", record_upload_failure_metrics)
    with pytest.raises(HTTPException) as exc:
        await rr._raise_upload_validation_http_exception(
            context=ctx,
            server_id="s1",
            reason="bad_input",
            status_code=400,
            detail="bad",
        )
    assert exc.value.status_code == 400
    assert metric_calls and metric_calls[-1]["reason"] == "bad_input"


@pytest.mark.asyncio
async def test_persist_upload_records_and_process_upload_endpoint(monkeypatch):
    ctx = _context()

    async def save_one_uploaded_file(**kwargs):
        return _UploadRecord(kwargs["uploaded_file"].filename)

    monkeypatch.setattr(rr, "save_one_uploaded_file", save_one_uploaded_file)
    monkeypatch.setattr(rr.asyncio, "create_task", lambda coro: coro)

    records = await rr._persist_upload_records(
        context=ctx,
        server_id="s1",
        session_id="sess",
        files=[_UploadFile("a"), _UploadFile("b")],
        sha256_values=["h1", "h2"],
    )
    assert len(records) == 2

    async def raise_upload_error(**kwargs):
        raise HTTPException(status_code=400, detail="upload failed")

    monkeypatch.setattr(rr, "_raise_upload_http_exception_for_failure", raise_upload_error)

    async def save_mixed(**kwargs):
        if kwargs["uploaded_file"].filename == "bad":
            return RuntimeError("boom")
        return _UploadRecord("ok")

    monkeypatch.setattr(rr, "save_one_uploaded_file", save_mixed)
    with pytest.raises(HTTPException):
        await rr._persist_upload_records(
            context=ctx,
            server_id="s1",
            session_id="sess",
            files=[_UploadFile("ok"), _UploadFile("bad")],
            sha256_values=None,
        )

    ctx.session_store.ensure_exc = RuntimeError("ensure")
    with pytest.raises(HTTPException):
        await rr._persist_upload_records(
            context=ctx,
            server_id="s1",
            session_id="sess",
            files=[_UploadFile("ok")],
            sha256_values=None,
        )

    close_calls = []
    batch_calls = []

    async def close_uploaded_files(files):
        close_calls.append(files)

    async def validate_ok(**kwargs):
        return "sess", [_UploadFile("x")], ["hh"]

    async def persist_ok(**kwargs):
        return [_UploadRecord("x")]

    async def record_batch(**kwargs):
        batch_calls.append(kwargs)

    monkeypatch.setattr(rr, "_validate_upload_request_inputs", validate_ok)
    monkeypatch.setattr(rr, "_persist_upload_records", persist_ok)
    monkeypatch.setattr(rr, "record_upload_batch_metrics", record_batch)
    monkeypatch.setattr(rr, "close_uploaded_files", close_uploaded_files)

    payload = await rr._process_upload_endpoint(
        context=ctx,
        server_id="s1",
        request=_request(headers={"mcp-session-id": "sess"}),
        file=[_UploadFile("x")],
        sha256=["hh"],
    )
    assert payload["server_id"] == "s1"
    assert batch_calls
    assert close_calls

    async def validate_fail(**kwargs):
        raise HTTPException(status_code=400, detail="bad")

    monkeypatch.setattr(rr, "_validate_upload_request_inputs", validate_fail)
    with pytest.raises(HTTPException):
        await rr._process_upload_endpoint(
            context=ctx,
            server_id="s1",
            request=_request(headers={"mcp-session-id": "sess"}),
            file=[_UploadFile("x")],
            sha256=["hh"],
        )


@pytest.mark.asyncio
async def test_register_health_upload_and_route_stack(monkeypatch):
    ctx = _context(allow_artifacts=True)

    async def collect_checks(_):
        return [{"name": "s1", "ok": True}]

    monkeypatch.setattr(rr, "collect_upstream_health_checks", collect_checks)
    monkeypatch.setattr(rr, "build_healthz_payload", lambda **kwargs: ({"ok": True}, False))

    rr._register_health_route(context=ctx)
    path, _, healthz = ctx.app.get_routes[0]
    assert path == "/healthz"
    assert (await healthz()).status_code == 200

    monkeypatch.setattr(rr, "build_healthz_payload", lambda **kwargs: ({"ok": False}, True))
    assert (await healthz()).status_code == 503

    async def process_upload(**kwargs):
        return {"ok": True}

    monkeypatch.setattr(rr, "_process_upload_endpoint", process_upload)
    rr._register_upload_route(context=ctx)
    up_path, _, upload_endpoint = ctx.app.post_routes[0]
    assert up_path == ctx.upload_route
    assert await upload_endpoint("s1", _request(headers={"mcp-session-id": "sess"}), [_UploadFile("x")], ["hh"]) == {"ok": True}

    reg_artifact_calls = []
    monkeypatch.setattr(rr, "_register_artifact_download_route", lambda **kwargs: reg_artifact_calls.append(kwargs))
    rr.register_route_stack(context=ctx)
    assert reg_artifact_calls

    ctx2 = _context(allow_artifacts=False)
    reg_artifact_calls.clear()
    monkeypatch.setattr(rr, "_register_health_route", lambda **kwargs: None)
    monkeypatch.setattr(rr, "_register_upload_route", lambda **kwargs: None)
    rr.register_route_stack(context=ctx2)
    assert reg_artifact_calls == []


@pytest.mark.asyncio
async def test_artifact_download_route_branches(monkeypatch):
    ctx = _context(allow_artifacts=True)
    info_calls = []
    metric_calls = []

    monkeypatch.setattr(rr.logger, "info", lambda *args, **kwargs: info_calls.append((args, kwargs)))

    async def record_metrics(**kwargs):
        metric_calls.append(kwargs)

    monkeypatch.setattr(rr, "_record_artifact_download_metrics", record_metrics)
    monkeypatch.setattr(rr, "raise_unknown_server_if_missing", lambda **kwargs: None)
    monkeypatch.setattr(rr, "raise_fail_closed_if_rejecting", lambda **kwargs: None)
    monkeypatch.setattr(rr, "resolve_artifact_for_read", lambda **kwargs: __import__("asyncio").sleep(0, result=_ArtifactRecord()))

    rr._register_artifact_download_route(context=ctx)
    assert info_calls
    _, _, download = ctx.app.get_routes[-1]

    req_mismatch = _request(path="/artifacts", headers={}, query={})
    with pytest.raises(HTTPException) as mismatch:
        await download("s1", "sess", "a1", req_mismatch, "artifact.txt")
    assert mismatch.value.status_code == 403
    assert metric_calls[-1]["result"] == "session_mismatch"

    req_signed = _request(path="/artifacts", headers={}, query={}, signed=True)
    ok = await download("s1", "sess", "a1", req_signed, "artifact.txt")
    assert ok.filename == "artifact.txt"
    assert metric_calls[-1]["result"] == "success"

    def fail_closed(**kwargs):
        raise HTTPException(status_code=503, detail="closed")

    monkeypatch.setattr(rr, "raise_fail_closed_if_rejecting", fail_closed)
    with pytest.raises(HTTPException):
        await download("s1", "sess", "a1", req_signed, "artifact.txt")
    assert metric_calls[-1]["result"] == "fail_closed"

    monkeypatch.setattr(rr, "raise_fail_closed_if_rejecting", lambda **kwargs: None)

    async def not_found(**kwargs):
        raise ArtifactNotFoundError("missing")

    monkeypatch.setattr(rr, "resolve_artifact_for_read", not_found)
    with pytest.raises(HTTPException) as notfound:
        await download("s1", "sess", "a1", req_signed, "artifact.txt")
    assert notfound.value.status_code == 404
    assert metric_calls[-1]["result"] == "not_found"

    async def file_missing(**kwargs):
        raise ArtifactFileMissingError("gone")

    monkeypatch.setattr(rr, "resolve_artifact_for_read", file_missing)
    with pytest.raises(HTTPException):
        await download("s1", "sess", "a1", req_signed, "artifact.txt")
    assert metric_calls[-1]["result"] == "file_missing"

    async def name_mismatch(**kwargs):
        raise ArtifactFilenameMismatchError("bad")

    monkeypatch.setattr(rr, "resolve_artifact_for_read", name_mismatch)
    with pytest.raises(HTTPException):
        await download("s1", "sess", "a1", req_signed, "artifact.txt")
    assert metric_calls[-1]["result"] == "filename_mismatch"

    async def unknown_error(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(rr, "resolve_artifact_for_read", unknown_error)

    async def runtime_switched(**kwargs):
        return True

    monkeypatch.setattr(rr, "apply_runtime_failure_policy_if_persistent_backend", runtime_switched)

    def raise_backend(*, cause):
        raise HTTPException(status_code=503, detail="backend")

    monkeypatch.setattr(rr, "raise_persistence_backend_operation_failed_http_exception", raise_backend)
    with pytest.raises(HTTPException) as backend:
        await download("s1", "sess", "a1", req_signed, "artifact.txt")
    assert backend.value.status_code == 503
    assert metric_calls[-1]["result"] == "persistence_unavailable"

    async def runtime_not_switched(**kwargs):
        return False

    monkeypatch.setattr(rr, "apply_runtime_failure_policy_if_persistent_backend", runtime_not_switched)
    with pytest.raises(RuntimeError):
        await download("s1", "sess", "a1", req_signed, "artifact.txt")
    assert metric_calls[-1]["result"] == "error"
