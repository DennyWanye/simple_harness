"""Trusted post-activation guard monitor.

Only typed host receipts can enter this adapter.  Preliminary classification
is performed before taking the expensive exclusive barrier/publish/gate fence;
an eligible incident is then compared with the exact current binding before
the Companion transaction creates a single rollback request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncContextManager, Literal, Mapping, Protocol

from .authority import RevocationBarrier
from .contracts import CapabilityGuardIncident, OwnerRef
from .store import canonical_hash

_ELIGIBLE_FAILURES = {
    "package_integrity",
    "runtime_contract",
    "schema_fingerprint",
    "effect_policy_fingerprint",
}
_TRUSTED_AUTHORITIES = {
    "capability_manager",
    "capability_registry",
    "tool_executor",
    "runtime_health",
}


@dataclass(frozen=True, slots=True)
class TrustedCapabilityFailureReceiptV1:
    source_authority: str
    source_event_id: str
    source_receipt_ref: str
    source_receipt_hash: str
    guard_id: str
    binding_generation: int
    pack_id: str
    version: str
    manifest_hash: str
    runtime_generation: int
    failure_class: str
    severity: str
    failure_fingerprint: str
    observed_at: str
    receipt_hash: str

    def __post_init__(self) -> None:
        if self.receipt_hash != canonical_hash(self.facts()):
            raise ValueError("capability failure receipt hash mismatch")

    def facts(self) -> Mapping[str, object]:
        return {
            "schema_version": 1,
            "source_authority": self.source_authority,
            "source_event_id": self.source_event_id,
            "source_receipt_ref": self.source_receipt_ref,
            "source_receipt_hash": self.source_receipt_hash,
            "guard_id": self.guard_id,
            "binding_generation": self.binding_generation,
            "pack_id": self.pack_id,
            "version": self.version,
            "manifest_hash": self.manifest_hash,
            "runtime_generation": self.runtime_generation,
            "failure_class": self.failure_class,
            "severity": self.severity,
            "failure_fingerprint": self.failure_fingerprint,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class GuardBindingSnapshotV1:
    target_owner_key: str
    target_scope: str
    target_scope_key: str
    pack_id: str
    version: str
    manifest_hash: str
    binding_generation: int
    owner_binding_set_stamp: str


class GuardFenceLeasePort(Protocol):
    snapshot: GuardBindingSnapshotV1

    def keep_closed_for_recovery(self, *, incident_id: str) -> None: ...


class GuardFencePlatformPort(Protocol):
    def guard_incident_fence(
        self,
        *,
        owner_key: str,
        scope: str,
        scope_key: str,
        pack_id: str,
        reason_code: str,
    ) -> AsyncContextManager[GuardFenceLeasePort]: ...


class GuardIncidentStorePort(Protocol):
    def record_capability_guard_incident(
        self,
        owner: OwnerRef,
        incident: CapabilityGuardIncident,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class GuardMonitorOutcomeV1:
    status: Literal["triggered", "rejected"]
    reason_code: str
    incident_id: str | None = None
    rollback_request_id: str | None = None


class ActivationGuardMonitor:
    """Validate one trusted failure and atomically close its target gate."""

    def __init__(
        self,
        *,
        store: GuardIncidentStorePort,
        platform: GuardFencePlatformPort,
        barrier: RevocationBarrier,
    ) -> None:
        self._store = store
        self._platform = platform
        self._barrier = barrier

    async def observe(
        self,
        owner: OwnerRef,
        receipt: TrustedCapabilityFailureReceiptV1,
        *,
        target_owner_key: str,
        target_scope: str,
        target_scope_key: str,
    ) -> GuardMonitorOutcomeV1:
        preliminary = self._preliminary_rejection(receipt)
        if preliminary is not None:
            return GuardMonitorOutcomeV1("rejected", preliminary)

        normalized = canonical_hash(
            {
                "failure_class": receipt.failure_class,
                "failure_fingerprint": receipt.failure_fingerprint,
                "pack_id": receipt.pack_id,
                "version": receipt.version,
                "manifest_hash": receipt.manifest_hash,
                "binding_generation": receipt.binding_generation,
            }
        )
        incident_id = canonical_hash(
            [
                "companion_guard_v1",
                receipt.guard_id,
                receipt.source_authority,
                receipt.source_event_id,
                normalized,
            ]
        )
        rollback_request_id = canonical_hash(
            ["companion_guard_v1", receipt.guard_id, incident_id, "rollback"]
        )
        request_fingerprint = canonical_hash(
            [
                "companion_guard_v1",
                receipt.guard_id,
                incident_id,
                rollback_request_id,
            ]
        )
        incident = CapabilityGuardIncident(
            guard_id=receipt.guard_id,
            incident_id=incident_id,
            source_authority=receipt.source_authority,
            source_event_id=receipt.source_event_id,
            binding_generation=receipt.binding_generation,
            pack_id=receipt.pack_id,
            version=receipt.version,
            manifest_hash=receipt.manifest_hash,
            runtime_generation=receipt.runtime_generation,
            failure_class=receipt.failure_class,
            failure_fingerprint=receipt.failure_fingerprint,
            source_receipt_ref=receipt.source_receipt_ref,
            source_receipt_hash=receipt.source_receipt_hash,
            observed_at=receipt.observed_at,
            dedupe_hash=normalized,
            rollback_request_id=rollback_request_id,
            request_fingerprint=request_fingerprint,
            reason_code=f"critical_{receipt.failure_class}",
        )

        async with self._barrier.exclusive():
            async with self._platform.guard_incident_fence(
                owner_key=target_owner_key,
                scope=target_scope,
                scope_key=target_scope_key,
                pack_id=receipt.pack_id,
                reason_code="companion_guard_incident",
            ) as fence:
                current = fence.snapshot
                if (
                    current.target_owner_key != target_owner_key
                    or current.target_scope != target_scope
                    or current.target_scope_key != target_scope_key
                    or current.pack_id != receipt.pack_id
                    or current.version != receipt.version
                    or current.manifest_hash != receipt.manifest_hash
                    or current.binding_generation != receipt.binding_generation
                    or not current.owner_binding_set_stamp
                ):
                    return GuardMonitorOutcomeV1(
                        "rejected", "guard_binding_superseded"
                    )
                self._store.record_capability_guard_incident(owner, incident)
                fence.keep_closed_for_recovery(incident_id=incident_id)
        return GuardMonitorOutcomeV1(
            "triggered",
            f"critical_{receipt.failure_class}",
            incident_id=incident_id,
            rollback_request_id=rollback_request_id,
        )

    @staticmethod
    def _preliminary_rejection(
        receipt: TrustedCapabilityFailureReceiptV1,
    ) -> str | None:
        if receipt.source_authority not in _TRUSTED_AUTHORITIES:
            return "guard_source_authority_untrusted"
        if receipt.severity != "critical":
            return "guard_severity_ineligible"
        if receipt.failure_class not in _ELIGIBLE_FAILURES:
            return "guard_failure_class_ineligible"
        return None


__all__ = [
    "ActivationGuardMonitor",
    "GuardBindingSnapshotV1",
    "GuardFenceLeasePort",
    "GuardFencePlatformPort",
    "GuardIncidentStorePort",
    "GuardMonitorOutcomeV1",
    "TrustedCapabilityFailureReceiptV1",
]
