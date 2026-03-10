"""Generate the config reference markdown from the commented YAML template."""

from __future__ import annotations

import argparse
from pathlib import Path

from remote_mcp_adapter.scripts.config_reference_builder import build_reference_markdown


def _build_output(template_text: str) -> str:
    """Render markdown content for the config reference page.

    Args:
        template_text: Full `config.yaml.template` file contents.

    Returns:
        Structured markdown reference generated from the template comments.
    """
    return build_reference_markdown(template_text)


def _write_if_changed(*, output_path: Path, content: str) -> bool:
    """Write generated content only when it changed.

    Args:
        output_path: Markdown file to overwrite.
        content: Generated markdown content.

    Returns:
        True when the file was updated, otherwise False.
    """
    existing = output_path.read_text(encoding="utf-8") if output_path.exists() else None
    if existing == content:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return True


def _repo_root() -> Path:
    """Return the repository root path.

    Returns:
        Absolute repository root path.
    """
    return Path(__file__).resolve().parents[3]


def _build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser.

    Returns:
        Configured parser.
    """
    repo_root = _repo_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template",
        default=str(repo_root / "config.yaml.template"),
        help="Path to the commented YAML template.",
    )
    parser.add_argument(
        "--output",
        default=str(repo_root / "docs" / "configuration" / "config-reference.md"),
        help="Path to the generated markdown file.",
    )
    return parser


def main() -> int:
    """Generate the config reference markdown page.

    Returns:
        Zero on success.
    """
    args = _build_parser().parse_args()
    template_path = Path(args.template)
    output_path = Path(args.output)

    template_text = template_path.read_text(encoding="utf-8")
    generated = _build_output(template_text)
    changed = _write_if_changed(output_path=output_path, content=generated)
    status = "updated" if changed else "up-to-date"
    print(f"{output_path} {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
