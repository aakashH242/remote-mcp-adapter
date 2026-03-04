from __future__ import annotations

from types import SimpleNamespace

from remote_mcp_adapter.proxy.upload_helpers import (
    build_artifact_download_path,
    build_server_upload_path,
    derive_public_base_url,
)


def _config(*, public_base_url: str = "", host: str = "127.0.0.1", port: int = 8000):
    return SimpleNamespace(core=SimpleNamespace(public_base_url=public_base_url, host=host, port=port))


def _context_with_request(*, headers: dict[str, str], base_url: str):
    request = SimpleNamespace(headers=headers, base_url=base_url)
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def test_build_server_upload_path_adds_leading_slash_for_relative_base():
    assert build_server_upload_path("uploads", "srv") == "/uploads/srv"


def test_build_server_upload_path_handles_root_base_path():
    assert build_server_upload_path("/", "srv") == "/srv"


def test_build_server_upload_path_trims_trailing_slash():
    assert build_server_upload_path("/uploads/", "srv") == "/uploads/srv"


def test_build_artifact_download_path_url_encodes_filename():
    path = build_artifact_download_path("srv", "sess", "a1", "file name+v1.txt")
    assert path == "/artifacts/srv/sess/a1/file%20name%2Bv1.txt"


def test_derive_public_base_url_prefers_config_value_and_strips_trailing_slash():
    config = _config(public_base_url="https://public.example.com/")
    assert derive_public_base_url(config) == "https://public.example.com"


def test_derive_public_base_url_uses_forwarded_headers_when_available():
    config = _config(public_base_url="")
    context = _context_with_request(
        headers={"x-forwarded-proto": "https", "x-forwarded-host": "edge.example.com"},
        base_url="http://internal.local:8080/",
    )
    assert derive_public_base_url(config, context) == "https://edge.example.com"


def test_derive_public_base_url_uses_request_base_url_without_forwarded_headers():
    config = _config(public_base_url="")
    context = _context_with_request(headers={}, base_url="http://api.local:9000/")
    assert derive_public_base_url(config, context) == "http://api.local:9000"


def test_derive_public_base_url_uses_host_port_when_no_context():
    config = _config(public_base_url="", host="example.local", port=7777)
    assert derive_public_base_url(config, None) == "http://example.local:7777"


def test_derive_public_base_url_maps_unspecified_host_to_loopback():
    config_empty = _config(public_base_url="", host="", port=7000)
    config_wildcard = _config(public_base_url="", host="0.0.0.0", port=7001)
    assert derive_public_base_url(config_empty, None) == "http://127.0.0.1:7000"
    assert derive_public_base_url(config_wildcard, None) == "http://127.0.0.1:7001"


def test_derive_public_base_url_ignores_context_when_request_context_missing():
    config = _config(public_base_url="", host="host.local", port=8088)
    context = SimpleNamespace(request_context=None)
    assert derive_public_base_url(config, context) == "http://host.local:8088"
