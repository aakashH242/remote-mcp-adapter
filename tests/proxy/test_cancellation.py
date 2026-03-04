from __future__ import annotations

from types import SimpleNamespace

import pytest

from remote_mcp_adapter.proxy import cancellation as c


def test_normalize_request_id_variants_and_numeric_fallback(monkeypatch):
    assert c._normalize_request_id(True) is None
    assert c._normalize_request_id(5) == 5
    assert c._normalize_request_id("  ") is None
    assert c._normalize_request_id(" 123 ") == 123
    assert c._normalize_request_id("abc") == "abc"
    assert c._normalize_request_id(1.5) is None

    class MatchAll:
        def fullmatch(self, value):
            return True

    monkeypatch.setattr(c, "_NUMERIC_REQUEST_ID_PATTERN", MatchAll())
    assert c._normalize_request_id("not-an-int") == "not-an-int"


def test_normalize_reason_and_message_shape_helpers():
    assert c._normalize_reason(1) is None
    assert c._normalize_reason("   ") is None
    assert c._normalize_reason(" ok ") == "ok"

    assert c._is_task_augmented(None) is False
    assert c._is_task_augmented({}) is False
    assert c._is_task_augmented({"task": 1}) is False
    assert c._is_task_augmented({"task": {"id": "t"}}) is True

    assert c._is_jsonrpc_request({"id": 1, "method": "m"}) is True
    assert c._is_jsonrpc_request({"id": 1}) is False

    assert c._iter_messages({"id": 1}) == [{"id": 1}]
    assert c._iter_messages([{"id": 1}, 2, "x", {"id": 2}]) == [{"id": 1}, {"id": 2}]
    assert c._iter_messages("bad") == []


def test_parse_mcp_envelope_handles_empty_invalid_and_unicode_errors():
    parsed_empty = c.parse_mcp_envelope(b"")
    assert parsed_empty.requests == []
    assert parsed_empty.cancellations == []

    parsed_invalid = c.parse_mcp_envelope(b"{not-json")
    assert parsed_invalid.requests == []
    assert parsed_invalid.cancellations == []

    parsed_bad_unicode = c.parse_mcp_envelope(b"\xff")
    assert parsed_bad_unicode.requests == []
    assert parsed_bad_unicode.cancellations == []


def test_parse_mcp_envelope_extracts_requests_and_cancellations():
    raw = b"""
[
  {"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":" 5 ","reason":"  bye "}},
  {"jsonrpc":"2.0","method":"notifications/cancelled","params":"bad"},
  {"jsonrpc":"2.0","id":"10","method":"tool/run","params":{"task":{"id":"t1"}}},
  {"jsonrpc":"2.0","id":11,"method":"initialize"},
  {"jsonrpc":"2.0","id":true,"method":"bad"},
  {"jsonrpc":"2.0","method":"notif-only"},
  {"jsonrpc":"2.0","id":99,"method":"notifications/cancelled","params":{}}
]
"""
    parsed = c.parse_mcp_envelope(raw)

    assert [(req.request_id, req.method, req.is_task_augmented) for req in parsed.requests] == [
        (10, "tool/run", True),
        (11, "initialize", False),
        (99, "notifications/cancelled", False),
    ]
    assert [(n.request_id, n.reason) for n in parsed.cancellations] == [(5, "bye"), (None, None)]


@pytest.mark.asyncio
async def test_cancellation_observer_register_and_complete_requests():
    observer = c.CancellationObserver()
    ctx = c.ProxySessionContext(server_id="srv", session_id="sess")
    reqs = [
        c.InboundRequest(request_id=1, method="initialize", is_task_augmented=False),
        c.InboundRequest(request_id="2", method="tool", is_task_augmented=True),
    ]

    await observer.register_requests(ctx, [])
    await observer.register_requests(ctx, reqs)
    assert ("srv", "sess", 1) in observer._in_flight
    assert ("srv", "sess", "2") in observer._in_flight

    await observer.complete_requests(ctx, [])
    await observer.complete_requests(ctx, [c.InboundRequest(request_id=1, method="initialize", is_task_augmented=False)])
    assert ("srv", "sess", 1) not in observer._in_flight


@pytest.mark.asyncio
async def test_cancellation_observer_observe_cancellations_all_paths(monkeypatch):
    observer = c.CancellationObserver()
    ctx = c.ProxySessionContext(server_id="srv", session_id="sess")

    await observer.register_requests(
        ctx,
        [
            c.InboundRequest(request_id=1, method="initialize", is_task_augmented=False),
            c.InboundRequest(request_id=2, method="tool/run", is_task_augmented=True),
            c.InboundRequest(request_id=3, method="tool/run", is_task_augmented=False),
        ],
    )

    events: list[tuple[str, str, dict]] = []

    def _capture(level):
        def _fn(message, *, extra):
            events.append((level, message, extra))

        return _fn

    monkeypatch.setattr(c.logger, "warning", _capture("warning"))
    monkeypatch.setattr(c.logger, "debug", _capture("debug"))
    monkeypatch.setattr(c.logger, "info", _capture("info"))

    await observer.observe_cancellations(ctx, [])
    await observer.observe_cancellations(
        ctx,
        [
            c.CancellationNotification(request_id=None, reason="missing"),
            c.CancellationNotification(request_id=1, reason="init"),
            c.CancellationNotification(request_id=0, reason="zero"),
            c.CancellationNotification(request_id=999, reason="unknown"),
            c.CancellationNotification(request_id=2, reason="task"),
            c.CancellationNotification(request_id=3, reason="normal"),
        ],
    )

    levels = [level for level, _, _ in events]
    assert levels == ["warning", "warning", "warning", "debug", "warning", "info"]
    assert any(msg.startswith("Malformed cancellation notification") for _, msg, _ in events)
    assert any("initialize must not be cancelled" in msg for _, msg, _ in events)
    assert any("request id 0" in msg for _, msg, _ in events)
    assert any("unknown or already-completed" in msg for _, msg, _ in events)
    assert any("Task-augmented request cancelled" in msg for _, msg, _ in events)
    assert any(msg.startswith("Observed cancellation notification") for _, msg, _ in events)
    assert events[-1][2]["request_id"] == 3
