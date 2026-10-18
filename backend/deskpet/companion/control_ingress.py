"""Trusted Companion mutation ingress for the two Tauri control windows."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .control_command_canonical import (
    CanonicalCommandError,
    parse_canonical_json,
)
from .control_credentials import (
    WindowControlCredential,
    WindowControlCredentialError,
    WindowControlCredentialVerifier,
)
from .identity import (
    HumanIdentity,
    ProfileBindingCoordinator,
    load_or_create_local_identity,
)
from .identity_gate import IdentityReadyGate

logger = logging.getLogger(__name__)

PRIVILEGED_CONTROL_KINDS = frozenset(
    {
        "companion_profile_bind",
        "companion_profile_unbind",
    }
)


def is_privileged_control_kind(kind: object) -> bool:
    return isinstance(kind, str) and kind in PRIVILEGED_CONTROL_KINDS


class CompanionControlIngressError(RuntimeError):
    def __init__(self, code: str, *, rechallenge: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.rechallenge = rechallenge


@dataclass(frozen=True, slots=True)
class ControlConnectionChallenge:
    connection_id: str
    control_epoch: int
    challenge: str
    challenge_hash: str
    request_seq: int
    binding_epoch: int
    expires_at: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ControlConnectionChallenge:
        return cls(
            connection_id=str(value["connection_id"]),
            control_epoch=int(value["control_epoch"]),
            challenge=str(value["challenge"]),
            challenge_hash=str(value["challenge_hash"]),
            request_seq=int(value["request_seq"]),
            binding_epoch=int(value["binding_epoch"]),
            expires_at=str(value["expires_at"]),
        )

    def public_payload(self) -> dict[str, str]:
        return {
            "connectionId": self.connection_id,
            "controlEpoch": str(self.control_epoch),
            "challenge": self.challenge,
            "requestSeq": str(self.request_seq),
            "bindingEpoch": str(self.binding_epoch),
            "expiresAt": self.expires_at,
        }

    def advance(self, *, request_seq: int, binding_epoch: int) -> ControlConnectionChallenge:
        return ControlConnectionChallenge(
            connection_id=self.connection_id,
            control_epoch=self.control_epoch,
            challenge=self.challenge,
            challenge_hash=self.challenge_hash,
            request_seq=request_seq,
            binding_epoch=binding_epoch,
            expires_at=self.expires_at,
        )


class TrustedAuthSnapshotProvider(Protocol):
    async def current_snapshot(self) -> Mapping[str, Any]: ...


class LocalAuthSnapshotProvider:
    """恒定返回本地身份快照。

    2026-08-09：relay 托管登录整套移除，取代原
    ``RegistryRelayAuthSnapshotProvider``（读 OS keychain 的 relay
    access_token → 调 ``/v1/me`` 换 user_id）。

    那条路径正是 WBUI-DEF-AUTH-01 的病灶：backend 在进程生命周期内缓存
    token，token 过期后既不重读 keychain 也不触发刷新，未绑定过的 profile
    会永久卡在「正在恢复身份…」且无 UI 恢复入口——supervisor 自动重启
    backend 同样触发。身份改为纯本地派生后，该缺陷在结构上消失：
    没有远端 token，就没有过期与刷新。

    "账户"概念随 relay 一并退役——用户身份就是这台机器上的本地 profile
    （``load_or_create_local_identity(user_data_dir)``，见
    ``validate_auth_snapshot``）。
    """

    async def current_snapshot(self) -> Mapping[str, Any]:
        return {"mode": "local", "user_id": None}


def validate_auth_snapshot(snapshot: Mapping[str, Any], *, user_data_dir: str) -> HumanIdentity:
    """Validate the signed main-window auth snapshot and derive its owner."""

    mode = snapshot.get("mode")
    user_id = snapshot.get("user_id")
    # 2026-08-09：relay 模式移除，只剩 local。保留 mode 字段与校验是为了
    # 让"快照形状不对"仍然显式失败，而不是静默按 local 处理。
    if mode == "local":
        if user_id is not None:
            raise CompanionControlIngressError("local_auth_snapshot_invalid")
        return load_or_create_local_identity(user_data_dir)
    raise CompanionControlIngressError("auth_snapshot_mode_invalid")


class CompanionControlIngress:
    """Verifies signed facts before delegating one atomic Store mutation."""

    def __init__(
        self,
        *,
        store: Any,
        coordinator: ProfileBindingCoordinator,
        identity_gate: IdentityReadyGate,
        verifier: WindowControlCredentialVerifier,
        user_data_dir: str,
        device_scope: str = "desktop",
        challenged_ttl_seconds: int = 45,
        challenged_quota: int = 16,
        trusted_auth_provider: TrustedAuthSnapshotProvider,
    ) -> None:
        self.store = store
        self.coordinator = coordinator
        self.identity_gate = identity_gate
        self.verifier = verifier
        self.user_data_dir = user_data_dir
        self.device_scope = device_scope
        self.challenged_ttl_seconds = challenged_ttl_seconds
        self.challenged_quota = challenged_quota
        self.trusted_auth_provider = trusted_auth_provider

    def open_challenge(
        self, *, requested_window_label: str, requested_scope: str
    ) -> ControlConnectionChallenge:
        row = self.store.open_profile_control_challenge(
            device_scope=self.device_scope,
            requested_window_label=requested_window_label,
            requested_scope=requested_scope,
            backend_process_instance_id=self.verifier.backend_process_instance_id,
            challenged_ttl_seconds=self.challenged_ttl_seconds,
            challenged_quota=self.challenged_quota,
        )
        return ControlConnectionChallenge.from_mapping(row)

    @staticmethod
    def parse_privileged_frame(raw: str | bytes) -> Mapping[str, Any] | None:
        """Strictly reparse any frame that asks for a privileged mutation."""

        try:
            probe = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return None
        if not isinstance(probe, Mapping):
            return None
        if not is_privileged_control_kind(probe.get("type")):
            return None
        try:
            strict = parse_canonical_json(raw)
        except CanonicalCommandError as exc:
            raise CompanionControlIngressError(f"canonical_command_rejected:{exc}") from exc
        if not isinstance(strict, Mapping):
            raise CompanionControlIngressError("control_command_not_object")
        return strict

    def _control_facts(
        self,
        *,
        message: Mapping[str, Any],
        challenge: ControlConnectionChallenge,
        command_kind: str,
        body: object,
        expected_window_label: str,
        expected_scope: str,
        binding_epoch: int,
    ) -> Mapping[str, Any]:
        payload = message.get("payload")
        envelope = payload if isinstance(payload, Mapping) else message
        raw_credential = envelope.get("credential")
        if not isinstance(raw_credential, Mapping):
            raise CompanionControlIngressError("window_credential_required")
        try:
            credential = WindowControlCredential.from_mapping(raw_credential)
            request_hash = self.verifier.verify(
                credential,
                expected_connection_id=challenge.connection_id,
                expected_control_epoch=challenge.control_epoch,
                expected_challenge_hash=challenge.challenge_hash,
                expected_window_label=expected_window_label,
                expected_scope=expected_scope,
                expected_request_seq=challenge.request_seq,
                expected_binding_epoch=binding_epoch,
                command_kind=command_kind,
                body=body,
            )
        except (WindowControlCredentialError, CanonicalCommandError) as exc:
            code = str(exc)
            # A credential whose signed facts do not match the active
            # connection/challenge must fail closed. Issuing another challenge
            # immediately lets a broken or stale signer create an unbounded
            # request/rechallenge loop; recovery belongs to a fresh transport
            # connection (or an explicit user retry), not a tight server loop.
            raise CompanionControlIngressError(
                code,
                rechallenge=(code != "credential_facts_mismatch"),
            ) from exc
        return {
            "connection_id": challenge.connection_id,
            "backend_process_instance_id": self.verifier.backend_process_instance_id,
            "control_epoch": challenge.control_epoch,
            "challenge_hash": challenge.challenge_hash,
            "window_label": expected_window_label,
            "scope": expected_scope,
            "request_seq": challenge.request_seq,
            "command_kind": command_kind,
            "request_hash": request_hash,
            "binding_epoch": binding_epoch,
            "credential_nonce_hash": hashlib.sha256(credential.nonce.encode("utf-8")).hexdigest(),
        }

    async def _identity(self, snapshot: Mapping[str, Any]) -> HumanIdentity:
        trusted = await self.trusted_auth_provider.current_snapshot()
        requested = {
            "mode": snapshot.get("mode"),
            "user_id": snapshot.get("user_id"),
        }
        authoritative = {
            "mode": trusted.get("mode"),
            "user_id": trusted.get("user_id"),
        }
        if requested != authoritative:

            def _identity_hash(value: Any) -> str | None:
                if value is None:
                    return None
                return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]

            logger.warning(
                "companion auth snapshot mismatch "
                "requested_mode=%s authoritative_mode=%s "
                "requested_user_hash=%s authoritative_user_hash=%s",
                requested["mode"],
                authoritative["mode"],
                _identity_hash(requested["user_id"]),
                _identity_hash(authoritative["user_id"]),
            )
            raise CompanionControlIngressError(
                "auth_snapshot_mismatch",
                rechallenge=False,
            )
        return validate_auth_snapshot(authoritative, user_data_dir=self.user_data_dir)

    async def execute(
        self,
        message: Mapping[str, Any],
        *,
        challenge: ControlConnectionChallenge,
    ) -> Mapping[str, Any]:
        command_kind = message.get("type")
        payload = message.get("payload")
        envelope = payload if isinstance(payload, Mapping) else message
        if command_kind == "companion_profile_bind":
            snapshot = envelope.get("auth_snapshot")
            if not isinstance(snapshot, Mapping):
                raise CompanionControlIngressError("auth_snapshot_required")
            body = {"auth_snapshot": dict(snapshot)}
            facts = self._control_facts(
                message=message,
                challenge=challenge,
                command_kind=command_kind,
                body=body,
                expected_window_label="main",
                expected_scope="identity_bind",
                binding_epoch=challenge.binding_epoch,
            )
            identity = await self._identity(snapshot)
            owner = self.store.resolve_active_profile(
                identity_namespace_hash=identity.identity_namespace_hash
            )
            generation = (
                owner.profile_generation
                if owner is not None
                else self.store.next_profile_generation(profile_id=identity.profile_id)
            )
            frozen = await self.coordinator.bind(
                identity=identity,
                profile_generation=generation,
                expected_binding_epoch=challenge.binding_epoch,
                control_facts=facts,
            )
            return {
                "type": "companion_profile_bound",
                "payload": {
                    "profile_id": frozen.owner.profile_id,
                    "profile_generation": frozen.owner.profile_generation,
                    "owner_key": frozen.owner_key,
                    "binding_epoch": str(frozen.binding_epoch),
                    "next_request_seq": str(challenge.request_seq + 1),
                },
            }

        if command_kind == "companion_profile_unbind":
            reason_code = envelope.get("reason_code", "trusted_logout")
            if not isinstance(reason_code, str) or not reason_code:
                raise CompanionControlIngressError("unbind_reason_invalid")
            body = {"reason_code": reason_code}
            facts = self._control_facts(
                message=message,
                challenge=challenge,
                command_kind=command_kind,
                body=body,
                expected_window_label="main",
                expected_scope="identity_bind",
                binding_epoch=challenge.binding_epoch,
            )
            await self.coordinator.unbind(
                expected_binding_epoch=challenge.binding_epoch,
                control_facts=facts,
            )
            return {
                "type": "companion_profile_unbound",
                "payload": {
                    "binding_epoch": str(challenge.binding_epoch + 1),
                    "next_request_seq": str(challenge.request_seq + 1),
                },
            }

        raise CompanionControlIngressError(
            "unsupported_companion_control_command",
            rechallenge=False,
        )


__all__ = [
    "CompanionControlIngress",
    "CompanionControlIngressError",
    "ControlConnectionChallenge",
    "PRIVILEGED_CONTROL_KINDS",
    "RegistryRelayAuthSnapshotProvider",
    "TrustedAuthSnapshotProvider",
    "is_privileged_control_kind",
    "validate_auth_snapshot",
]
