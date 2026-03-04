"""Signed short-lived credentials for upload endpoint authorization."""

from __future__ import annotations

import hashlib
import hmac
from secrets import token_hex
import time
from typing import TYPE_CHECKING, Mapping

from ..config import AdapterConfig
from .upload_nonce_store import InMemoryUploadNonceStore, UploadNonceStore

if TYPE_CHECKING:
    from ..telemetry import TelemetryManager

_EXP_PARAM = "mcp_upload_exp"
_NONCE_PARAM = "mcp_upload_nonce"
_SIG_PARAM = "mcp_upload_sig"


def _signature_payload(*, server_id: str, session_id: str, expires_at: int, nonce: str) -> bytes:
    """Build the canonical signature payload bytes.

    Args:
        server_id: Server identifier.
        session_id: Session identifier.
        expires_at: Expiry epoch timestamp.
        nonce: One-time random nonce.
    """
    return f"{server_id}\n{session_id}\n{expires_at}\n{nonce}".encode("utf-8")


class UploadCredentialManager:
    """Issues and validates one-time signed credentials for upload URLs."""

    def __init__(
        self,
        *,
        enabled: bool,
        secret: str,
        ttl_seconds: int,
        nonce_store: UploadNonceStore | None = None,
        telemetry: TelemetryManager | None = None,
    ) -> None:
        """Initialize the credential manager.

        Args:
            enabled: Whether signed uploads are active.
            secret: HMAC signing secret.
            ttl_seconds: Credential lifetime in seconds.
            nonce_store: Optional replay-protection backend.
            telemetry: Optional telemetry recorder.
        """
        self._enabled = enabled
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._nonce_store: UploadNonceStore = nonce_store or InMemoryUploadNonceStore()
        self._telemetry = telemetry

    @classmethod
    def from_config(
        cls,
        config: AdapterConfig,
        *,
        nonce_store: UploadNonceStore | None = None,
        telemetry: TelemetryManager | None = None,
    ) -> "UploadCredentialManager":
        """Build a manager from the global adapter configuration.

        Args:
            config: Full adapter configuration.
            nonce_store: Optional nonce store backend override.
            telemetry: Optional telemetry recorder.
        """
        auth = config.core.auth
        enabled = bool(auth.enabled)
        secret = (auth.signing_secret or auth.token or "").strip()
        return cls(
            enabled=enabled and bool(secret),
            secret=secret or "disabled",
            ttl_seconds=auth.signed_upload_ttl_seconds,
            nonce_store=nonce_store,
            telemetry=telemetry,
        )

    @property
    def enabled(self) -> bool:
        """Whether upload credential signing is enabled."""
        return self._enabled

    @property
    def ttl_seconds(self) -> int:
        """Credential time-to-live in seconds."""
        return self._ttl_seconds

    @property
    def nonce_backend(self) -> str:
        """Return nonce store backend label for diagnostics."""
        return self._nonce_store.backend

    def set_nonce_store(self, nonce_store: UploadNonceStore) -> None:
        """Replace nonce store backend for runtime fallback transitions.

        Args:
            nonce_store: New nonce store to use.
        """
        self._nonce_store = nonce_store

    def use_memory_nonce_store(self) -> None:
        """Switch replay protection to in-memory mode."""
        self._nonce_store = InMemoryUploadNonceStore()

    def _sign(self, *, server_id: str, session_id: str, expires_at: int, nonce: str) -> str:
        """Compute HMAC-SHA256 signature hex digest.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            expires_at: Expiry epoch timestamp.
            nonce: One-time random nonce.
        """
        payload = _signature_payload(
            server_id=server_id,
            session_id=session_id,
            expires_at=expires_at,
            nonce=nonce,
        )
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    async def issue(self, *, server_id: str, session_id: str) -> dict[str, str]:
        """Create signed one-time query parameters for an upload URL.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.

        Returns:
            Dict of signed query parameters, or empty dict when disabled.

        Raises:
            RuntimeError: If nonce reservation fails after retries.
        """
        if not self._enabled:
            await self._record_upload_credential(operation="issue", result="disabled", server_id=server_id)
            return {}
        now_epoch = int(time.time())
        expires_at = now_epoch + self._ttl_seconds
        for _ in range(5):
            nonce = token_hex(16)
            reserved = await self._nonce_store.reserve_nonce(
                nonce=nonce,
                server_id=server_id,
                session_id=session_id,
                expires_at=expires_at,
                now_epoch=now_epoch,
            )
            if not reserved:
                await self._record_nonce(operation="reserve", result="collision", server_id=server_id)
                continue
            await self._record_nonce(operation="reserve", result="success", server_id=server_id)
            signature = self._sign(
                server_id=server_id,
                session_id=session_id,
                expires_at=expires_at,
                nonce=nonce,
            )
            await self._record_upload_credential(operation="issue", result="issued", server_id=server_id)
            return {
                _EXP_PARAM: str(expires_at),
                _NONCE_PARAM: nonce,
                _SIG_PARAM: signature,
            }
        await self._record_upload_credential(operation="issue", result="reserve_failed", server_id=server_id)
        raise RuntimeError("Failed to reserve upload nonce after retries.")

    async def validate_and_consume(
        self,
        *,
        server_id: str,
        session_id: str,
        query_params: Mapping[str, str],
    ) -> bool:
        """Validate signed upload query params and consume them once.

        Args:
            server_id: Server identifier.
            session_id: Session identifier.
            query_params: Incoming request query parameters.

        Returns:
            True if the credential is valid and successfully consumed.
        """
        if not self._enabled:
            await self._record_upload_credential(operation="validate", result="disabled", server_id=server_id)
            return False

        exp_value = query_params.get(_EXP_PARAM)
        nonce = query_params.get(_NONCE_PARAM)
        signature = query_params.get(_SIG_PARAM)
        if not exp_value or not nonce or not signature:
            await self._record_upload_credential(operation="validate", result="missing_fields", server_id=server_id)
            return False
        try:
            expires_at = int(exp_value)
        except ValueError:
            await self._record_upload_credential(operation="validate", result="invalid_expiry", server_id=server_id)
            return False

        now_epoch = int(time.time())
        if expires_at < now_epoch:
            await self._record_upload_credential(operation="validate", result="expired", server_id=server_id)
            return False

        expected_signature = self._sign(
            server_id=server_id,
            session_id=session_id,
            expires_at=expires_at,
            nonce=nonce,
        )
        if not hmac.compare_digest(signature, expected_signature):
            await self._record_upload_credential(operation="validate", result="bad_signature", server_id=server_id)
            return False

        consumed = await self._nonce_store.consume_nonce(
            nonce=nonce,
            server_id=server_id,
            session_id=session_id,
            expires_at=expires_at,
            now_epoch=now_epoch,
        )
        await self._record_nonce(operation="consume", result="success" if consumed else "invalid", server_id=server_id)
        await self._record_upload_credential(
            operation="validate",
            result="accepted" if consumed else "replay_or_invalid",
            server_id=server_id,
        )
        return consumed

    async def _record_nonce(self, *, operation: str, result: str, server_id: str) -> None:
        """Emit a telemetry event for nonce operations.

        Args:
            operation: Nonce operation name.
            result: Operation result label.
        """
        if self._telemetry is None:
            return
        await self._telemetry.record_nonce_operation(
            operation=operation,
            result=result,
            backend=self._nonce_store.backend,
            server_id=server_id,
        )

    async def _record_upload_credential(self, *, operation: str, result: str, server_id: str) -> None:
        """Emit a telemetry event for credential operations.

        Args:
            operation: Credential operation name.
            result: Operation result label.
        """
        if self._telemetry is None:
            return
        await self._telemetry.record_upload_credential_event(
            operation=operation,
            result=result,
            backend=self._nonce_store.backend,
            server_id=server_id,
        )
