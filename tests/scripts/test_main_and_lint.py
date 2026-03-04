from __future__ import annotations

from types import SimpleNamespace

from remote_mcp_adapter import __main__ as cli
from remote_mcp_adapter.scripts import lint


def test_parse_args_defaults_and_overrides(monkeypatch):
    monkeypatch.setenv("MCP_ADAPTER_CONFIG", "c.yaml")
    monkeypatch.setattr("sys.argv", ["prog"])
    args = cli._parse_args()
    assert args.config == "c.yaml"
    assert args.host is None and args.port is None and args.log_level is None

    monkeypatch.setattr("sys.argv", ["prog", "--config", "x.yaml", "--host", "0.0.0.0", "--port", "9000", "--log-level", "WARNING"])
    args2 = cli._parse_args()
    assert args2.config == "x.yaml"
    assert args2.host == "0.0.0.0"
    assert args2.port == 9000
    assert args2.log_level == "WARNING"


def test_main_invokes_uvicorn(monkeypatch):
    cfg = SimpleNamespace(core=SimpleNamespace(host="127.0.0.1", port=8000, log_level="INFO"))
    calls = {}

    monkeypatch.setattr(cli, "_parse_args", lambda: SimpleNamespace(config="c.yaml", host=None, port=None, log_level=None))
    monkeypatch.setattr(cli, "load_config", lambda path: cfg)
    monkeypatch.setattr(cli, "create_app", lambda config: "app")
    monkeypatch.setenv("MCP_ADAPTER_SHUTDOWN_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("MCP_ADAPTER_KEEP_ALIVE_TIMEOUT_SECONDS", "7")

    def fake_run(app, **kwargs):
        calls["app"] = app
        calls.update(kwargs)

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    cli.main()

    assert calls["app"] == "app"
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 8000
    assert calls["log_level"] == "info"
    assert calls["timeout_graceful_shutdown"] == 12
    assert calls["timeout_keep_alive"] == 7


def test_lint_run_and_main(monkeypatch):
    class _Done:
        returncode = 3

    seen = {}

    def fake_subprocess_run(command, check, timeout):
        seen["command"] = command
        seen["check"] = check
        seen["timeout"] = timeout
        return _Done()

    monkeypatch.setattr(lint.subprocess, "run", fake_subprocess_run)
    assert lint._run(["echo", "x"]) == 3
    assert seen["check"] is False

    calls = []

    def fake_run(command):
        calls.append(command)
        if command[0] == "ruff" and "--fix" in command:
            return 0
        if command[0] == "black":
            return 0
        if command[0] == "ruff":
            return 1
        return 0

    monkeypatch.setattr(lint, "_run", fake_run)
    monkeypatch.setattr("sys.argv", ["lint", "--fix"])
    assert lint.main() == 0

    monkeypatch.setattr("sys.argv", ["lint"])
    assert lint.main() == 1
    assert any(cmd[0] == "black" for cmd in calls)
    assert any(cmd[0] == "ruff" and "--fix" not in cmd for cmd in calls)

    monkeypatch.setattr(lint, "_run", lambda command: 0)
    monkeypatch.setattr("sys.argv", ["lint"])
    assert lint.main() == 0
