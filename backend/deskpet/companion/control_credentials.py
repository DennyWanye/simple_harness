"""Host verification for Rust-issued window control credentials."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .control_command_canonical import (
    CanonicalCommandError,
    canonical_request_hash,
    credential_signed_payload,
    parse_canonical_u64,
)

# 2026-08-04 Workbench UI 改版（behavior-contract B5）：message-panel 窗口
# 并入主窗，companion_action 的合法窗口标签迁移为 "main"。五处同步迁移之一
# （Rust webview_permissions 白名单 / 本白名单 / control_ingress
# expected_window_label / SQL CHECK 迁移 007 / 前端 controlWs 常量）。
ALLOWED_WINDOW_SCOPES = frozenset(
    {("main", "identity_bind"), ("main", "companion_action")}
)


class WindowControlCredentialError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WindowControlBootstrap:
    backend_process_instance_id: str
    public_key_hex: str

    @classmethod
    def parse_line(cls, line: str) -> "WindowControlBootstrap":
        prefix = "WINDOW_CONTROL_BOOTSTRAP="
        if not line.startswith(prefix):
            raise WindowControlCredentialError("window_control_bootstrap_missing")
        try:
            value = json.loads(line[len(prefix) :])
            if value["schema"] != "window-control-bootstrap-v1":
                raise ValueError("schema")
            instance_id = str(value["backend_process_instance_id"])
            public_key_hex = str(value["public_key_hex"])
            Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WindowControlCredentialError(
                "window_control_bootstrap_malformed"
            ) from exc
        return cls(instance_id, public_key_hex)


@dataclass(frozen=True, slots=True)
class ControlChallenge:
    connection_id: str
    control_epoch: int
    challenge: str
    challenge_hash: str


@dataclass(frozen=True, slots=True)
class WindowControlCredential:
    backend_process_instance_id: str
    connection_id: str
    control_epoch: str
    window_label: str
    scope: str
    challenge_hash: str
    request_seq: str
    command_kind: str
    canonical_request_hash: str
    nonce: str
    issued_at: str
    expires_at: str
    signature_hex: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "WindowControlCredential":
        aliases = {
            "backend_process_instance_id": "backendProcessInstanceId",
            "connection_id": "connectionId",
            "control_epoch": "controlEpoch",
            "window_label": "windowLabel",
            "challenge_hash": "challengeHash",
            "request_seq": "requestSeq",
            "command_kind": "commandKind",
            "canonical_request_hash": "canonicalRequestHash",
            "issued_at": "issuedAt",
            "expires_at": "expiresAt",
            "signature_hex": "signatureHex",
        }
        try:
            return cls(
                **{
                    field: str(
                        value[field]
                        if field in value
                        else value[aliases.get(field, field)]
                    )
                    for field in cls.__dataclass_fields__
                }
            )
        except (KeyError, TypeError) as exc:
            raise WindowControlCredentialError("malformed_credential") from exc


def issue_control_challenge(*, control_epoch: int) -> ControlChallenge:
    if control_epoch < 1:
        raise ValueError("control_epoch must be positive")
    challenge = secrets.token_urlsafe(32)
    return ControlChallenge(
        connection_id=str(uuid.uuid4()),
        control_epoch=control_epoch,
        challenge=challenge,
        challenge_hash=hashlib.sha256(challenge.encode("utf-8")).hexdigest(),
    )


def assert_window_scope(window_label: str, scope: str) -> None:
    if (window_label, scope) not in ALLOWED_WINDOW_SCOPES:
        raise WindowControlCredentialError("window_scope_denied")


class WindowControlCredentialVerifier:
    def __init__(
        self,
        *,
        public_key_hex: str,
        backend_process_instance_id: str,
        max_clock_skew_seconds: int = 5,
    ) -> None:
        try:
            key_bytes = bytes.fromhex(public_key_hex)
            self._public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
        except (ValueError, TypeError) as exc:
            raise WindowControlCredentialError("malformed_public_key") from exc
        self.backend_process_instance_id = backend_process_instance_id
        self.max_clock_skew_seconds = max_clock_skew_seconds

    def __deepcopy__(self, _memo):
        # Immutable process credential verifier, shared by session contexts.
        return self

    def verify(
        self,
        credential: WindowControlCredential,
        *,
        expected_connection_id: str,
        expected_control_epoch: int,
        expected_challenge_hash: str,
        expected_window_label: str,
        expected_scope: str,
        expected_request_seq: int,
        expected_binding_epoch: int,
        command_kind: str,
        body: object,
        now: int | None = None,
    ) -> str:
        assert_window_scope(expected_window_label, expected_scope)
        expected_hash = canonical_request_hash(
            command_kind,
            str(expected_request_seq),
            str(expected_binding_epoch),
            body,
        )
        facts = (
            credential.backend_process_instance_id
            == self.backend_process_instance_id,
            credential.connection_id == expected_connection_id,
            parse_canonical_u64(
                credential.control_epoch, field="control_epoch"
            )
            == expected_control_epoch,
            credential.window_label == expected_window_label,
            credential.scope == expected_scope,
            hmac.compare_digest(
                credential.challenge_hash, expected_challenge_hash
            ),
            parse_canonical_u64(credential.request_seq, field="request_seq")
            == expected_request_seq,
            credential.command_kind == command_kind,
            hmac.compare_digest(
                credential.canonical_request_hash, expected_hash
            ),
        )
        if not all(facts):
            raise WindowControlCredentialError("credential_facts_mismatch")
        issued_at = parse_canonical_u64(credential.issued_at, field="issued_at")
        expires_at = parse_canonical_u64(credential.expires_at, field="expires_at")
        clock = int(time.time()) if now is None else now
        if issued_at > clock + self.max_clock_skew_seconds or expires_at < clock:
            raise WindowControlCredentialError("credential_expired_or_future")
        try:
            signature = bytes.fromhex(credential.signature_hex)
            payload = credential_signed_payload(
                backend_process_instance_id=credential.backend_process_instance_id,
                connection_id=credential.connection_id,
                control_epoch=credential.control_epoch,
                window_label=credential.window_label,
                scope=credential.scope,
                challenge_hash=credential.challenge_hash,
                request_seq=credential.request_seq,
                command_kind=credential.command_kind,
                canonical_request_hash_hex=credential.canonical_request_hash,
                nonce=credential.nonce,
                issued_at=credential.issued_at,
                expires_at=credential.expires_at,
            )
            self._public_key.verify(signature, payload)
        except (ValueError, InvalidSignature, CanonicalCommandError) as exc:
            raise WindowControlCredentialError("invalid_signature") from exc
        return expected_hash


__all__ = [
    "ALLOWED_WINDOW_SCOPES",
    "ControlChallenge",
    "WindowControlCredential",
    "WindowControlBootstrap",
    "WindowControlCredentialError",
    "WindowControlCredentialVerifier",
    "assert_window_scope",
    "issue_control_challenge",
]
