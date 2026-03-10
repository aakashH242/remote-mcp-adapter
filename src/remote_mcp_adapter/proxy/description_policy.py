"""Helpers for shaping client-facing tool descriptions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

_DEFAULT_SHORT_DESCRIPTION_MAX_TOKENS = 16
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_VERB_HINTS = {
    "analyze",
    "browse",
    "capture",
    "check",
    "click",
    "convert",
    "create",
    "delete",
    "download",
    "execute",
    "extract",
    "fetch",
    "fill",
    "generate",
    "get",
    "inspect",
    "list",
    "load",
    "navigate",
    "open",
    "parse",
    "press",
    "read",
    "render",
    "save",
    "screenshot",
    "search",
    "select",
    "send",
    "take",
    "type",
    "update",
    "upload",
    "write",
}
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "then",
    "this",
    "to",
    "tool",
    "use",
    "with",
    "your",
}


@dataclass(frozen=True, slots=True)
class DescriptionPolicy:
    """Resolved description-shaping policy for one server."""

    shorten: bool
    max_tokens: int


def resolve_description_policy(*, config=None, server=None) -> DescriptionPolicy:
    """Resolve effective description-shaping settings for one server.

    Args:
        config: Adapter config or compatible test double.
        server: Server config or compatible test double.

    Returns:
        Resolved description policy with server override precedence.
    """
    core = getattr(config, "core", None)
    shorten = bool(getattr(core, "shorten_descriptions", False))
    server_override = getattr(server, "shorten_descriptions", None)
    if server_override is not None:
        shorten = bool(server_override)

    max_tokens = int(getattr(core, "short_description_max_tokens", _DEFAULT_SHORT_DESCRIPTION_MAX_TOKENS))
    server_max_tokens = getattr(server, "short_description_max_tokens", None)
    if server_max_tokens is not None:
        max_tokens = int(server_max_tokens)

    return DescriptionPolicy(shorten=shorten, max_tokens=max_tokens)


def build_upload_consumer_description(
    *,
    upstream_description: Any,
    adapter_note: str,
    config=None,
    server=None,
) -> str:
    """Build the visible description for an upload_consumer override.

    Args:
        upstream_description: Original upstream description text.
        adapter_note: Adapter-owned workflow guidance.
        config: Adapter config or compatible test double.
        server: Server config or compatible test double.

    Returns:
        Full or shortened client-facing description depending on policy.
    """
    normalized_upstream = _normalize_description(upstream_description)
    if not normalized_upstream:
        return adapter_note

    policy = resolve_description_policy(config=config, server=server)
    if not policy.shorten:
        return _join_blocks(normalized_upstream, adapter_note)

    summary = _summarize_first_sentence(normalized_upstream, max_tokens=policy.max_tokens)
    details = _extract_semantic_details(normalized_upstream)

    semantic_blocks: list[str] = []
    if summary:
        semantic_blocks.append(f"Purpose: {_ensure_sentence(summary)}")
    if details:
        semantic_blocks.append(details)
    semantic_blocks.append(adapter_note)
    return _join_blocks(*semantic_blocks)


def _normalize_description(value: Any) -> str:
    """Return a single-line normalized description string.

    Args:
        value: Raw description value.

    Returns:
        Normalized non-empty string, or an empty string when unavailable.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _join_blocks(*parts: str) -> str:
    """Join non-empty text blocks with blank lines.

    Args:
        *parts: Candidate text blocks.

    Returns:
        Joined description string.
    """
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return "\n\n".join(cleaned)


def _summarize_first_sentence(text: str, *, max_tokens: int) -> str:
    """Take the first sentence and trim it to the configured token budget.

    Args:
        text: Full upstream description.
        max_tokens: Maximum number of tokens to keep.

    Returns:
        Shortened first sentence.
    """
    first_sentence = _SENTENCE_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    matches = list(_WORD_RE.finditer(first_sentence))
    if len(matches) <= max_tokens:
        return first_sentence
    end_index = matches[max_tokens - 1].end()
    trimmed = first_sentence[:end_index].rstrip(" ,;:-")
    return f"{trimmed}..."


def _extract_semantic_details(text: str) -> str:
    """Extract lightweight action/object hints from upstream text.

    Args:
        text: Full upstream description.

    Returns:
        Compact semantic hint string, or empty string when no terms are found.
    """
    tokens = [match.group(0).lower() for match in _WORD_RE.finditer(text)]

    verbs: list[str] = []
    nouns: list[str] = []
    seen_verbs: set[str] = set()
    seen_nouns: set[str] = set()

    for token in tokens:
        if len(verbs) >= 2 and len(nouns) >= 2:
            break
        if token in _STOPWORDS:
            continue
        if token in _VERB_HINTS and token not in seen_verbs and len(verbs) < 2:
            verbs.append(token)
            seen_verbs.add(token)
            continue
        if token not in seen_nouns and token not in seen_verbs and len(nouns) < 2:
            nouns.append(token)
            seen_nouns.add(token)

    parts: list[str] = []
    if verbs:
        parts.append(f"Key actions: {', '.join(verbs)}.")
    if nouns:
        parts.append(f"Key objects: {', '.join(nouns)}.")
    return " ".join(parts)


def _ensure_sentence(text: str) -> str:
    """Ensure text ends with sentence punctuation.

    Args:
        text: Candidate sentence text.

    Returns:
        Text with terminal punctuation.
    """
    stripped = text.strip()
    if not stripped:
        return stripped
    if stripped.endswith((".", "!", "?")):
        return stripped
    return f"{stripped}."
