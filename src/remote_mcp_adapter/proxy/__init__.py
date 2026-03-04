"""Proxy factory and hook wiring for upstream MCP servers."""

from .factory import build_proxy_map
from .hooks import AdapterWireState, wire_adapters

__all__ = ["build_proxy_map", "wire_adapters", "AdapterWireState"]
