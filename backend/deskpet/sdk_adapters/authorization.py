"""SDK AuthorizationPort backed by the product authorization saga."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from dataclasses import replace

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
)
from deskpet.product_state.task_grants import DurableTaskGrantAuthority
from deskpet.types.task_grants import TaskGrant
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
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._identity_factory = identity_factory
        self._grant_authority = grant_authority
        self._grant_factory = grant_factory
        self._clock = clock
        self._fault = fault

    async def prepare(self, prepared: PreparedToolEffect) -> AuthorizationResult:
        # Product policies expose the Host authorization port as
        # ``decide(prepared, request=...)``.  Older unit fixtures supplied a
        # plain callable, so keep that form as a compatibility path while
        # honoring the real object contract used by the SDK runtime.
        decide = getattr(self._policy, "decide", None)
        if callable(decide):
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
            self._grant_authority.activate(
                identity.grant_id,
                version=identity.grant_version,
                policy_generation=identity.policy_generation,
                now=now,
            )
            return AuthorizationResult(
                AuthorizationDecision.ALLOW,
                receipt_ref=(
                    f"product-authorization-prepared:{record.identity.authorization_id}:"
                    f"{record.request_fingerprint}"
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
            self._repository.abort(
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
        getattr(self._repository, operation)(
            record.identity.authorization_id,
            expected_version=record.version,
            reason_hash=_hash({"operation": operation, "reason": reason}),
            now=now,
        )
        grant_operation = "expire" if operation == "expire" else "revoke"
        getattr(self._grant_authority, grant_operation)(
            record.identity.grant_id,
            version=record.identity.grant_version,
            policy_generation=record.identity.policy_generation,
            now=now,
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
            or _hash(record.identity.arguments) != _hash(dict(prepared.call.arguments))
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


__all__ = ("ProductAuthorizationAdapter",)
