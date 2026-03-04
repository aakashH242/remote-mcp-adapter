"""Signed credentials for browser-safe artifact download URLs."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping

from ..config import AdapterConfig

_EXP_PARAM = "mcp_artifact_exp"
_SIG_PARAM = "mcp_artifact_sig"


def _signature_payload(
    *,
    server_id: str,
    session_id: str,
    artifact_id: str,
    filename: str,
    expires_at: int,
) -> bytes:
    """Build the canonical signature payload bytes.

    Args:
        server_id: Server identifier.
        session_id: Session identifier.
        artifact_id: Artifact identifier.
        filename: Artifact filename.
        expires_at: Expiry epoch timestamp.
    """
    return f"{server_id}\n{session_id}\n{artifact_id}\n{filename}\n{expires_at}".encode("utf-8")


def _resolve_ttl_seconds(config: AdapterConfig) -> int:
    """Resolve TTL for download credentials using artifact then auth fallback.

    Args:
        config: Full adapter configuration.
    """
    artifact_ttl = config.artifacts.ttl_seconds
    if artifact_ttl is not None and artifact_ttl > 0:
        return artifact_ttl
    return config.core.auth.signed_upload_ttl_seconds


class ArtifactDownloadCredentialManager:
    """Issues and validates signed query credentials for artifact downloads."""

    def __init__(self, *, enabled: bool, secret: str, ttl_seconds: int) -> None:
        """Initialize the download credential manager.

        Args:
            enabled: Whether signed downloads are active.
            secret: HMAC signing secret.
            ttl_seconds: Credential lifetime in seconds.
        """
        self._enabled = enabled
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = max(1, int(ttl_seconds))

    @classmethod
    def from_config(cls, config: AdapterConfig) -> "ArtifactDownloadCredentialManager":
        """Build a manager from the global adapter configuration.

        Args:
            config: Full adapter configuration.
        """
        auth = config.core.auth
        secret = (auth.signing_secret or auth.token or "").strip()
        enabled = bool(config.core.allow_artifacts_download and auth.enabled and secret)
        return cls(enabled=enabled, secret=secret or "disabled", ttl_seconds=_resolve_ttl_seconds(config))

    @property
    def enabled(self) -> bool:
        """Return whether signed artifact download URLs are active."""
        return self._enabled

    @property
    def ttl_seconds(self) -> int:
        """Return signed URL TTL in seconds."""
        return self._ttl_seconds

    def _sign(
        self,
        *,
        server_id: str,
        session_id: str,
        artifact_id: str,
        filename: str,
        expires_at: int,
    ) -> str:
        """Compute HMAC-SHA256 signature hex digest for an artifact.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            artifact_id: Artifact identifier.
            filename: Artifact filename.
            expires_at: Expiry epoch timestamp.
        """
        payload = _signature_payload(
            server_id=server_id,
            session_id=session_id,
            artifact_id=artifact_id,
            filename=filename,
            expires_at=expires_at,
        )
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def issue(
        self,
        *,
        server_id: str,
        session_id: str,
        artifact_id: str,
        filename: str,
    ) -> dict[str, str]:
        """Build signed query params for one artifact download URL.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            artifact_id: Artifact identifier.
            filename: Artifact filename.

        Returns:
            Dict of signed query parameters, or empty dict when disabled.
        """
        if not self._enabled:
            return {}
        expires_at = int(time.time()) + self._ttl_seconds
        return {
            _EXP_PARAM: str(expires_at),
            _SIG_PARAM: self._sign(
                server_id=server_id,
                session_id=session_id,
                artifact_id=artifact_id,
                filename=filename,
                expires_at=expires_at,
            ),
        }

    def validate(
        self,
        *,
        server_id: str,
        session_id: str,
        artifact_id: str,
        filename: str,
        query_params: Mapping[str, str],
    ) -> bool:
        """Validate signed query credentials for one artifact download URL.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            artifact_id: Artifact identifier.
            filename: Artifact filename.
            query_params: Incoming request query parameters.

        Returns:
            True if the credential is valid and unexpired.
        """
        if not self._enabled:
            return False
        exp_value = query_params.get(_EXP_PARAM)
        signature = query_params.get(_SIG_PARAM)
        if not exp_value or not signature:
            return False
        try:
            expires_at = int(exp_value)
        except ValueError:
            return False
        if expires_at < int(time.time()):
            return False
        expected_signature = self._sign(
            server_id=server_id,
            session_id=session_id,
            artifact_id=artifact_id,
            filename=filename,
            expires_at=expires_at,
        )
        return hmac.compare_digest(signature, expected_signature)
