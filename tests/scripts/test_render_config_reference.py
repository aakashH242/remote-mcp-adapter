from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from remote_mcp_adapter.scripts import config_reference_builder as builder
from remote_mcp_adapter.scripts import render_config_reference as rc


def test_parse_template_fields_keeps_nested_list_paths():
    parsed = builder.parse_template_fields("""
servers:
  - # Unique identifier for this server entry.
    id: "playwright"
    upstream:
      # Full URL of the upstream MCP endpoint.
      url: "http://localhost:8931/mcp"
""".strip())

    assert [field.path for field in parsed] == ["servers", "servers[].id", "servers[].upstream", "servers[].upstream.url"]


def test_build_output_renders_structured_reference_and_template_appendix():
    rendered = rc._build_output("""
# core -- runtime behaviour
core:
  # IP address the HTTP server binds to.
  # Optional. Default: "0.0.0.0"
  host: "0.0.0.0"
""".strip())

    assert rendered.startswith("# Detailed Reference")
    assert "## Sections" in rendered
    assert "## `core`" in rendered
    assert '??? example "Show example snippet"' in rendered
    assert (
        "    ```yaml\n"
        "    core:\n"
        "      # IP address the HTTP server binds to.\n"
        '      # Optional. Default: "0.0.0.0"\n'
        '      host: "0.0.0.0"\n'
        "    ```" in rendered
    )
    assert "<table>" in rendered
    assert "<th>Field</th>" in rendered
    assert (
        "<td><code>host</code></td>\n"
        "      <td><code>string</code></td>\n"
        "      <td>optional</td>\n"
        "      <td>&quot;0.0.0.0&quot;</td>\n"
        "      <td>-</td>\n"
        "      <td>-</td>\n"
        "      <td>IP address the HTTP server binds to.</td>"
    ) in rendered
    assert "## Full template appendix" in rendered
    assert '??? example "Show full commented template"' in rendered
    assert rendered.rstrip().endswith("start with the easiest opinionated profile.")


def test_build_output_skips_leading_example_comment_paragraphs():
    rendered = rc._build_output("""
# core -- runtime behaviour
core:
  # Example field
  example_field: null
  # example_field: "demo"
  #
  # Actual field behaviour.
  # Optional. Default: null.
  next_field: null
""".strip())

    assert (
        "<td><code>next_field</code></td>\n"
        "      <td><code>null</code></td>\n"
        "      <td>optional</td>\n"
        "      <td>null</td>\n"
        "      <td>-</td>\n"
        "      <td>-</td>\n"
        "      <td>Actual field behaviour.</td>"
    ) in rendered


def test_build_output_adds_common_overrides_column():
    rendered = rc._build_output("""
# core -- runtime behaviour
core:
  defaults:
    # Timeout in seconds for upstream tool calls.
    # Can be overridden per-server and per-adapter.
    # Optional. Default: 60. Allowed: > 0 or null.
    tool_call_timeout_seconds: 60
servers:
  - id: "playwright"
""".strip())

    assert "<th>Common Overrides</th>" in rendered
    assert (
        "<td><code>defaults.tool_call_timeout_seconds</code></td>\n"
        "      <td><code>number</code></td>\n"
        "      <td>optional</td>\n"
        "      <td>60</td>\n"
        "      <td>&gt; 0 or null</td>\n"
        "      <td><code>servers[].tool_defaults.tool_call_timeout_seconds</code>; "
        "<code>servers[].adapters[].overrides.tool_call_timeout_seconds</code></td>\n"
        "      <td>Timeout in seconds for upstream tool calls.</td>"
    ) in rendered


def test_build_output_extracts_multiline_server_override_targets():
    rendered = rc._build_output("""
# core -- runtime behaviour
core:
  # When true, the server exposes FastMCP Code Mode instead of the full direct tool list.
  #
  # This is the global default. Individual servers can override it with
  # servers[].code_mode_enabled.
  #
  # Optional. Default: false.
  code_mode_enabled: false
  #
  # Maximum token budget used when core.shorten_descriptions=true.
  #
  # Individual servers can override this with
  # servers[].short_description_max_tokens.
  #
  # Optional. Default: 16. Allowed: > 0.
  short_description_max_tokens: 16
servers:
  - id: "playwright"
""".strip())

    assert (
        "<td><code>code_mode_enabled</code></td>\n"
        "      <td><code>boolean</code></td>\n"
        "      <td>optional</td>\n"
        "      <td>false</td>\n"
        "      <td>-</td>\n"
        "      <td><code>servers[].code_mode_enabled</code></td>\n"
        "      <td>When true, the server exposes FastMCP Code Mode instead of the full direct tool list.</td>"
    ) in rendered
    assert (
        "<td><code>short_description_max_tokens</code></td>\n"
        "      <td><code>number</code></td>\n"
        "      <td>optional</td>\n"
        "      <td>16</td>\n"
        "      <td>&gt; 0</td>\n"
        "      <td><code>servers[].short_description_max_tokens</code></td>\n"
        "      <td>Maximum token budget used when core.shorten_descriptions=true.</td>"
    ) in rendered


def test_section_snippet_excludes_next_section_comment_banner():
    snippet = builder._section_snippet(
        """
# core -- runtime behaviour
core:
  host: "0.0.0.0"

# telemetry -- telemetry
telemetry:
  enabled: false
""".strip(),
        "core",
    )

    assert snippet == 'core:\n  host: "0.0.0.0"'


def test_indented_fenced_yaml_keeps_blank_lines_inside_collapsible_block():
    block = builder._indented_fenced_yaml('core:\n  host: "0.0.0.0"\n\n  port: 8932')

    assert "    ```yaml" in block
    assert "\n    \n" in block
    assert "    ```" in block


def test_write_if_changed_updates_only_on_real_diff(tmp_path: Path):
    output_path = tmp_path / "config-reference.md"
    content = "# x\n"

    assert rc._write_if_changed(output_path=output_path, content=content) is True
    assert rc._write_if_changed(output_path=output_path, content=content) is False
    assert output_path.read_text(encoding="utf-8") == content


def test_repo_root_and_build_parser_defaults() -> None:
    repo_root = rc._repo_root()
    parser = rc._build_parser()
    args = parser.parse_args([])

    assert (repo_root / "pyproject.toml").exists()
    assert Path(args.template) == repo_root / "config.yaml.template"
    assert Path(args.output) == repo_root / "docs" / "configuration" / "config-reference.md"


def test_main_prints_updated_and_up_to_date_status(tmp_path: Path, monkeypatch, capsys) -> None:
    template_path = tmp_path / "config.yaml.template"
    output_path = tmp_path / "config-reference.md"
    template_path.write_text("core:\n  host: 0.0.0.0\n", encoding="utf-8")

    parser = SimpleNamespace(parse_args=lambda: SimpleNamespace(template=str(template_path), output=str(output_path)))
    monkeypatch.setattr(rc, "_build_parser", lambda: parser)
    monkeypatch.setattr(rc, "_build_output", lambda template_text: f"rendered:{template_text}")

    assert rc.main() == 0
    assert output_path.read_text(encoding="utf-8") == "rendered:core:\n  host: 0.0.0.0\n"
    assert "updated" in capsys.readouterr().out

    assert rc.main() == 0
    assert "up-to-date" in capsys.readouterr().out
