"""SDK AuthorizationPort backed by the product authorization saga."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from dataclasses import dataclass, replace
from typing import Protocol

from simple_harness import thaw_json
from simple_harness.tools import (
    AuthorizationDecision,
    AuthorizationReceipt,
    AuthorizationRequest,
    AuthorizationResult,
    PreparedToolEffect,
)

from deskpet.product_state.authorization_saga import (
    AuthorizationSagaIdentity,
    AuthorizationSagaRepository,
    AuthorizationSagaState,
    auto_skill_approval_receipt,
)
from deskpet.product_state.task_grants import DurableTaskGrantAuthority
from deskpet.types.task_grants import TaskGrant


@dataclass(frozen=True, slots=True)
class AuthorizationTerminalEvidence:
    authorization_id: str
    run_id: str
    call_id: str
    effect_id: str
    principal_id: str
    terminal_kind: str
    saga_version: int
    reason_hash: str


class AuthorizationTerminalLifecyclePort(Protocol):
    def settle_authorization_terminal(
        self, evidence: AuthorizationTerminalEvidence
    ) -> object: ...
def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class ProductAuthorizationAdapter:
    def __init__(
        self,
        repository: AuthorizationSagaRepository,
        *,
        policy,
        identity_factory,
        grant_authority: DurableTaskGrantAuthority,
        grant_factory,
        clock=time.time,
        fault=None,
        terminal_lifecycle: AuthorizationTerminalLifecyclePort | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._identity_factory = identity_factory
        self._grant_authority = grant_authority
        self._grant_factory = grant_factory
        self._clock = clock
        self._fault = fault
        self._terminal_lifecycle = terminal_lifecycle

    async def prepare(self, prepared: PreparedToolEffect) -> AuthorizationResult:
        replay = await self._replay_of_durable_authorization(prepared)
        if isinstance(replay, AuthorizationResult) and replay.decision is AuthorizationDecision.DENY:
            return replay
        # Product policies expose the Host authorization port as
        # ``decide(prepared, request=...)``.  Older unit fixtures supplied a
        # plain callable, so keep that form as a compatibility path while
        # honoring the real object contract used by the SDK runtime.
        decide = getattr(self._policy, "decide", None)
        if replay is not None:
            result = replay
        elif callable(decide):
            result = decide(prepared, request=None)
        elif callable(self._policy):
            result = self._policy(prepared)
        else:
            raise TypeError("product authorization policy must be callable or expose decide")
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, AuthorizationResult):
            raise TypeError("product authorization policy must return AuthorizationResult")
        identity = self._identity_factory(prepared, result.request)
        if not isinstance(identity, AuthorizationSagaIdentity):
            raise TypeError("identity_factory must return AuthorizationSagaIdentity")
        # SDK owns the final replay nonce and intentionally replaces the Host's
        # suggested request nonce after prepare.  P1 must therefore remain
        # unbound; the final SDK nonce is CAS-bound at P2 below.
        identity = replace(identity, decision_nonce=None)
        grant = self._grant_factory(prepared, result)
        if not isinstance(grant, TaskGrant):
            raise TypeError("grant_factory must return TaskGrant")
        self._validate_grant(identity, grant)
        now = self._clock()
        record = self._repository.prepare(identity, now=now)
        self._hit("after_product_prepare")
        self._grant_authority.prepare(grant, now=now)
        if result.decision is AuthorizationDecision.ALLOW:
            if identity.tool_name == "skill_install":
                approval_for = getattr(self._policy, "auto_skill_approval_for", None)
                metadata = (
                    dict(approval_for(prepared)) if callable(approval_for) else {}
                )
                required = (
                    "skill_install_intent_id", "skill_install_content_digest",
                    "skill_install_member_digest", "skill_install_expires_at",
                )
                if any(not metadata.get(name) for name in required):
                    raise RuntimeError(
                        "Auto Skill approval requires a completed trusted preflight"
                    )
                nonce, host_hash = auto_skill_approval_receipt(
                    identity,
                    intent_id=str(metadata["skill_install_intent_id"]),
                    content_digest=str(metadata["skill_install_content_digest"]),
                    member_set_stamp=str(metadata["skill_install_member_digest"]),
                    expires_at=float(metadata["skill_install_expires_at"]),
                )
                record = self._repository.bind_auto_decision(
                    identity.authorization_id,
                    expected_version=record.version,
                    decision_nonce=nonce,
                    decision_version=identity.decision_version,
                    host_receipt_hash=host_hash,
                    now=now,
                )
                self._hit("product_auto_decision_bind")
            self._grant_authority.activate(
                identity.grant_id,
                version=identity.grant_version,
                policy_generation=identity.policy_generation,
                now=now,
            )
            return AuthorizationResult(
                AuthorizationDecision.ALLOW,
                receipt_ref=(
                    (
                        "product-auto-approved-skill-install-v1:"
                        if identity.tool_name == "skill_install"
                        else "product-authorization-prepared:"
                    )
                    + f"{record.identity.authorization_id}:{record.request_fingerprint}"
                ),
            )
        if result.decision is AuthorizationDecision.DENY:
            reason_hash = _hash({"reason_code": result.reason_code or "policy_denied"})
            self._repository.abort(
                identity.authorization_id,
                expected_version=record.version,
                reason_hash=reason_hash,
                now=now,
            )
            self._grant_authority.revoke(
                identity.grant_id,
                version=identity.grant_version,
                policy_generation=identity.policy_generation,
                now=now,
            )
        return result

    _REPLAYABLE = frozenset({
        AuthorizationSagaState.PREPARED,
        AuthorizationSagaState.DECISION_BOUND,
        AuthorizationSagaState.EFFECT_BOUND,
    })

    async def _replay_of_durable_authorization(
        self, prepared: PreparedToolEffect
    ) -> AuthorizationResult | None:
        """2026-09-30（架构方案 A）：同一次工具调用重启后重放，沿用原授权或具名拒绝。

        授权记录按"效果 + 调用"已有一条时，这就是重放：终态记录（取消/过期/撤销/隔离/已
        结清/已交出）具名拒绝；策略代数变了具名拒绝；票据过期则记录作废、具名拒绝；其余
        情况让策略用记录里那张票据恢复冻结事实，身份哈希与首次相同，下面的准备走幂等路径。
        返回 None = 不是重放（或手动票据还在等人，照常再问一次）。
        """

        record = self._repository.read_for_effect(
            prepared.effect_id.value, prepared.call.call_id.value
        )
        if record is None:
            return None
        identity = record.identity
        if (
            identity.run_id != prepared.run_id.value
            or identity.tool_name != prepared.call.name
            or _hash(identity.arguments) != _hash(thaw_json(prepared.call.arguments))
        ):
            return None  # another call under the same ids: the durable identity refuses it below
        if record.state not in self._REPLAYABLE:
            return AuthorizationResult(
                AuthorizationDecision.DENY,
                reason_code=f"authorization_replay_refused:{record.state.value}",
            )
        generation = self._grant_authority.current_policy_generation()
        if generation is not None and identity.policy_generation != generation:
            return AuthorizationResult(
                AuthorizationDecision.DENY, reason_code="policy_generation_moved"
            )
        durable = self._grant_authority.read(identity.grant_id)
        if durable is None:
            # The record was written but the process died before the grant was: this is the
            # second half of the same prepare, not a replay of an authorized call.  The normal
            # path recomputes the grant and the durable identity checks it as before.
            return None
        grant = durable.grant
        now = self._clock()
        if grant.expires_at is not None and grant.expires_at <= now:
            self._repository.abort(
                identity.authorization_id,
                expected_version=record.version,
                reason_hash=_hash({"reason_code": "grant_expired"}),
                now=now,
            )
            if durable.status in {"prepared", "active"}:
                self._grant_authority.expire(
                    identity.grant_id,
                    version=identity.grant_version,
                    policy_generation=identity.policy_generation,
                    now=now,
                )
            return AuthorizationResult(AuthorizationDecision.DENY, reason_code="grant_expired")
        if grant.source == "user" and record.state is AuthorizationSagaState.PREPARED:
            return None  # a manual grant still waiting for the person: ask again as before
        restore = getattr(self._policy, "restore_frozen_authorization", None)
        if not callable(restore):
            return None
        result = restore(prepared, grant=grant)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, AuthorizationResult):
            raise TypeError("restore_frozen_authorization must return AuthorizationResult")
        return result

    async def bind_decision(
        self,
        prepared: PreparedToolEffect,
        request: AuthorizationRequest,
        decision: AuthorizationDecision,
        sdk_receipt: AuthorizationReceipt,
    ) -> AuthorizationReceipt:
        record = self._record(prepared)
        if (
            record.bound_decision_nonce is not None
            and request.nonce != record.bound_decision_nonce
        ):
            raise RuntimeError("authorization decision nonce differs from durable identity")
        receipt_ref = (
            f"product-decision:{record.identity.authorization_id}:"
            f"{record.identity.decision_version}:{decision.value}"
        )
        host_hash = _hash(
            {
                "decision": decision.value,
                "nonce": request.nonce,
                "receipt_ref": receipt_ref,
                "sdk_receipt_hash": sdk_receipt.receipt_hash,
            }
        )
        if record.state is not AuthorizationSagaState.PREPARED:
            if (
                record.decision_sdk_receipt_hash == sdk_receipt.receipt_hash
                and record.decision_host_receipt_hash == host_hash
            ):
                if decision is AuthorizationDecision.ALLOW:
                    self._grant_authority.activate(
                        record.identity.grant_id,
                        version=record.identity.grant_version,
                        policy_generation=record.identity.policy_generation,
                        now=self._clock(),
                    )
                else:
                    self._grant_authority.revoke(
                        record.identity.grant_id,
                        version=record.identity.grant_version,
                        policy_generation=record.identity.policy_generation,
                        now=self._clock(),
                    )
                return AuthorizationReceipt(
                    receipt_ref, host_hash, sdk_receipt.receipt_hash
                )
            raise RuntimeError("authorization decision conflicts with durable binding")
        bound = self._repository.bind_decision(
            record.identity.authorization_id,
            expected_version=record.version,
            sdk_receipt_hash=sdk_receipt.receipt_hash,
            host_receipt_hash=host_hash,
            decision_nonce=request.nonce,
            decision_version=record.identity.decision_version,
            now=self._clock(),
        )
        self._hit("product_decision_bind")
        if decision is AuthorizationDecision.ALLOW:
            self._grant_authority.activate(
                record.identity.grant_id,
                version=record.identity.grant_version,
                policy_generation=record.identity.policy_generation,
                now=self._clock(),
            )
        else:
            terminal = self._repository.abort(
                record.identity.authorization_id,
                expected_version=bound.version,
                reason_hash=_hash({"decision": decision.value, "nonce": request.nonce}),
                now=self._clock(),
            )
            self._grant_authority.revoke(
                record.identity.grant_id,
                version=record.identity.grant_version,
                policy_generation=record.identity.policy_generation,
                now=self._clock(),
            )
            terminal_kind = "denied"
            if request.expires_at is not None and self._clock() >= request.expires_at:
                terminal_kind = "expired"
            self._emit_terminal(
                terminal,
                terminal_kind=terminal_kind,
                reason_hash=_hash({"decision": decision.value, "nonce": request.nonce}),
            )
        return AuthorizationReceipt(receipt_ref, host_hash, sdk_receipt.receipt_hash)

    async def bind_effect_handoff(
        self,
        prepared: PreparedToolEffect,
        authorization_receipt_ref: str,
        sdk_receipt: AuthorizationReceipt,
    ) -> AuthorizationReceipt:
        record = self._record(prepared)
        self._grant_authority.assert_active(
            record.identity.grant_id,
            version=record.identity.grant_version,
            policy_generation=record.identity.policy_generation,
            now=self._clock(),
        )
        effect_hash = _hash(
            {
                "authorization_receipt_ref": authorization_receipt_ref,
                "effect_id": prepared.effect_id.value,
                "phase": "effect_bound",
                "sdk_receipt_hash": sdk_receipt.receipt_hash,
            }
        )
        receipt_ref = (
            f"product-handoff:{record.identity.authorization_id}:"
            f"{record.identity.effect_id}"
        )
        handoff_hash = _hash(
            {
                "effect_receipt_hash": effect_hash,
                "receipt_ref": receipt_ref,
                "sdk_receipt_hash": sdk_receipt.receipt_hash,
            }
        )
        if record.state is AuthorizationSagaState.HANDOFF_COMMITTED:
            if (
                record.effect_sdk_receipt_hash == sdk_receipt.receipt_hash
                and record.effect_host_receipt_hash == effect_hash
                and record.handoff_sdk_receipt_hash == sdk_receipt.receipt_hash
                and record.handoff_host_receipt_hash == handoff_hash
            ):
                return AuthorizationReceipt(
                    receipt_ref, handoff_hash, sdk_receipt.receipt_hash
                )
            raise RuntimeError("effect handoff conflicts with durable binding")
        if record.state in {
            AuthorizationSagaState.PREPARED,
            AuthorizationSagaState.DECISION_BOUND,
        }:
            record = self._repository.bind_effect(
                record.identity.authorization_id,
                expected_version=record.version,
                sdk_receipt_hash=sdk_receipt.receipt_hash,
                host_receipt_hash=effect_hash,
                now=self._clock(),
            )
            self._hit("product_effect_bind")
        self._repository.commit_handoff(
            record.identity.authorization_id,
            expected_version=record.version,
            sdk_receipt_hash=sdk_receipt.receipt_hash,
            host_receipt_hash=handoff_hash,
            now=self._clock(),
        )
        self._hit("product_handoff_commit")
        return AuthorizationReceipt(receipt_ref, handoff_hash, sdk_receipt.receipt_hash)

    def cancel(self, prepared: PreparedToolEffect, *, reason: str) -> None:
        self._terminate(prepared, reason=reason, operation="abort")

    def expire(self, prepared: PreparedToolEffect, *, reason: str) -> None:
        self._terminate(prepared, reason=reason, operation="expire")

    def revoke(self, prepared: PreparedToolEffect, *, reason: str) -> None:
        self._terminate(prepared, reason=reason, operation="revoke")

    def mark_dispatch_unknown(
        self, prepared: PreparedToolEffect, *, evidence_hash: str
    ) -> None:
        record = self._record(prepared)
        self._repository.mark_dispatch_unknown(
            record.identity.authorization_id,
            expected_version=record.version,
            evidence_hash=evidence_hash,
            now=self._clock(),
        )

    def settle(self, prepared: PreparedToolEffect, *, outcome_hash: str) -> None:
        record = self._record(prepared)
        self._hit("before_product_settle")
        self._repository.settle(
            record.identity.authorization_id,
            expected_version=record.version,
            outcome_hash=outcome_hash,
            now=self._clock(),
        )

    def _terminate(
        self, prepared: PreparedToolEffect, *, reason: str, operation: str
    ) -> None:
        record = self._record(prepared)
        now = self._clock()
        reason_hash = _hash({"operation": operation, "reason": reason})
        terminal = getattr(self._repository, operation)(
            record.identity.authorization_id,
            expected_version=record.version,
            reason_hash=reason_hash,
            now=now,
        )
        grant_operation = "expire" if operation == "expire" else "revoke"
        getattr(self._grant_authority, grant_operation)(
            record.identity.grant_id,
            version=record.identity.grant_version,
            policy_generation=record.identity.policy_generation,
            now=now,
        )
        self._emit_terminal(
            terminal,
            terminal_kind={
                "abort": "cancelled",
                "expire": "expired",
                "revoke": "revoked",
            }[operation],
            reason_hash=reason_hash,
        )

    def _emit_terminal(self, record, *, terminal_kind: str, reason_hash: str) -> None:
        if self._terminal_lifecycle is None:
            return
        identity = record.identity
        self._terminal_lifecycle.settle_authorization_terminal(
            AuthorizationTerminalEvidence(
                authorization_id=identity.authorization_id,
                run_id=identity.run_id,
                call_id=identity.call_id,
                effect_id=identity.effect_id,
                principal_id=identity.principal_id,
                terminal_kind=terminal_kind,
                saga_version=record.version,
                reason_hash=reason_hash,
            )
        )

    def _record(self, prepared: PreparedToolEffect):
        record = self._repository.read_for_effect(
            prepared.effect_id.value, prepared.call.call_id.value
        )
        if record is None:
            raise RuntimeError("product authorization saga is missing")
        if (
            record.identity.run_id != prepared.run_id.value
            or record.identity.effect_id != prepared.effect_id.value
            or record.identity.call_id != prepared.call.call_id.value
            or record.identity.tool_name != prepared.call.name
            # SDK ToolCall recursively freezes nested JSON containers.  A
            # shallow dict() leaves MappingProxyType values inside arrays and
            # objects, which crashes the JSON hash during effect handoff.
            or _hash(record.identity.arguments)
            != _hash(thaw_json(prepared.call.arguments))
        ):
            raise RuntimeError("prepared Tool effect differs from durable authorization identity")
        return record

    def _hit(self, point: str) -> None:
        if self._fault is not None:
            self._fault(point)

    @staticmethod
    def _validate_grant(identity: AuthorizationSagaIdentity, grant: TaskGrant) -> None:
        if (
            grant.task_grant_id != identity.grant_id
            or grant.version != identity.grant_version
            or grant.policy_generation != identity.policy_generation
            or grant.fingerprint != identity.grant_fingerprint
            or grant.root_run_id != identity.root_run_id
            or grant.principal_id != identity.principal_id
        ):
            raise RuntimeError("TaskGrant identity differs from authorization identity")


__all__ = (
    "AuthorizationTerminalEvidence",
    "AuthorizationTerminalLifecyclePort",
    "ProductAuthorizationAdapter",
)
