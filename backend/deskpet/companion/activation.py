"""Durable Companion capability-activation dispatcher.

The model may propose a candidate, but it never chooses or invokes the
capability lifecycle directly.  This module turns one already-admitted
Companion mutation row into short-fenced runtime starts and one trusted
Platform receipt.  CapabilityStore remains the only active-binding authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol

from .authority import RevocationBarrier
from .contracts import (
    CapabilityMutationReceipt,
    CandidateMode,
    MutationAction,
    MutationRequest,
    OwnerRef,
)
from .store import canonical_hash


class ActivationDispatchError(RuntimeError):
    """Fail-closed activation saga error with a stable reason code."""

    def __init__(self, reason_code: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.outcome_unknown = outcome_unknown


@dataclass(frozen=True, slots=True)
class CapabilityMutationAuthorization:
    """Host-issued, action-discriminated authority for one request claim."""

    request_id: str
    request_fingerprint: str
    action: MutationAction
    target_owner_key: str
    target_scope: str
    target_scope_key: str
    pack_id: str
    manager_idempotency_key: str
    target_expected_binding_generation: int
    claim_owner: str
    claim_epoch: int
    revocation_epoch: int
    candidate_id: str | None = None
    candidate_mode: CandidateMode | None = None
    target_version: str | None = None
    target_manifest_hash: str | None = None
    target_package_hash: str | None = None
    target_archive_hash: str | None = None
    source_fence_hash: str | None = None
    report_id: str | None = None
    risk_id: str | None = None
    decision_id: str | None = None
    risk_ack: str | None = None
    rollback_kind: str | None = None
    activation_mode: str = "normal"
    cause_ref: str | None = None
    authorization_hash: str = ""

    def __post_init__(self) -> None:
        if not self.authorization_hash:
            object.__setattr__(
                self,
                "authorization_hash",
                canonical_hash(self.facts()),
            )
        if self.authorization_hash != canonical_hash(self.facts()):
            raise ValueError("capability mutation authorization hash mismatch")

    def facts(self) -> Mapping[str, object]:
        return {
            "schema_version": 1,
            "request_id": self.request_id,
            "request_fingerprint": self.request_fingerprint,
            "action": self.action.value,
            "target_owner_key": self.target_owner_key,
            "target_scope": self.target_scope,
            "target_scope_key": self.target_scope_key,
            "pack_id": self.pack_id,
            "manager_idempotency_key": self.manager_idempotency_key,
            "target_expected_binding_generation":
                self.target_expected_binding_generation,
            "claim_owner": self.claim_owner,
            "claim_epoch": self.claim_epoch,
            "revocation_epoch": self.revocation_epoch,
            "candidate_id": self.candidate_id,
            "candidate_mode": (
                None if self.candidate_mode is None else self.candidate_mode.value
            ),
            "target_version": self.target_version,
            "target_manifest_hash": self.target_manifest_hash,
            "target_package_hash": self.target_package_hash,
            "target_archive_hash": self.target_archive_hash,
            "source_fence_hash": self.source_fence_hash,
            "report_id": self.report_id,
            "risk_id": self.risk_id,
            "decision_id": self.decision_id,
            "risk_ack": self.risk_ack,
            "rollback_kind": self.rollback_kind,
            "activation_mode": self.activation_mode,
            "cause_ref": self.cause_ref,
        }


@dataclass(frozen=True, slots=True)
class PreparedMutationInstance:
    ordinal: int
    instance_id: str
    adapter_id: str
    instance_hash: str


@dataclass(frozen=True, slots=True)
class PreparedCapabilityMutation:
    """Trusted Platform staging result persisted before any runtime starts."""

    manager_operation_id: str
    runtime_set_ref: str | None
    runtime_set_hash: str | None
    instances: tuple[PreparedMutationInstance, ...]
    prepared_hash: str

    def __post_init__(self) -> None:
        if not self.manager_operation_id:
            raise ValueError("manager operation id is required")
        if (self.runtime_set_ref is None) != (self.runtime_set_hash is None):
            raise ValueError("runtime set ref/hash must be both present or absent")
        if tuple(item.ordinal for item in self.instances) != tuple(
            range(len(self.instances))
        ):
            raise ValueError("prepared runtime instances must use canonical ordinals")
        expected = canonical_hash(
            {
                "schema_version": 1,
                "manager_operation_id": self.manager_operation_id,
                "runtime_set_ref": self.runtime_set_ref,
                "runtime_set_hash": self.runtime_set_hash,
                "instances": [
                    {
                        "ordinal": item.ordinal,
                        "instance_id": item.instance_id,
                        "adapter_id": item.adapter_id,
                        "instance_hash": item.instance_hash,
                    }
                    for item in self.instances
                ],
            }
        )
        if expected != self.prepared_hash:
            raise ValueError("prepared capability mutation hash mismatch")


@dataclass(frozen=True, slots=True)
class RuntimeInstanceStartOutcome:
    outcome: Literal["started", "not_started", "unknown"]
    start_receipt_hash: str
    reason_code: str


class ActivationStorePort(Protocol):
    def claim_capability_mutation_request(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        lease_seconds: float,
    ) -> Mapping[str, Any]: ...

    def get_capability_mutation_request(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
    ) -> Mapping[str, Any]: ...

    def record_capability_mutation_prepared(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        claim_epoch: int,
        manager_operation_id: str,
        runtime_set_ref: str | None,
        runtime_set_hash: str | None,
        reason_code: str,
    ) -> Mapping[str, Any]: ...

    def mark_capability_mutation_publishing(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        claim_epoch: int,
        reason_code: str,
    ) -> Mapping[str, Any]: ...

    def fail_capability_mutation_request(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        claim_epoch: int,
        status: str,
        reason_code: str,
    ) -> Mapping[str, Any]: ...

    def settle_capability_mutation_receipt(
        self,
        owner: OwnerRef,
        receipt: CapabilityMutationReceipt,
    ) -> Mapping[str, Any]: ...


class CapabilityMutationPlatformPort(Protocol):
    """Only host-side façade allowed to reach CapabilityPackManager."""

    async def prepare_mutation(
        self,
        authorization: CapabilityMutationAuthorization,
        *,
        persisted_operation_id: str | None,
        persisted_runtime_set_ref: str | None,
        persisted_runtime_set_hash: str | None,
    ) -> PreparedCapabilityMutation: ...

    async def start_runtime_instance(
        self,
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
        instance: PreparedMutationInstance,
    ) -> RuntimeInstanceStartOutcome: ...

    async def await_runtime_instance_health(
        self,
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
        instance: PreparedMutationInstance,
        start: RuntimeInstanceStartOutcome,
    ) -> bool: ...

    async def activate_mutation(
        self,
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
    ) -> CapabilityMutationReceipt: ...

    async def abort_mutation(
        self,
        authorization: CapabilityMutationAuthorization,
        prepared: PreparedCapabilityMutation,
        *,
        reason_code: str,
    ) -> bool: ...


class ActivationDecisionStorePort(Protocol):
    def record_activation_decision(
        self,
        owner: OwnerRef,
        *,
        decision_id: str,
        nonce: str,
        candidate_id: str,
        report_id: str,
        risk_id: str,
        decision: str,
        actor: str,
        activation_package_hash: str | None,
        activation_code_digest: str | None,
        activation_risk_ack: str,
        reason_code: str,
    ) -> Mapping[str, Any]: ...

    def create_capability_mutation_request(
        self, owner: OwnerRef, request: MutationRequest
    ) -> Mapping[str, Any]: ...


class ActivationDecisionCoordinator:
    """Persist a decision first, then idempotently enqueue its lifecycle saga."""

    def __init__(self, store: ActivationDecisionStorePort) -> None:
        self._store = store

    def decide_and_enqueue(
        self,
        owner: OwnerRef,
        *,
        decision_id: str,
        nonce: str,
        candidate_id: str,
        report_id: str,
        risk_id: str,
        actor: str,
        activation_package_hash: str,
        activation_code_digest: str | None,
        activation_risk_ack: str,
        reason_code: str,
        mutation: MutationRequest,
    ) -> Mapping[str, Any]:
        if mutation.candidate_id != candidate_id:
            raise ValueError("activation decision and mutation candidate mismatch")
        self._store.record_activation_decision(
            owner,
            decision_id=decision_id,
            nonce=nonce,
            candidate_id=candidate_id,
            report_id=report_id,
            risk_id=risk_id,
            decision="activate",
            actor=actor,
            activation_package_hash=activation_package_hash,
            activation_code_digest=activation_code_digest,
            activation_risk_ack=activation_risk_ack,
            reason_code=reason_code,
        )
        return self._store.create_capability_mutation_request(owner, mutation)


class ActivationDispatcher:
    """Run one durable mutation saga with short revocation leases."""

    def __init__(
        self,
        *,
        store: ActivationStorePort,
        platform: CapabilityMutationPlatformPort,
        barrier: RevocationBarrier,
    ) -> None:
        self._store = store
        self._platform = platform
        self._barrier = barrier

    async def execute(
        self,
        owner: OwnerRef,
        *,
        request_id: str,
        claim_owner: str,
        lease_seconds: float = 30.0,
    ) -> Mapping[str, Any]:
        claimed = self._store.claim_capability_mutation_request(
            owner,
            request_id=request_id,
            claim_owner=claim_owner,
            lease_seconds=lease_seconds,
        )
        authorization = self._authorization(claimed)
        prepared: PreparedCapabilityMutation | None = None
        try:
            prepared = await self._platform.prepare_mutation(
                authorization,
                persisted_operation_id=_optional(claimed, "manager_operation_id"),
                persisted_runtime_set_ref=_optional(claimed, "runtime_set_ref"),
                persisted_runtime_set_hash=_optional(claimed, "runtime_set_hash"),
            )
            self._store.record_capability_mutation_prepared(
                owner,
                request_id=request_id,
                claim_owner=claim_owner,
                claim_epoch=authorization.claim_epoch,
                manager_operation_id=prepared.manager_operation_id,
                runtime_set_ref=prepared.runtime_set_ref,
                runtime_set_hash=prepared.runtime_set_hash,
                reason_code="capability_mutation_prepared",
            )
            for instance in prepared.instances:
                async with self._barrier.shared() as lease:
                    self._revalidate_claim(
                        owner,
                        authorization=authorization,
                        expected_revocation_epoch=lease.epoch,
                    )
                    start = await self._platform.start_runtime_instance(
                        authorization, prepared, instance
                    )
                if start.outcome != "started":
                    raise ActivationDispatchError(
                        start.reason_code,
                        outcome_unknown=start.outcome == "unknown",
                    )
                healthy = await self._platform.await_runtime_instance_health(
                    authorization, prepared, instance, start
                )
                if not healthy:
                    raise ActivationDispatchError(
                        "capability_runtime_health_failed"
                    )

            async with self._barrier.shared() as lease:
                self._revalidate_claim(
                    owner,
                    authorization=authorization,
                    expected_revocation_epoch=lease.epoch,
                )
                self._store.mark_capability_mutation_publishing(
                    owner,
                    request_id=request_id,
                    claim_owner=claim_owner,
                    claim_epoch=authorization.claim_epoch,
                    reason_code="capability_mutation_publishing",
                )
                receipt = await self._platform.activate_mutation(
                    authorization, prepared
                )
            return self._store.settle_capability_mutation_receipt(owner, receipt)
        except ActivationDispatchError as exc:
            cleanup_ok = True
            if prepared is not None:
                cleanup_ok = await self._platform.abort_mutation(
                    authorization,
                    prepared,
                    reason_code=exc.reason_code,
                )
            status = (
                "cleanup_required"
                if not cleanup_ok
                else "unknown"
                if exc.outcome_unknown
                else "failed"
            )
            self._store.fail_capability_mutation_request(
                owner,
                request_id=request_id,
                claim_owner=claim_owner,
                claim_epoch=authorization.claim_epoch,
                status=status,
                reason_code=exc.reason_code,
            )
            raise

    def _revalidate_claim(
        self,
        owner: OwnerRef,
        *,
        authorization: CapabilityMutationAuthorization,
        expected_revocation_epoch: int,
    ) -> None:
        row = self._store.get_capability_mutation_request(
            owner, request_id=authorization.request_id
        )
        if (
            row["claim_owner"] != authorization.claim_owner
            or int(row["claim_epoch"]) != authorization.claim_epoch
            or str(row["request_fingerprint"])
            != authorization.request_fingerprint
            or str(row["manager_idempotency_key"])
            != authorization.manager_idempotency_key
            or expected_revocation_epoch != authorization.revocation_epoch
            or str(row["status"])
            not in {"claimed", "staging", "staged", "publishing"}
        ):
            raise ActivationDispatchError("capability_mutation_fence_stale")

    def _authorization(
        self, row: Mapping[str, Any]
    ) -> CapabilityMutationAuthorization:
        action = MutationAction(str(row["action"]))
        candidate_mode = (
            None
            if row["candidate_mode"] is None
            else CandidateMode(str(row["candidate_mode"]))
        )
        source_fence_hash = (
            None
            if row["source_fence_json"] is None
            else canonical_hash(_decode_json(str(row["source_fence_json"])))
        )
        return CapabilityMutationAuthorization(
            request_id=str(row["activation_request_id"]),
            request_fingerprint=str(row["request_fingerprint"]),
            action=action,
            target_owner_key=str(row["target_owner_key"]),
            target_scope=str(row["target_scope"]),
            target_scope_key=str(row["target_scope_key"]),
            pack_id=str(row["pack_id"]),
            manager_idempotency_key=str(row["manager_idempotency_key"]),
            target_expected_binding_generation=int(
                row["target_expected_binding_generation"]
            ),
            claim_owner=str(row["claim_owner"]),
            claim_epoch=int(row["claim_epoch"]),
            revocation_epoch=self._barrier.epoch,
            candidate_id=_optional(row, "candidate_id"),
            candidate_mode=candidate_mode,
            target_version=_optional(row, "target_version"),
            target_manifest_hash=_optional(row, "target_manifest_hash"),
            target_package_hash=_optional(row, "candidate_package_hash"),
            target_archive_hash=_optional(row, "archive_hash"),
            source_fence_hash=source_fence_hash,
            report_id=_optional(row, "report_id"),
            risk_id=_optional(row, "risk_id"),
            decision_id=_optional(row, "decision_id"),
            risk_ack=_optional(row, "risk_ack"),
            rollback_kind=_optional(row, "rollback_kind"),
            activation_mode=str(row["activation_mode"]),
            cause_ref=_optional(row, "cause_ref"),
        )


def _optional(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    return None if value is None else str(value)


def _decode_json(value: str) -> Mapping[str, object]:
    import json

    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("source fence must be an object")
    return decoded


__all__ = [
    "ActivationDispatchError",
    "ActivationDecisionCoordinator",
    "ActivationDecisionStorePort",
    "ActivationDispatcher",
    "ActivationStorePort",
    "CapabilityMutationAuthorization",
    "CapabilityMutationPlatformPort",
    "PreparedCapabilityMutation",
    "PreparedMutationInstance",
    "RuntimeInstanceStartOutcome",
]
