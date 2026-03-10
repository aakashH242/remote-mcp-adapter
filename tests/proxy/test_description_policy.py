from __future__ import annotations

from types import SimpleNamespace

from remote_mcp_adapter.proxy import description_policy as dp


def _config(*, shorten_descriptions=False, short_description_max_tokens=16):
    return SimpleNamespace(
        core=SimpleNamespace(
            shorten_descriptions=shorten_descriptions,
            short_description_max_tokens=short_description_max_tokens,
        )
    )


def _server(*, shorten_descriptions=None, short_description_max_tokens=None):
    return SimpleNamespace(
        shorten_descriptions=shorten_descriptions,
        short_description_max_tokens=short_description_max_tokens,
    )


def test_resolve_description_policy_uses_server_override():
    policy = dp.resolve_description_policy(
        config=_config(shorten_descriptions=False, short_description_max_tokens=16),
        server=_server(shorten_descriptions=True, short_description_max_tokens=9),
    )

    assert policy.shorten is True
    assert policy.max_tokens == 9


def test_build_upload_consumer_description_keeps_full_description_when_disabled():
    description = dp.build_upload_consumer_description(
        upstream_description="Upload an image to the browser and attach it to the current page.",
        adapter_note="Adapter note.",
        config=_config(shorten_descriptions=False),
        server=_server(),
    )

    assert description.startswith("Upload an image")
    assert description.endswith("Adapter note.")


def test_build_upload_consumer_description_shortens_and_adds_semantic_hints():
    description = dp.build_upload_consumer_description(
        upstream_description=(
            "Upload an image file to the browser page and attach it to the selected form field. "
            "Use this for screenshots and media inputs."
        ),
        adapter_note="Adapter note.",
        config=_config(shorten_descriptions=True, short_description_max_tokens=8),
        server=_server(),
    )

    assert "Purpose: Upload an image file to the browser page..." in description
    assert "Key actions:" in description
    assert "Key objects:" in description
    assert description.endswith("Adapter note.")


def test_build_upload_consumer_description_falls_back_to_adapter_note_when_upstream_missing():
    description = dp.build_upload_consumer_description(
        upstream_description=None,
        adapter_note="Adapter note.",
        config=_config(shorten_descriptions=True),
        server=_server(),
    )

    assert description == "Adapter note."
