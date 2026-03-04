from __future__ import annotations

import remote_mcp_adapter.proxy as proxy


def test_proxy_init_exports_expected_symbols():
    assert set(proxy.__all__) == {"build_proxy_map", "wire_adapters", "AdapterWireState"}
    assert callable(proxy.build_proxy_map)
    assert callable(proxy.wire_adapters)
    assert proxy.AdapterWireState.__name__ == "AdapterWireState"
