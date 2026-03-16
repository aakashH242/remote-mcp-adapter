"""Typed storage-layer errors for session and artifact flows."""

from __future__ import annotations

from fastmcp.exceptions import ToolError


class TerminalSessionInvalidatedError(ToolError):
    """Raised when a terminally invalidated adapter session is reused."""


class SessionTrustContextMismatchError(ToolError):
    """Raised when a request does not match the trust context bound to a session."""
