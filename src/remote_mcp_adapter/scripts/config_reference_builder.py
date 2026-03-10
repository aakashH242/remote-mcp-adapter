"""Build a structured config reference from the commented YAML template."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import re

_SECTION_SEPARATOR_CHARS = {"=", "-"}
_COMMENT_RE = re.compile(r"^\s*#\s?(?P<text>.*)$")
_DASH_COMMENT_RE = re.compile(r"^(?P<indent>\s*)-\s*(?:#\s*(?P<comment>.*))?$")
_KEY_RE = re.compile(r"^(?P<indent>\s*)(?P<dash>-\s*)?(?P<key>[A-Za-z0-9_][A-Za-z0-9_-]*):(?:\s*(?P<value>.*))?$")
_DEFAULT_RE = re.compile(r"Default:\s*(?P<value>.+?)(?:(?:\.\s+Allowed:)|$)")
_ALLOWED_RE = re.compile(r"Allowed:\s*(?P<value>.+?)(?:\.\s*$|$)")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_SECTION_TITLE_RE = re.compile(r"^[A-Za-z0-9_]+\s+--\s+(?P<summary>.+)$")
_EXAMPLE_KEY_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*:\s*")
_TOP_LEVEL_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_][A-Za-z0-9_-]*):(?:\s*(?P<value>.*))?$")
_INLINE_PATH_RE = re.compile(r"(?P<path>[A-Za-z0-9_.\[\]]+)")
_OVERRIDE_TARGET_RE = re.compile(r"(?P<path>[A-Za-z0-9_.\[\]]+)")


@dataclass(frozen=True)
class ParsedField:
    """Single config field extracted from the commented YAML template.

    Attributes:
        path: Full dotted field path.
        value: Literal YAML value string when the field is scalar.
        comments: Normalized comment lines associated with the field.
    """

    path: str
    value: str | None
    comments: tuple[str, ...]


@dataclass(frozen=True)
class ReferenceRow:
    """Single rendered row inside a section table.

    Attributes:
        field_path: Path relative to the section root.
        type_name: Human-readable type summary.
        required: Requiredness summary.
        default: Default value summary.
        allowed: Allowed-values summary.
        common_overrides: Common override or inheritance hints.
        summary: One-line human summary.
    """

    field_path: str
    type_name: str
    required: str
    default: str
    allowed: str
    common_overrides: str
    summary: str


@dataclass(frozen=True)
class ReferenceSection:
    """Top-level config section for the generated markdown page.

    Attributes:
        name: Top-level section key.
        summary: Short summary shown in the overview table.
        description: Introductory paragraph shown before the section table.
        snippet: Example YAML snippet for the section.
        rows: Table rows for all descendant fields.
    """

    name: str
    summary: str
    description: str
    snippet: str
    rows: tuple[ReferenceRow, ...]


def parse_template_fields(template_text: str) -> tuple[ParsedField, ...]:
    """Parse commented YAML template fields into structured entries.

    Args:
        template_text: Full `config.yaml.template` text.

    Returns:
        Parsed field entries in template order.
    """

    pending_comments: list[str] = []
    stack: list[tuple[int, str]] = []
    parsed_fields: list[ParsedField] = []

    for raw_line in template_text.splitlines():
        stripped_line = raw_line.strip()
        if not stripped_line:
            _append_comment_blank_line(pending_comments)
            continue

        comment_match = _COMMENT_RE.match(raw_line)
        if comment_match is not None:
            _append_comment_line(pending_comments, comment_match.group("text"))
            continue

        dash_match = _DASH_COMMENT_RE.match(raw_line)
        if dash_match is not None and _KEY_RE.match(raw_line) is None:
            indent = len(dash_match.group("indent"))
            stack = _trim_stack_for_indent(stack, indent)
            stack = _ensure_list_context(stack)
            if dash_match.group("comment") is not None:
                _append_comment_line(pending_comments, dash_match.group("comment"))
            continue

        key_match = _KEY_RE.match(raw_line)
        if key_match is None:
            pending_comments.clear()
            continue

        indent = len(key_match.group("indent"))
        stack = _trim_stack_for_indent(stack, indent)
        if key_match.group("dash") is not None:
            stack = _ensure_list_context(stack)

        key = key_match.group("key")
        raw_value = key_match.group("value")
        value = None if raw_value is None or not raw_value.strip() else raw_value.strip()
        path = _build_path(stack, key)
        parsed_fields.append(
            ParsedField(
                path=path,
                value=value,
                comments=tuple(_normalize_comment_lines(pending_comments)),
            )
        )
        pending_comments.clear()

        if value is None:
            stack.append((indent, key))

    return tuple(parsed_fields)


def build_reference_sections(template_text: str) -> tuple[ReferenceSection, ...]:
    """Build structured section data for the config reference page.

    Args:
        template_text: Full `config.yaml.template` text.

    Returns:
        Top-level config sections in template order.
    """

    parsed_fields = parse_template_fields(template_text)
    all_paths = {field.path for field in parsed_fields}
    top_level_fields = [field for field in parsed_fields if "." not in field.path and "[]" not in field.path]
    sections: list[ReferenceSection] = []

    for section_field in top_level_fields:
        section_comments = tuple(_top_level_section_comments(section_field.path, section_field.comments))
        descendant_fields = [
            field
            for field in parsed_fields
            if field.path != section_field.path
            and (field.path.startswith(f"{section_field.path}.") or field.path.startswith(f"{section_field.path}[]"))
        ]
        rows = tuple(_build_reference_row(field, section_field.path, all_paths) for field in descendant_fields)
        sections.append(
            ReferenceSection(
                name=section_field.path,
                summary=_field_summary(section_comments, fallback=section_field.path),
                description=_section_description(section_comments, fallback=section_field.path),
                snippet=_section_snippet(template_text, section_field.path),
                rows=rows,
            )
        )

    return tuple(sections)


def build_reference_markdown(template_text: str) -> str:
    """Render the full config reference markdown document.

    Args:
        template_text: Full `config.yaml.template` text.

    Returns:
        Generated markdown document with overview table, per-section tables,
        and a full-template appendix.
    """

    sections = build_reference_sections(template_text)
    lines: list[str] = [
        "# Detailed Reference",
        "",
        "> This page is generated from `config.yaml.template`. Do not edit it by hand.",
        "",
        (
            "**What you'll learn here:** the full supported config surface, rendered "
            "as a structured reference from the commented YAML template."
        ),
        "",
        (
            "Use [Configuration](../configuration.md) for a guided overview and the "
            "scenario pages for opinionated production profiles."
        ),
        "",
        "---",
        "",
        "## How to read this page",
        "",
        "- The tables below are the generated reference view.",
        "- The full commented YAML template is still included at the end as an appendix.",
        "- Change `config.yaml.template`, then regenerate this page.",
        "",
        "---",
        "",
        "## Sections",
        "",
        "| Section | Summary |",
        "| --- | --- |",
    ]
    for section in sections:
        lines.append(f"| [`{section.name}`](#{_section_anchor(section.name)}) | {section.summary} |")

    for section in sections:
        lines.extend(
            [
                "",
                "---",
                "",
                f"## `{section.name}`",
                "",
                section.description,
                "",
                _render_reference_table(section.rows),
            ]
        )
        lines.extend(
            [
                "",
                '??? example "Show example snippet"',
                "",
                _indented_fenced_yaml(section.snippet),
            ]
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## Full template appendix",
            "",
            (
                "The exact commented YAML template is still included here for "
                "copy-paste use and for comments that are too detailed for a table cell."
            ),
            "",
            '??? example "Show full commented template"',
            "",
            _indented_fenced_yaml(template_text.rstrip()),
            "",
            "---",
            "",
            "## Next steps",
            "",
            "- **Previous topic:** [Configuration](../configuration.md) - practical guide to the main config sections.",
            "- **Next:** [Local Dev Scenario](local-dev.md) - start with the easiest opinionated profile.",
        ]
    )
    return "\n".join(lines) + "\n"


def _append_comment_blank_line(pending_comments: list[str]) -> None:
    """Add a single logical blank line to the pending comment block.

    Args:
        pending_comments: Mutable pending comment list.
    """

    if pending_comments and pending_comments[-1] != "":
        pending_comments.append("")


def _append_comment_line(pending_comments: list[str], raw_comment: str) -> None:
    """Append one normalized comment line to the pending comment block.

    Args:
        pending_comments: Mutable pending comment list.
        raw_comment: Comment text without the leading `#`.
    """

    normalized = raw_comment.strip()
    if not normalized:
        _append_comment_blank_line(pending_comments)
        return
    if set(normalized) <= _SECTION_SEPARATOR_CHARS:
        return
    pending_comments.append(normalized)


def _normalize_comment_lines(comment_lines: list[str]) -> list[str]:
    """Collapse repeated blank comment lines and trim edges.

    Args:
        comment_lines: Raw comment lines for one field.

    Returns:
        Normalized comment block.
    """

    normalized: list[str] = []
    for line in comment_lines:
        if not line:
            _append_comment_blank_line(normalized)
            continue
        normalized.append(line)
    while normalized and normalized[0] == "":
        normalized.pop(0)
    while normalized and normalized[-1] == "":
        normalized.pop()
    return normalized


def _trim_stack_for_indent(stack: list[tuple[int, str]], indent: int) -> list[tuple[int, str]]:
    """Trim path stack to the parent container for the current indent.

    Args:
        stack: Current path stack.
        indent: Indent of the current YAML line.

    Returns:
        Trimmed stack.
    """

    trimmed = list(stack)
    while trimmed and trimmed[-1][0] >= indent:
        trimmed.pop()
    return trimmed


def _ensure_list_context(stack: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Ensure the path stack ends with a list-item marker.

    Args:
        stack: Current path stack.

    Returns:
        Stack with a `[]` marker at the end.
    """

    if stack and stack[-1][1] == "[]":
        return list(stack)
    updated = list(stack)
    next_indent = updated[-1][0] + 1 if updated else 0
    updated.append((next_indent, "[]"))
    return updated


def _build_path(stack: list[tuple[int, str]], key: str) -> str:
    """Build a dotted config path from the current stack and key.

    Args:
        stack: Current path stack.
        key: Field key at the current line.

    Returns:
        Dotted config path such as `servers[].upstream.url`.
    """

    segments = [segment for _, segment in stack] + [key]
    path_parts: list[str] = []
    for segment in segments:
        if segment == "[]":
            if path_parts:
                path_parts[-1] = f"{path_parts[-1]}[]"
            else:
                path_parts.append("[]")
            continue
        path_parts.append(segment)
    return ".".join(path_parts)


def _build_reference_row(field: ParsedField, section_name: str, all_paths: set[str]) -> ReferenceRow:
    """Convert one parsed field into a rendered table row.

    Args:
        field: Parsed template field.
        section_name: Top-level section name.
        all_paths: Set of every parsed path for type inference.

    Returns:
        Structured table row.
    """

    return ReferenceRow(
        field_path=_relative_field_path(field.path, section_name),
        type_name=_infer_type_name(field.path, field.value, all_paths),
        required=_field_required(field.comments),
        default=_field_default(field.comments),
        allowed=_field_allowed(field.comments),
        common_overrides=_field_common_overrides(field.path, field.comments),
        summary=_field_summary(field.comments, fallback=field.path),
    )


def _relative_field_path(path: str, section_name: str) -> str:
    """Strip the top-level section prefix from a full field path.

    Args:
        path: Full field path.
        section_name: Top-level section name.

    Returns:
        Relative field path for display within a section table.
    """

    if path == section_name:
        return path
    if path.startswith(f"{section_name}."):
        return path[len(section_name) + 1 :]
    if path.startswith(f"{section_name}[]"):
        return path[len(section_name) :]
    return path


def _infer_type_name(path: str, value: str | None, all_paths: set[str]) -> str:
    """Infer a human-readable type label from the template value and children.

    Args:
        path: Full field path.
        value: Literal YAML value string.
        all_paths: Set of every parsed path.

    Returns:
        Human-readable type label.
    """

    if value is None:
        if any(other.startswith(f"{path}[].") or other == f"{path}[]" for other in all_paths if other != path):
            return "array"
        if any(other.startswith(f"{path}.") for other in all_paths if other != path):
            return "object"
        return "object"
    if value in {"true", "false"}:
        return "boolean"
    if value == "null":
        return "null"
    if value.startswith("[") and value.endswith("]"):
        return "array"
    if value.startswith("{") and value.endswith("}"):
        return "object"
    if _looks_like_number(value):
        return "number"
    return "string"


def _looks_like_number(value: str) -> bool:
    """Check whether a scalar template value looks numeric.

    Args:
        value: Scalar YAML value.

    Returns:
        True when the value looks like an int or float.
    """

    try:
        float(value)
    except ValueError:
        return False
    return True


def _field_required(comment_lines: tuple[str, ...]) -> str:
    """Extract requiredness metadata from a comment block.

    Args:
        comment_lines: Normalized comment lines for one field.

    Returns:
        Requiredness summary.
    """

    for line in comment_lines:
        lowered = line.lower()
        if lowered == "required.":
            return "yes"
        if "required when" in lowered or lowered.startswith("optional (required"):
            return "conditional"
        if lowered.startswith("optional."):
            return "optional"
    return "-"


def _field_default(comment_lines: tuple[str, ...]) -> str:
    """Extract default-value metadata from a comment block.

    Args:
        comment_lines: Normalized comment lines for one field.

    Returns:
        Default-value summary.
    """

    for line in comment_lines:
        match = _DEFAULT_RE.search(line)
        if match is not None:
            return match.group("value").strip().rstrip(".")
    return "-"


def _field_allowed(comment_lines: tuple[str, ...]) -> str:
    """Extract allowed-values metadata from a comment block.

    Args:
        comment_lines: Normalized comment lines for one field.

    Returns:
        Allowed-values summary.
    """

    for line in comment_lines:
        match = _ALLOWED_RE.search(line)
        if match is not None:
            return match.group("value").strip().rstrip(".")
    return "-"


def _field_common_overrides(path: str, comment_lines: tuple[str, ...]) -> str:
    """Extract common override or inheritance hints from a comment block.

    Args:
        path: Full field path.
        comment_lines: Normalized comment lines for one field.

    Returns:
        Short override/inheritance summary for table display.
    """

    joined = " ".join(comment_lines)
    extracted_targets: list[str] = []

    extracted_targets.extend(_extract_override_targets(joined))
    extracted_targets.extend(_extract_inheritance_targets(joined))

    extracted_targets.extend(_path_specific_common_overrides(path))
    deduped = _dedupe_preserving_order(extracted_targets)
    if not deduped:
        return "-"
    return "; ".join(deduped)


def _extract_override_targets(joined_comments: str) -> list[str]:
    """Extract explicit override target paths from a joined comment block.

    Args:
        joined_comments: Comment block collapsed into one string.

    Returns:
        Markdown-formatted override targets.
    """

    targets: list[str] = []
    capture_prefixes = (
        "Individual servers can override this with ",
        "Individual servers can override it with ",
        "Per-server overrides are under ",
        "Can be overridden per adapter via ",
        "Overrides ",
    )
    for prefix in capture_prefixes:
        targets.extend(_extract_targets_after_prefix(joined_comments, prefix))
    return targets


def _extract_targets_after_prefix(joined_comments: str, prefix: str) -> list[str]:
    """Extract one or more config paths that appear immediately after a phrase.

    Args:
        joined_comments: Comment block collapsed into one string.
        prefix: Phrase that appears before the config path.

    Returns:
        Markdown-formatted path targets.
    """

    targets: list[str] = []
    search_start = 0
    while True:
        prefix_index = joined_comments.find(prefix, search_start)
        if prefix_index < 0:
            return targets
        candidate_start = prefix_index + len(prefix)
        candidate = joined_comments[candidate_start:]
        match = _OVERRIDE_TARGET_RE.search(candidate)
        if match is not None:
            targets.append(f"`{match.group('path').rstrip('.')}`")
            search_start = candidate_start + match.end()
            continue
        return targets


def _extract_inheritance_targets(joined_comments: str) -> list[str]:
    """Extract inheritance hints from a joined comment block.

    Args:
        joined_comments: Comment block collapsed into one string.

    Returns:
        Markdown-formatted inheritance hints.
    """

    targets: list[str] = []
    inheritance_matcher = re.compile(r"null inherits(?: from)? (?P<path>[A-Za-z0-9_.\[\]]+)")
    for match in inheritance_matcher.finditer(joined_comments):
        targets.append(f"inherits `{match.group('path').rstrip('.')}`")
    return targets


def _field_summary(comment_lines: tuple[str, ...], *, fallback: str) -> str:
    """Build a one-line summary from a field's comment block.

    Args:
        comment_lines: Normalized comment lines for one field.
        fallback: Fallback text when no description could be derived.

    Returns:
        One-line summary.
    """

    first_line = next((line for line in comment_lines if line), "")
    title_match = _SECTION_TITLE_RE.match(first_line)
    if title_match is not None:
        return title_match.group("summary").strip()
    description = _field_description(comment_lines)
    if not description:
        return fallback
    first_sentence = _SENTENCE_RE.split(description, maxsplit=1)[0]
    return first_sentence.strip()


def _field_description(comment_lines: tuple[str, ...]) -> str:
    """Build a short prose description from a comment block.

    Args:
        comment_lines: Normalized comment lines for one field.

    Returns:
        Human-readable description without metadata-only lines.
    """

    lines = list(comment_lines)
    if len(lines) >= 2 and lines[1] == "" and _looks_like_heading(lines[0]):
        stripped = lines[2:]
        if any(line for line in stripped):
            lines = stripped
    paragraphs = _paragraphs(lines)
    candidate_paragraphs = [
        paragraph for paragraph in paragraphs if any(line for line in paragraph if not _is_metadata_line(line))
    ]
    for index, paragraph in enumerate(candidate_paragraphs):
        cleaned = [line for line in paragraph if not _is_metadata_line(line)]
        if _is_heading_paragraph(cleaned) and any(
            not _is_heading_paragraph([line for line in later if not _is_metadata_line(line)])
            for later in candidate_paragraphs[index + 1 :]
        ):
            continue
        if _is_example_paragraph(cleaned) and any(
            not _is_example_paragraph([line for line in later if not _is_metadata_line(line)])
            for later in candidate_paragraphs[index + 1 :]
        ):
            continue
        return " ".join(cleaned).strip()
    return ""


def _looks_like_heading(line: str) -> bool:
    """Check whether one comment line is probably a section heading.

    Args:
        line: Comment line content.

    Returns:
        True when the line looks like a heading rather than prose.
    """

    word_count = len(line.split())
    return word_count <= 6 and not any(character in line for character in ":.,;")


def _is_metadata_line(line: str) -> bool:
    """Check whether a comment line is metadata rather than prose.

    Args:
        line: Comment line content.

    Returns:
        True when the line is mostly metadata.
    """

    lowered = line.lower()
    return (
        lowered.startswith("optional.")
        or lowered.startswith("optional (")
        or lowered.startswith("required.")
        or lowered.startswith("required (")
        or lowered.startswith("allowed:")
    )


def _top_level_section_comments(section_name: str, comment_lines: tuple[str, ...]) -> list[str]:
    """Keep only the final section-specific comment block for a top-level key.

    Args:
        section_name: Top-level section name.
        comment_lines: Raw normalized comments attached to a top-level field.

    Returns:
        Section-specific trailing comment block.
    """

    lines = list(comment_lines)
    section_marker = f"{section_name} -- "
    for index, line in enumerate(lines):
        if line.startswith(section_marker):
            return lines[index:]
    if "" not in lines:
        return lines
    last_blank_index = max(index for index, line in enumerate(lines) if line == "")
    tail = lines[last_blank_index + 1 :]
    return tail or lines


def _paragraphs(lines: list[str]) -> list[list[str]]:
    """Split normalized comment lines into paragraphs.

    Args:
        lines: Normalized comment lines.

    Returns:
        Paragraphs separated by blank lines.
    """

    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if not line:
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(current)
    return paragraphs


def _is_example_paragraph(paragraph: list[str]) -> bool:
    """Check whether a paragraph looks like an inline YAML example block.

    Args:
        paragraph: Paragraph lines with metadata already removed.

    Returns:
        True when the paragraph appears to be an example rather than prose.
    """

    if not paragraph:
        return False
    first_line = paragraph[0]
    return bool(_EXAMPLE_KEY_RE.match(first_line) or first_line.startswith("- "))


def _is_heading_paragraph(paragraph: list[str]) -> bool:
    """Check whether a paragraph is just a short heading label.

    Args:
        paragraph: Paragraph lines with metadata already removed.

    Returns:
        True when the paragraph looks like a heading.
    """

    return len(paragraph) == 1 and _looks_like_heading(paragraph[0])


def _section_description(comment_lines: tuple[str, ...], *, fallback: str) -> str:
    """Build a cleaner intro paragraph for a top-level section.

    Args:
        comment_lines: Section comment block.
        fallback: Fallback text when no description could be derived.

    Returns:
        Human-readable section intro.
    """

    if not comment_lines:
        return fallback
    lines = list(comment_lines)
    first_line = next((line for line in lines if line), "")
    title_match = _SECTION_TITLE_RE.match(first_line)
    if title_match is None:
        return _field_description(comment_lines) or fallback

    remaining_lines = lines[1:]
    remaining_description = _field_description(tuple(remaining_lines))
    summary = title_match.group("summary").strip()
    if not remaining_description:
        return summary
    return f"{summary}. {remaining_description}"


def _section_snippet(template_text: str, section_name: str) -> str:
    """Extract the YAML snippet for one top-level section.

    Args:
        template_text: Full `config.yaml.template` text.
        section_name: Top-level section name.

    Returns:
        YAML snippet for that top-level section.
    """

    lines = template_text.splitlines()
    start_index: int | None = None
    end_index = len(lines)
    for index, raw_line in enumerate(lines):
        key_match = _TOP_LEVEL_KEY_RE.match(raw_line)
        if key_match is None:
            continue
        key = key_match.group("key")
        if key == section_name and start_index is None:
            start_index = index
            continue
        if start_index is not None:
            end_index = index
            break
    if start_index is None:
        return f"{section_name}:"
    snippet_lines = _trim_trailing_section_comments(lines[start_index:end_index])
    return "\n".join(snippet_lines).rstrip()


def _trim_trailing_section_comments(lines: list[str]) -> list[str]:
    """Drop comment banners that belong to the next top-level section.

    Args:
        lines: Candidate snippet lines for one section.

    Returns:
        Snippet lines without trailing comment-only section banners.
    """

    trimmed = list(lines)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()

    while trimmed:
        if _COMMENT_RE.match(trimmed[-1]) is not None:
            trimmed.pop()
            continue
        break

    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return trimmed


def _indented_fenced_yaml(yaml_text: str) -> str:
    """Render a fenced YAML block indented for a collapsible admonition block.

    Args:
        yaml_text: Raw YAML snippet text.

    Returns:
        Indented fenced YAML block.
    """

    indented_lines = ["    ```yaml"]
    indented_lines.extend(f"    {line}" if line else "    " for line in yaml_text.splitlines())
    indented_lines.append("    ```")
    return "\n".join(indented_lines)


def _render_reference_table(rows: tuple[ReferenceRow, ...]) -> str:
    """Render one section table as explicit HTML.

    Args:
        rows: Reference rows for one section.

    Returns:
        HTML table markup that renders consistently after docs processing.
    """

    lines = [
        "<table>",
        "  <thead>",
        "    <tr>",
        "      <th>Field</th>",
        "      <th>Type</th>",
        "      <th>Required</th>",
        "      <th>Default</th>",
        "      <th>Allowed</th>",
        "      <th>Common Overrides</th>",
        "      <th>Summary</th>",
        "    </tr>",
        "  </thead>",
        "  <tbody>",
    ]
    for row in rows:
        lines.extend(_render_reference_table_row(row))
    lines.extend(["  </tbody>", "</table>"])
    return "\n".join(lines)


def _render_reference_table_row(row: ReferenceRow) -> list[str]:
    """Render one HTML reference row.

    Args:
        row: Reference row content.

    Returns:
        HTML lines for a single row.
    """

    return [
        "    <tr>",
        f"      <td><code>{escape(row.field_path)}</code></td>",
        f"      <td><code>{escape(row.type_name)}</code></td>",
        f"      <td>{_html_table_cell(row.required)}</td>",
        f"      <td>{_html_table_cell(row.default)}</td>",
        f"      <td>{_html_table_cell(row.allowed)}</td>",
        f"      <td>{_html_table_cell(row.common_overrides)}</td>",
        f"      <td>{_html_table_cell(row.summary)}</td>",
        "    </tr>",
    ]


def _html_table_cell(value: str) -> str:
    """Escape one HTML table cell while preserving inline code spans.

    Args:
        value: Raw cell value.

    Returns:
        Safe HTML string for the table cell.
    """

    normalized = value.strip() or "-"
    escaped = escape(normalized)
    return re.sub(r"`([^`]+)`", lambda match: f"<code>{escape(match.group(1))}</code>", escaped)


def _extract_first_path(text: str) -> str | None:
    """Extract the first config-like path from free-form comment text.

    Args:
        text: Free-form comment text.

    Returns:
        First path-like token or None.
    """

    match = _INLINE_PATH_RE.search(text.rstrip("."))
    if match is None:
        return None
    return match.group("path").rstrip(".")


def _path_specific_common_overrides(path: str) -> tuple[str, ...]:
    """Return hand-tuned common override hints for important fields.

    Args:
        path: Full field path.

    Returns:
        Additional override hints.
    """

    mapping: dict[str, tuple[str, ...]] = {
        "core.defaults.tool_call_timeout_seconds": (
            "`servers[].tool_defaults.tool_call_timeout_seconds`",
            "`servers[].adapters[].overrides.tool_call_timeout_seconds`",
        ),
        "core.defaults.allow_raw_output": (
            "`servers[].tool_defaults.allow_raw_output`",
            "`servers[].adapters[].overrides.allow_raw_output`",
            "`servers[].adapters[].allow_raw_output`",
        ),
        "artifacts.expose_as_resources": ("`servers[].adapters[].expose_as_resource`",),
    }
    return mapping.get(path, ())


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    """Remove duplicates from a list while preserving order.

    Args:
        items: Raw string items.

    Returns:
        Deduplicated items in original order.
    """

    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _markdown_cell(value: str) -> str:
    """Escape markdown table cell content.

    Args:
        value: Raw cell content.

    Returns:
        Escaped single-line cell content.
    """

    return value.replace("|", "\\|").replace("\n", " ").strip() or "-"


def _section_anchor(section_name: str) -> str:
    """Build the markdown anchor used by the section overview table.

    Args:
        section_name: Section heading text.

    Returns:
        Section anchor slug.
    """

    return section_name
