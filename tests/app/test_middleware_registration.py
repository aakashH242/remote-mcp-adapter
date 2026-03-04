from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastmcp.exceptions import ToolError

from remote_mcp_adapter.app import middleware_registration as mr
from remote_mcp_adapter.core import PersistenceUnavailableError


class _DummyApp:
    def __init__(self):
        self.middlewares = []

    def middleware(self, method: str):
        def decorator(fn):
            self.middlewares.append((method, fn.__name__, fn))
            return fn

        return decorator


class _Telemetry:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.rejections = []
        self.requests = []

    async def record_request_rejection(self, **kwargs):
        self.rejections.append(kwargs)

    async def record_http_request(self, **kwargs):
        self.requests.append(kwargs)


class _UploadCreds:
    def __init__(self, enabled=True, valid=True, raises=False):
        self.enabled = enabled
        self.valid = valid
        self.raises = raises

    async def validate_and_consume(self, **kwargs):
        if self.raises:
            raise RuntimeError("backend down")
        return self.valid


class _ArtifactCreds:
    def __init__(self, enabled=True, valid=True):
        self.enabled = enabled
        self.valid = valid

    def validate(self, **kwargs):
        return self.valid


class _SessionStore:
    def __init__(self):
        self.begin_calls = []
        self.end_calls = []
        self.begin_exc = None
        self.end_exc = None

    async def begin_in_flight(self, server_id, session_id):
        self.begin_calls.append((server_id, session_id))
        if self.begin_exc:
            raise self.begin_exc

    async def end_in_flight(self, server_id, session_id):
        self.end_calls.append((server_id, session_id))
        if self.end_exc:
            raise self.end_exc


class _CancellationObserver:
    def __init__(self):
        self.calls = []

    async def register_requests(self, ctx, reqs):
        self.calls.append(("register", ctx, reqs))

    async def observe_cancellations(self, ctx, cans):
        self.calls.append(("observe", ctx, cans))

    async def complete_requests(self, ctx, reqs):
        self.calls.append(("complete", ctx, reqs))


def _request(path="/x", method="GET", headers=None, query=None):
    async def _body():
        return b"{}"

    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        headers=headers or {},
        query_params=query or {},
        state=SimpleNamespace(),
        body=_body,
    )


def _context():
    app = _DummyApp()
    return SimpleNamespace(
        app=app,
        resolved_config=SimpleNamespace(
            core=SimpleNamespace(auth=SimpleNamespace(enabled=True)),
        ),
        persistence_policy=SimpleNamespace(should_reject_stateful_requests=lambda: False),
        runtime_ref={"current": object()},
        session_store=_SessionStore(),
        upstream_health={},
        mount_path_to_server_id={"/mcp/s1": "s1"},
        cancellation_observer=_CancellationObserver(),
        upload_path_prefix="/upload",
        upload_credentials=None,
        artifact_download_credentials=None,
        telemetry=None,
        build_memory_persistence_runtime=lambda: object(),
    )


def test_telemetry_server_id_for_path_and_known_servers(monkeypatch):
    known = {"s1"}
    mounts = {"/mcp/s1": "s1", "/mcp/sx": "sx"}

    assert mr._telemetry_server_id_for_path(path="/mcp/s1/tools", mount_path_to_server_id=mounts, upload_path_prefix="/upload", known_server_ids=known) == "s1"
    assert mr._telemetry_server_id_for_path(path="/mcp/sx/tools", mount_path_to_server_id=mounts, upload_path_prefix="/upload", known_server_ids=known) == "unknown"

    monkeypatch.setattr(mr, "parse_artifact_download_path", lambda path: ("s1", "sess", "a", "f") if path.startswith("/artifacts") else None)
    assert mr._telemetry_server_id_for_path(path="/artifacts/s1/s/a/f", mount_path_to_server_id=mounts, upload_path_prefix="/upload", known_server_ids=known) == "s1"

    assert mr._telemetry_server_id_for_path(path="/upload/s1", mount_path_to_server_id=mounts, upload_path_prefix="/upload", known_server_ids=known) == "s1"
    assert mr._telemetry_server_id_for_path(path="/upload/sx", mount_path_to_server_id=mounts, upload_path_prefix="/upload", known_server_ids=known) == "unknown"
    assert mr._telemetry_server_id_for_path(path="/uploadx/s1", mount_path_to_server_id=mounts, upload_path_prefix="/upload", known_server_ids=known) == "global"
    assert mr._telemetry_server_id_for_path(path="/public", mount_path_to_server_id=mounts, upload_path_prefix="/upload", known_server_ids=known) == "global"

    ctx = _context()
    ctx.upstream_health = {"s2": object()}
    assert mr._known_server_ids(ctx) == {"s1", "s2"}


@pytest.mark.asyncio
async def test_rejection_metric_and_response_helpers(monkeypatch):
    ctx = _context()
    req = _request("/mcp/s1/tools")

    await mr._record_request_rejection_metrics(context=ctx, request=req, reason="r", status_code=400)

    ctx.telemetry = _Telemetry(enabled=False)
    await mr._record_request_rejection_metrics(context=ctx, request=req, reason="r", status_code=400)
    assert ctx.telemetry.rejections == []

    ctx.telemetry = _Telemetry(enabled=True)
    await mr._record_request_rejection_metrics(context=ctx, request=req, reason="r", status_code=400)
    assert ctx.telemetry.rejections[0]["reason"] == "r"

    response = await mr._rejection_json_response(
        context=ctx,
        request=req,
        reason="r2",
        status_code=403,
        content={"detail": "x"},
        server_id="s1",
    )
    assert isinstance(response, JSONResponse)
    assert response.status_code == 403

    auth_calls = []
    monkeypatch.setattr(mr, "record_auth_rejection", lambda **kwargs: auth_calls.append(kwargs) or __import__("asyncio").sleep(0))
    auth_response = await mr._auth_rejection_json_response(
        context=ctx,
        route_group="/g",
        reason="bad",
        server_id="s1",
        status_code=401,
        detail="d",
    )
    assert auth_response.status_code == 401
    assert auth_calls


def test_authorize_signed_artifact_download(monkeypatch):
    ctx = _context()
    req = _request(path="/x", method="GET")
    assert mr._authorize_signed_artifact_download(context=ctx, request=req) is False

    monkeypatch.setattr(mr, "parse_artifact_download_path", lambda path: ("s1", "sess", "a", "f"))
    req2 = _request(path="/artifacts/s1/sess/a/f", method="POST")
    assert mr._authorize_signed_artifact_download(context=ctx, request=req2) is False

    ctx.resolved_config.core.auth.enabled = False
    req3 = _request(path="/artifacts/s1/sess/a/f", method="GET")
    assert mr._authorize_signed_artifact_download(context=ctx, request=req3) is False

    ctx.resolved_config.core.auth.enabled = True
    ctx.artifact_download_credentials = _ArtifactCreds(enabled=False, valid=True)
    assert mr._authorize_signed_artifact_download(context=ctx, request=req3) is False

    ctx.artifact_download_credentials = _ArtifactCreds(enabled=True, valid=False)
    assert mr._authorize_signed_artifact_download(context=ctx, request=req3) is False

    ctx.artifact_download_credentials = _ArtifactCreds(enabled=True, valid=True)
    assert mr._authorize_signed_artifact_download(context=ctx, request=req3) is True
    assert getattr(req3.state, "artifact_download_signed_auth", False) is True


@pytest.mark.asyncio
async def test_evaluate_signed_upload_auth_paths(monkeypatch):
    ctx = _context()
    req = _request(path="/public")
    assert await mr._evaluate_signed_upload_auth(context=ctx, request=req, route_group="/g", telemetry_server_id="s1") == (False, None)

    req_upload_get = _request(path="/upload/s1", method="GET", headers={"mcp-session-id": "sess"}, query={})
    assert await mr._evaluate_signed_upload_auth(context=ctx, request=req_upload_get, route_group="/g", telemetry_server_id="s1") == (False, None)

    req_upload = _request(path="/upload/s1", method="POST", headers={"mcp-session-id": "sess"}, query={})
    ctx.resolved_config.core.auth.enabled = False
    assert await mr._evaluate_signed_upload_auth(context=ctx, request=req_upload, route_group="/g", telemetry_server_id="s1") == (False, None)

    ctx.resolved_config.core.auth.enabled = True
    assert await mr._evaluate_signed_upload_auth(context=ctx, request=req_upload, route_group="/g", telemetry_server_id="s1") == (False, None)

    ctx.upload_credentials = _UploadCreds(enabled=False, valid=True)
    assert await mr._evaluate_signed_upload_auth(context=ctx, request=req_upload, route_group="/g", telemetry_server_id="s1") == (False, None)

    ctx.upload_credentials = _UploadCreds(enabled=True, valid=True)
    ok = await mr._evaluate_signed_upload_auth(context=ctx, request=req_upload, route_group="/g", telemetry_server_id="s1")
    assert ok == (True, None)

    ctx.upload_credentials = _UploadCreds(enabled=True, valid=False)
    bad, resp = await mr._evaluate_signed_upload_auth(context=ctx, request=req_upload, route_group="/g", telemetry_server_id="s1")
    assert bad is False and isinstance(resp, JSONResponse) and resp.status_code == 403

    ctx.upload_credentials = _UploadCreds(enabled=True, raises=True)
    bad2, resp2 = await mr._evaluate_signed_upload_auth(context=ctx, request=req_upload, route_group="/g", telemetry_server_id="s1")
    assert bad2 is False and isinstance(resp2, JSONResponse) and resp2.status_code == 503

    ctx.upload_path_prefix = "/upload"
    req_non_upload = _request(path="/uploadx/s1", method="POST", headers={"mcp-session-id": "sess"}, query={})
    assert await mr._evaluate_signed_upload_auth(context=ctx, request=req_non_upload, route_group="/g", telemetry_server_id="s1") == (False, None)


@pytest.mark.asyncio
async def test_in_flight_rejection_helpers(monkeypatch):
    ctx = _context()
    req = _request(path="/mcp/s1/tools")

    ctx.persistence_policy = SimpleNamespace(should_reject_stateful_requests=lambda: False)
    assert await mr._reject_in_flight_for_fail_closed(context=ctx, request=req, matched_server_id="s1") is None
    ctx.persistence_policy = SimpleNamespace(should_reject_stateful_requests=lambda: True)
    assert await mr._reject_in_flight_for_fail_closed(context=ctx, request=req, matched_server_id=None) is None

    marker = JSONResponse(status_code=503, content={"detail": "x"})
    monkeypatch.setattr(mr, "_rejection_json_response", lambda **kwargs: __import__("asyncio").sleep(0, result=marker))
    assert await mr._reject_in_flight_for_fail_closed(context=ctx, request=req, matched_server_id="s1") is marker

    ctx.upstream_health = {}
    assert await mr._reject_in_flight_for_breaker(context=ctx, request=req, matched_server_id=None) is None
    assert await mr._reject_in_flight_for_breaker(context=ctx, request=req, matched_server_id="s1") is None
    ctx.upstream_health = {"s1": SimpleNamespace(allow_proxy_request=lambda: __import__("asyncio").sleep(0, result=(True, None)))}
    assert await mr._reject_in_flight_for_breaker(context=ctx, request=req, matched_server_id="s1") is None
    ctx.upstream_health = {"s1": SimpleNamespace(allow_proxy_request=lambda: __import__("asyncio").sleep(0, result=(False, "blocked")))}
    assert await mr._reject_in_flight_for_breaker(context=ctx, request=req, matched_server_id="s1") is marker


@pytest.mark.asyncio
async def test_begin_end_track_in_flight_and_process_auth(monkeypatch):
    ctx = _context()
    req = _request(path="/mcp/s1/tools", headers={"mcp-session-id": "sess"})

    async def call_next(request):
        return JSONResponse(status_code=200, content={"ok": True})

    assert await mr._begin_in_flight_or_reject(context=ctx, request=req, matched_server_id="s1", session_id="sess") is None

    ctx.session_store.begin_exc = PersistenceUnavailableError("down")
    response = await mr._begin_in_flight_or_reject(context=ctx, request=req, matched_server_id="s1", session_id="sess")
    assert isinstance(response, JSONResponse) and response.status_code == 503

    ctx.session_store.begin_exc = ToolError("limit")
    response2 = await mr._begin_in_flight_or_reject(context=ctx, request=req, matched_server_id="s1", session_id="sess")
    assert isinstance(response2, JSONResponse) and response2.status_code == 429

    ctx.session_store.begin_exc = RuntimeError("x")
    monkeypatch.setattr(mr, "apply_runtime_failure_policy_if_persistent_backend", lambda **kwargs: __import__("asyncio").sleep(0, result=True))
    monkeypatch.setattr(mr, "persistence_backend_operation_failed_response", lambda: JSONResponse(status_code=503, content={"detail": "p"}))
    response3 = await mr._begin_in_flight_or_reject(context=ctx, request=req, matched_server_id="s1", session_id="sess")
    assert response3.status_code == 503

    ctx.session_store.begin_exc = RuntimeError("x")
    monkeypatch.setattr(mr, "apply_runtime_failure_policy_if_persistent_backend", lambda **kwargs: __import__("asyncio").sleep(0, result=False))
    with pytest.raises(RuntimeError):
        await mr._begin_in_flight_or_reject(context=ctx, request=req, matched_server_id="s1", session_id="sess")

    ctx.session_store.end_exc = RuntimeError("x")
    await mr._end_in_flight_best_effort(context=ctx, matched_server_id="s1", session_id="sess")

    monkeypatch.setattr(mr, "_reject_in_flight_for_fail_closed", lambda **kwargs: __import__("asyncio").sleep(0, result=None))
    monkeypatch.setattr(mr, "_reject_in_flight_for_breaker", lambda **kwargs: __import__("asyncio").sleep(0, result=None))
    monkeypatch.setattr(mr, "_begin_in_flight_or_reject", lambda **kwargs: __import__("asyncio").sleep(0, result=None))
    monkeypatch.setattr(mr, "_end_in_flight_best_effort", lambda **kwargs: __import__("asyncio").sleep(0))

    tracked = await mr._track_in_flight_request(context=ctx, request=req, call_next=call_next)
    assert tracked.status_code == 200

    req_no_session = _request(path="/mcp/s1/tools", headers={})
    tracked2 = await mr._track_in_flight_request(context=ctx, request=req_no_session, call_next=call_next)
    assert tracked2.status_code == 200

    monkeypatch.setattr(mr, "is_public_unprotected_path", lambda path: True)
    assert (await mr._process_auth_request(context=ctx, request=req, call_next=call_next)).status_code == 200

    monkeypatch.setattr(mr, "is_public_unprotected_path", lambda path: False)
    monkeypatch.setattr(mr, "is_oauth_discovery_path", lambda path: True)
    assert (await mr._process_auth_request(context=ctx, request=req, call_next=call_next)).status_code == 200

    monkeypatch.setattr(mr, "is_oauth_discovery_path", lambda path: False)
    monkeypatch.setattr(mr, "_authorize_signed_artifact_download", lambda **kwargs: True)
    assert (await mr._process_auth_request(context=ctx, request=req, call_next=call_next)).status_code == 200

    monkeypatch.setattr(mr, "_authorize_signed_artifact_download", lambda **kwargs: False)
    monkeypatch.setattr(mr, "_evaluate_signed_upload_auth", lambda **kwargs: __import__("asyncio").sleep(0, result=(True, None)))
    assert (await mr._process_auth_request(context=ctx, request=req, call_next=call_next)).status_code == 200

    reject = JSONResponse(status_code=403, content={"detail": "bad"})
    monkeypatch.setattr(mr, "_evaluate_signed_upload_auth", lambda **kwargs: __import__("asyncio").sleep(0, result=(False, reject)))
    assert (await mr._process_auth_request(context=ctx, request=req, call_next=call_next)).status_code == 403

    monkeypatch.setattr(mr, "_evaluate_signed_upload_auth", lambda **kwargs: __import__("asyncio").sleep(0, result=(False, None)))

    def raise_auth(request, config):
        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(mr, "validate_adapter_auth", raise_auth)
    auth_resp = await mr._process_auth_request(context=ctx, request=req, call_next=call_next)
    assert auth_resp.status_code == 403

    monkeypatch.setattr(mr, "validate_adapter_auth", lambda request, config: None)
    auth_ok = await mr._process_auth_request(context=ctx, request=req, call_next=call_next)
    assert auth_ok.status_code == 200


@pytest.mark.asyncio
async def test_track_in_flight_short_circuit_paths(monkeypatch):
    ctx = _context()
    req = _request(path="/mcp/s1/tools", headers={"mcp-session-id": "sess"})

    async def call_next(request):
        return JSONResponse(status_code=200, content={"ok": True})

    fail_closed = JSONResponse(status_code=503, content={"detail": "fc"})
    monkeypatch.setattr(mr, "_reject_in_flight_for_fail_closed", lambda **kwargs: __import__("asyncio").sleep(0, result=fail_closed))
    response = await mr._track_in_flight_request(context=ctx, request=req, call_next=call_next)
    assert response is fail_closed

    monkeypatch.setattr(mr, "_reject_in_flight_for_fail_closed", lambda **kwargs: __import__("asyncio").sleep(0, result=None))
    breaker = JSONResponse(status_code=503, content={"detail": "breaker"})
    monkeypatch.setattr(mr, "_reject_in_flight_for_breaker", lambda **kwargs: __import__("asyncio").sleep(0, result=breaker))
    response2 = await mr._track_in_flight_request(context=ctx, request=req, call_next=call_next)
    assert response2 is breaker

    monkeypatch.setattr(mr, "_reject_in_flight_for_breaker", lambda **kwargs: __import__("asyncio").sleep(0, result=None))
    begin_reject = JSONResponse(status_code=429, content={"detail": "limit"})
    monkeypatch.setattr(mr, "_begin_in_flight_or_reject", lambda **kwargs: __import__("asyncio").sleep(0, result=begin_reject))
    monkeypatch.setattr(mr, "_end_in_flight_best_effort", lambda **kwargs: __import__("asyncio").sleep(0))
    response3 = await mr._track_in_flight_request(context=ctx, request=req, call_next=call_next)
    assert response3 is begin_reject


@pytest.mark.asyncio
async def test_register_middleware_functions_and_stack(monkeypatch):
    ctx = _context()
    ctx.telemetry = _Telemetry(enabled=True)
    app = _DummyApp()
    ctx.app = app

    monkeypatch.setattr(mr, "_known_server_ids", lambda context: {"s1"})
    monkeypatch.setattr(mr, "_telemetry_server_id_for_path", lambda **kwargs: "s1")
    monkeypatch.setattr(mr, "route_group_for_metrics", lambda path, upload_path_prefix: "/g")
    monkeypatch.setattr(mr, "resolve_server_id_for_path", lambda path, m: "s1")
    monkeypatch.setattr(mr, "parse_mcp_envelope", lambda body: SimpleNamespace(requests=["r"], cancellations=["c"]))
    monkeypatch.setattr(mr, "is_stateful_request_path", lambda **kwargs: True)

    async def call_next(request):
        return JSONResponse(status_code=200, content={"ok": True})

    mr.register_middleware_stack(context=ctx)
    assert len(app.middlewares) == 5

    by_name = {name: fn for _, name, fn in app.middlewares}

    await by_name["request_telemetry"](_request(path="/mcp/s1/tools"), call_next)
    assert ctx.telemetry.requests[-1]["status_code"] == 200

    ctx.telemetry.enabled = False
    await by_name["request_telemetry"](_request(path="/mcp/s1/tools"), call_next)
    ctx.telemetry.enabled = True

    async def raising_next(request):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await by_name["request_telemetry"](_request(path="/mcp/s1/tools"), raising_next)

    req_cancel = _request(path="/mcp/s1/tools", method="POST", headers={"mcp-session-id": "sess"})
    async def cancel_body():
        return b"{}"

    req_cancel.body = cancel_body
    await by_name["observe_cancellation_notifications"](req_cancel, call_next)
    assert ctx.cancellation_observer.calls

    req_passthrough = _request(path="/mcp/s1/tools", method="GET", headers={})
    await by_name["observe_cancellation_notifications"](req_passthrough, call_next)

    # no session header path
    req_no_session = _request(path="/mcp/s1/tools", method="POST", headers={})
    async def no_session_body():
        return b"{}"

    req_no_session.body = no_session_body

    warning_calls = []
    monkeypatch.setattr(mr.logger, "warning", lambda *args, **kwargs: warning_calls.append((args, kwargs)))
    await by_name["observe_cancellation_notifications"](req_no_session, call_next)
    assert warning_calls

    ctx.persistence_policy = SimpleNamespace(should_reject_stateful_requests=lambda: True)
    monkeypatch.setattr(mr, "is_stateful_request_path", lambda **kwargs: False)
    pass_stateful = await by_name["persistence_fail_closed_guard"](_request(path="/mcp/s1/tools"), call_next)
    assert pass_stateful.status_code == 200

    monkeypatch.setattr(mr, "is_stateful_request_path", lambda **kwargs: True)
    rejected = await by_name["persistence_fail_closed_guard"](_request(path="/mcp/s1/tools"), call_next)
    assert rejected.status_code == 503

    ctx.persistence_policy = SimpleNamespace(should_reject_stateful_requests=lambda: False)
    ok = await by_name["persistence_fail_closed_guard"](_request(path="/mcp/s1/tools"), call_next)
    assert ok.status_code == 200

    monkeypatch.setattr(mr, "_track_in_flight_request", lambda **kwargs: __import__("asyncio").sleep(0, result=JSONResponse(status_code=201, content={})))
    tracked = await by_name["track_in_flight"](_request(path="/mcp/s1/tools"), call_next)
    assert tracked.status_code == 201

    monkeypatch.setattr(mr, "_process_auth_request", lambda **kwargs: __import__("asyncio").sleep(0, result=JSONResponse(status_code=202, content={})))
    authed = await by_name["auth_middleware"](_request(path="/mcp/s1/tools"), call_next)
    assert authed.status_code == 202
