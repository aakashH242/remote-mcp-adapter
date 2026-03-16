"""Session-integrity model types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SessionTrustBindingKind = Literal["adapter_auth_token"]


@dataclass(slots=True, frozen=True)
class SessionTrustContext:
    """Stable trust-context fingerprint bound to one adapter session.

    Attributes:
        binding_kind: Type of trust binding currently in force.
        fingerprint: Stable non-secret fingerprint for the bound context.
    """

    binding_kind: SessionTrustBindingKind
    fingerprint: str


@dataclass(slots=True, frozen=True)
class SessionTrustCandidate:
    """Request-derived trust context for one stateful adapter request.

    Attributes:
        server_id: Target upstream server identifier.
        session_id: Adapter session identifier.
        trust_context: Trust context derived from the current request.
    """

    server_id: str
    session_id: str
    trust_context: SessionTrustContext
