"""Logging redaction helpers to prevent sensitive token/header leakage."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
import re
from typing import Any

from .constants import (
    REDACTED_LOG_VALUE,
    SENSITIVE_LOG_KEY_FRAGMENTS,
    SENSITIVE_LOG_KEY_NAMES,
)

_LOG_RECORD_BASE_FIELDS = frozenset(logging.makeLogRecord({}).__dict__.keys())
_BEARER_TOKEN_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*")
_BASIC_TOKEN_PATTERN = re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/]+=*")
_JWT_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")
_DEFAULT_SENSITIVE_ALTERNATION = "|".join(
    sorted((re.escape(value) for value in SENSITIVE_LOG_KEY_NAMES), key=len, reverse=True)
)
_DEFAULT_KEY_VALUE_PATTERN = re.compile(
    rf"(?i)(?P<key>{_DEFAULT_SENSITIVE_ALTERNATION})\s*(?P<sep>[:=])\s*(?P<value>\"[^\"]*\"|'[^']*'|[^,\s;&]+)"
)
_ACTIVE_REDACTION_FILTER: "SensitiveLogFilter | None" = None


def _normalize_key(value: str) -> str:
    """Normalize a dict/header key for case-insensitive matching.

    Args:
        value: Raw key string.

    Returns:
        Lower-cased key with trimmed whitespace and hyphen/underscore normalization.
    """
    return value.strip().lower().replace("-", "_")


def _build_key_value_pattern(sensitive_key_names: set[str]) -> re.Pattern[str] | None:
    """Build a key/value redaction regex for configured header/key names.

    Args:
        sensitive_key_names: Normalized keys that should have values redacted.

    Returns:
        Compiled regex or ``None`` when there are no configured names.
    """
    if not sensitive_key_names:
        return None
    normalized_alternatives = []
    for value in sensitive_key_names:
        escaped = re.escape(value)
        escaped = escaped.replace(r"\_", "[-_]")
        normalized_alternatives.append(escaped)
    alternatives = "|".join(sorted(normalized_alternatives, key=len, reverse=True))
    return re.compile(rf"(?i)(?P<key>{alternatives})\s*(?P<sep>[:=])\s*(?P<value>\"[^\"]*\"|'[^']*'|[^,\s;&]+)")


def _replace_key_value(match: re.Match[str]) -> str:
    """Return one key/value token with value replaced by the redaction marker.

    Args:
        match: Regex key/value match.

    Returns:
        Redacted ``key<sep><value>`` string.
    """
    key = match.group("key")
    separator = match.group("sep")
    return f"{key}{separator}{REDACTED_LOG_VALUE}"


class SensitiveLogFilter(logging.Filter):
    """Sanitize log messages and structured extras for sensitive data."""

    def __init__(self, *, sensitive_key_names: Iterable[str] = ()) -> None:
        """Initialize the log filter with configured sensitive key names.

        Args:
            sensitive_key_names: Header/key names that should always have values redacted.
        """
        super().__init__()
        self._sensitive_key_names: set[str] = set()
        self._configured_key_value_pattern: re.Pattern[str] | None = None
        self.update_sensitive_key_names(sensitive_key_names)

    def update_sensitive_key_names(self, sensitive_key_names: Iterable[str]) -> None:
        """Refresh configured sensitive key names used for log redaction.

        Args:
            sensitive_key_names: Header/key names to redact.
        """
        normalized = {_normalize_key(value) for value in sensitive_key_names if value and value.strip()}
        normalized.update(_normalize_key(value) for value in SENSITIVE_LOG_KEY_NAMES)
        self._sensitive_key_names = normalized
        self._configured_key_value_pattern = _build_key_value_pattern(normalized)

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact sensitive data on one log record in-place.

        Args:
            record: Record emitted by ``logging``.

        Returns:
            Always ``True`` so the record keeps flowing to handlers.
        """
        record.msg = self._sanitize_value(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._sanitize_value(value) for value in record.args)
        elif isinstance(record.args, Mapping):
            record.args = {key: self._sanitize_value(value, key_hint=str(key)) for key, value in record.args.items()}
        elif record.args:
            record.args = self._sanitize_value(record.args)

        for key, value in list(record.__dict__.items()):
            if key in _LOG_RECORD_BASE_FIELDS:
                continue
            record.__dict__[key] = self._sanitize_value(value, key_hint=key)
        return True

    def _sanitize_value(self, value: Any, *, key_hint: str | None = None, seen: set[int] | None = None) -> Any:
        """Recursively sanitize one value for safe logging.

        Args:
            value: Candidate value.
            key_hint: Optional parent key used for sensitive-key detection.
            seen: Object id set used to avoid recursion loops.

        Returns:
            Sanitized value.
        """
        if key_hint is not None and self._is_sensitive_key(key_hint):
            return REDACTED_LOG_VALUE

        if isinstance(value, str):
            return self._sanitize_text(value)
        if isinstance(value, Mapping):
            return self._sanitize_mapping(value, seen=seen)
        if isinstance(value, list):
            return [self._sanitize_value(item, seen=seen) for item in value]
        if isinstance(value, tuple):
            return tuple(self._sanitize_value(item, seen=seen) for item in value)
        if isinstance(value, set):
            return {self._sanitize_value(item, seen=seen) for item in value}
        return value

    def _sanitize_mapping(self, value: Mapping[Any, Any], *, seen: set[int] | None = None) -> dict[Any, Any]:
        """Sanitize a mapping while preserving keys and redacting sensitive values.

        Args:
            value: Mapping to sanitize.
            seen: Object id set used to avoid recursion loops.

        Returns:
            Sanitized dictionary copy.
        """
        active_seen = seen or set()
        obj_id = id(value)
        if obj_id in active_seen:
            return {"recursive": REDACTED_LOG_VALUE}
        active_seen.add(obj_id)
        try:
            sanitized: dict[Any, Any] = {}
            for nested_key, nested_value in value.items():
                key_text = str(nested_key)
                if self._is_sensitive_key(key_text):
                    sanitized[nested_key] = REDACTED_LOG_VALUE
                    continue
                sanitized[nested_key] = self._sanitize_value(
                    nested_value,
                    key_hint=key_text,
                    seen=active_seen,
                )
            return sanitized
        finally:
            active_seen.remove(obj_id)

    def _sanitize_text(self, value: str) -> str:
        """Redact known sensitive patterns from free-form text.

        Args:
            value: Original string.

        Returns:
            Redacted string.
        """
        sanitized = _BEARER_TOKEN_PATTERN.sub("Bearer <redacted>", value)
        sanitized = _BASIC_TOKEN_PATTERN.sub("Basic <redacted>", sanitized)
        sanitized = _DEFAULT_KEY_VALUE_PATTERN.sub(_replace_key_value, sanitized)
        if self._configured_key_value_pattern is not None:
            sanitized = self._configured_key_value_pattern.sub(_replace_key_value, sanitized)
        sanitized = _JWT_TOKEN_PATTERN.sub(REDACTED_LOG_VALUE, sanitized)
        return sanitized

    def _is_sensitive_key(self, key: str) -> bool:
        """Return True when a key should always have its value redacted.

        Args:
            key: Key/header name.

        Returns:
            ``True`` when key indicates potentially sensitive data.
        """
        normalized = _normalize_key(key)
        if normalized in self._sensitive_key_names:
            return True
        return any(fragment in normalized for fragment in SENSITIVE_LOG_KEY_FRAGMENTS)


def collect_sensitive_log_keys(*, config) -> set[str]:
    """Collect configured header/key names whose values must never be logged.

    Args:
        config: Resolved adapter configuration.

    Returns:
        Normalized sensitive key/header names.
    """
    keys: set[str] = set()
    auth_header = (config.core.auth.header_name or "").strip()
    if auth_header:
        keys.add(auth_header)
    keys.update(config.telemetry.headers.keys())
    for server in config.servers:
        keys.update(server.upstream.static_headers.keys())
        keys.update(server.upstream.client_headers.passthrough)
        keys.update(server.upstream.client_headers.required)
    return {_normalize_key(key) for key in keys if key and key.strip()}


def install_log_redaction_filter(*, config) -> SensitiveLogFilter:
    """Install/update the shared redaction filter on active loggers and handlers.

    Args:
        config: Resolved adapter configuration.

    Returns:
        The active shared ``SensitiveLogFilter`` instance.
    """
    global _ACTIVE_REDACTION_FILTER
    sensitive_keys = collect_sensitive_log_keys(config=config)
    if _ACTIVE_REDACTION_FILTER is None:
        _ACTIVE_REDACTION_FILTER = SensitiveLogFilter(sensitive_key_names=sensitive_keys)
    else:
        _ACTIVE_REDACTION_FILTER.update_sensitive_key_names(sensitive_keys)

    _attach_filter_to_known_loggers(_ACTIVE_REDACTION_FILTER)
    return _ACTIVE_REDACTION_FILTER


def _attach_filter_to_known_loggers(redaction_filter: SensitiveLogFilter) -> None:
    """Attach one redaction filter to all currently configured log handlers.

    Args:
        redaction_filter: Shared redaction filter instance.
    """
    logger_names = ("", "uvicorn", "uvicorn.access", "uvicorn.error", "remote_mcp_adapter")
    for logger_name in logger_names:
        _attach_filter(logging.getLogger(logger_name), redaction_filter)

    for logger_obj in logging.root.manager.loggerDict.values():
        if isinstance(logger_obj, logging.Logger):
            _attach_filter(logger_obj, redaction_filter)


def _attach_filter(logger: logging.Logger, redaction_filter: SensitiveLogFilter) -> None:
    """Attach redaction filter to one logger and all its handlers.

    Args:
        logger: Logger object.
        redaction_filter: Shared redaction filter instance.
    """
    if redaction_filter not in logger.filters:
        logger.addFilter(redaction_filter)
    for handler in logger.handlers:
        if redaction_filter not in handler.filters:
            handler.addFilter(redaction_filter)
