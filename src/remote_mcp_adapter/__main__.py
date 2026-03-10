"""CLI entrypoint for running the scaffolded MCP general adapter."""

from __future__ import annotations

import argparse
import os

import uvicorn

from .server import create_app
from .config.load import load_config


def _parse_args() -> argparse.Namespace:
    """Build arg parser and return parsed CLI arguments.

    Returns:
        Parsed ``argparse.Namespace`` with config, host, port, and log-level.
    """
    parser = argparse.ArgumentParser(description="Run the MCP general adapter.")
    parser.add_argument(
        "--config",
        default=os.getenv("MCP_ADAPTER_CONFIG", "/etc/remote-mcp-adapter/config.yaml"),
        help="Path to adapter YAML config.",
    )
    parser.add_argument("--host", default=None, help="Bind host. Defaults to core.host from config.")
    parser.add_argument("--port", type=int, default=None, help="Bind port. Defaults to core.port from config.")
    parser.add_argument(
        "--log-level",
        default=None,
        help="Uvicorn log level. Defaults to core.log_level from config.",
    )
    return parser.parse_args()


def main() -> None:
    """Load scaffold config, create app, and run uvicorn.

    Reads the config file specified by CLI args or the
    ``MCP_ADAPTER_CONFIG`` environment variable, constructs the FastAPI
    application, and starts the uvicorn server.
    """
    args = _parse_args()
    config = load_config(args.config)
    app = create_app(config)
    shutdown_timeout_seconds = int(os.getenv("MCP_ADAPTER_SHUTDOWN_TIMEOUT_SECONDS", "10"))
    keep_alive_timeout_seconds = int(os.getenv("MCP_ADAPTER_KEEP_ALIVE_TIMEOUT_SECONDS", "5"))
    uvicorn.run(
        app,
        host=args.host or config.core.host,
        port=args.port or config.core.port,
        log_level=(args.log_level or config.core.log_level).lower(),
        ws="websockets-sansio",
        timeout_keep_alive=keep_alive_timeout_seconds,
        timeout_graceful_shutdown=shutdown_timeout_seconds,
    )


if __name__ == "__main__":
    main()
