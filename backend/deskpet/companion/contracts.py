"""Stable, product-facing contracts for the Companion growth subsystem.

The model may propose values represented by these contracts, but only the host
constructs owner identity, binding fences, hashes, and lifecycle receipts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping, Sequence

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class CompanionConflictError(RuntimeError):
    """A stable id was replayed with different immutable facts."""


class CompanionOwnerError(RuntimeError):
    """The requested profile generation is absent, deleted, or stale."""


class CompanionLeaseError(RuntimeError):
    """A claim/settle operation did not own the current lease epoch."""


class CompanionStateError(RuntimeError):
    """A requested state transition is not legal."""


class CandidateMode(StrEnum):
    GENESIS = "genesis"
    UPDATE = "update"
    BUILTIN_OVERRIDE = "builtin_override"


class MutationAction(StrEnum):
    INSTALL = "install"
    UPDATE = "update"
    ROLLBACK = "rollback"
    UNINSTALL = "uninstall"
    DISABLE = "disable"


@dataclass(frozen=True, slots=True)
class OwnerRef:
    profile_id: str
    profile_generation: int

    def __post_init__(self) -> None:
        if not self.profile_id or self.profile_generation < 1:
            raise ValueError("owner requires profile_id and generation >= 1")


@dataclass(frozen=True, slots=True)
class GrowthEvent:
    owner: OwnerRef
    event_id: str
    source_kind: str
    source_ref: str
    context_key: str
    root_run_id: str
    reason_code: str
    payload: Mapping[str, JsonValue]
    event_hash: str | None = None
    retry_of: str | None = None
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class CandidatePackageFile:
    relative_path: str
    file_kind: str
    content_hash: str
    size_bytes: int
    blob_id: str
    file_mode: int = 0o644


@dataclass(frozen=True, slots=True)
class CandidatePackageBlob:
    blob_kind: str
    blob_id: str
    content_hash: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class CandidatePackage:
    package_id: str
    candidate_mode: CandidateMode
    pack_id: str
    version: str
    candidate_content_hash: str
    candidate_manifest_hash: str
    candidate_package_hash: str
    archive_hash: str
    effect_topology_hash: str
    source_facts: Mapping[str, JsonValue]
    target_facts: Mapping[str, JsonValue]
    files: Sequence[CandidatePackageFile] = field(default_factory=tuple)
    blobs: Sequence[CandidatePackageBlob] = field(default_factory=tuple)
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class CandidateAttempt:
    candidate_id: str
    candidate_attempt_key: str
    proposal_source_kind: str
    proposal_source_ref: str
    source_hash: str
    candidate_mode: CandidateMode
    target_id: str
    target_owner_key: str
    target_scope: str
    target_scope_key: str
    target_expected_absent: bool
    target_expected_binding_generation: int
    evidence_event_ids: Sequence[str]
    evidence_set_hash: str
    builder_receipt_ref: str
    builder_receipt_hash: str
    source_owner_key: str | None = None
    source_scope: str | None = None
    source_scope_key: str | None = None
    source_version: str | None = None
    source_manifest_hash: str | None = None
    source_binding_generation: int | None = None
    reservation_version: int | None = None
    reflection_job_id: str | None = None
    build_id: str | None = None
    attempt_generation: int = 1
    reason_code: str = "candidate_created"
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class GrowthDependency:
    dependency_kind: str
    dependency_id: str
    content_hash: str
    evidence_event_ids: Sequence[str] = field(default_factory=tuple)
    pack_id: str | None = None
    version: str | None = None
    manifest_hash: str | None = None
    binding_generation: int | None = None
    catalog_content_stamp: str | None = None
    effect_hash: str | None = None


@dataclass(frozen=True, slots=True)
class RunGrowthSnapshot:
    snapshot_id: str
    request_id: str
    run_id: str
    snapshot_generation: int
    snapshot_hash: str
    prior_snapshot_id: str | None = None
    prior_snapshot_hash: str | None = None
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class LeaseClaim:
    item_id: str
    claim_owner: str
    claim_epoch: int
    lease_expires_at: str
    attempt: int
    payload: Mapping[str, Any]
    event_kind: str | None = None
    event_id: str | None = None
    sink_kind: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class MutationRequest:
    request_id: str
    request_fingerprint: str
    action: MutationAction
    target_owner_key: str
    target_scope: str
    target_scope_key: str
    pack_id: str
    reason_code: str
    candidate_id: str | None = None
    candidate_mode: CandidateMode | None = None
    target_version: str | None = None
    target_manifest_hash: str | None = None
    target_package_hash: str | None = None
    target_archive_hash: str | None = None
    source_fence: Mapping[str, JsonValue] | None = None
    target_expected_absent: bool = False
    target_expected_binding_generation: int = 0
    rollback_kind: str | None = None
    activation_mode: str = "normal"
    cause_ref: str | None = None
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class EvaluationExecutionPermit:
    permit_id: str
    evaluation_id: str
    mode: str
    candidate_id: str
    package_hash: str
    manifest_hash: str
    archive_hash: str
    suite_hash: str
    runner_policy_hash: str
    issued_revocation_epoch: int
    preflight_ref: str
    preflight_hash: str
    risk_ref: str
    risk_hash: str
    permit_hash: str
    reason_code: str
    authorization_id: str | None = None
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class EvaluationCaseLaunch:
    launch_id: str
    evaluation_id: str
    case_id: str
    variant: str
    attempt_ordinal: int
    candidate_package_hash: str
    candidate_manifest_hash: str
    candidate_archive_hash: str
    suite_hash: str
    permit_mode: str
    permit_id: str
    permit_hash: str
    case_lease_epoch: int
    revocation_epoch: int
    adapter_id: str
    adapter_version: str
    adapter_fingerprint: str
    launch_fingerprint: str
    reason_code: str
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class CapabilityMutationReceipt:
    activation_request_id: str
    manager_operation_id: str
    action: MutationAction
    pack_id: str
    result_hash: str
    reason_code: str
    candidate_mode: CandidateMode | None = None
    version: str | None = None
    manifest_hash: str | None = None
    source_fence_hash: str | None = None
    runtime_set_ref: str | None = None
    runtime_set_hash: str | None = None
    target_owner_key: str | None = None
    target_scope: str | None = None
    binding_generation: int | None = None
    owner_binding_set_stamp: str | None = None
    process_projection_fingerprint: str | None = None
    target_user_owner_binding_set_stamp: str | None = None
    fallback_owner_key: str | None = None
    fallback_scope: str | None = None
    fallback_scope_key: str | None = None
    fallback_binding_id: str | None = None
    fallback_binding_generation: int | None = None
    fallback_pack_id: str | None = None
    fallback_version: str | None = None
    fallback_manifest_hash: str | None = None
    fallback_owner_binding_set_stamp: str | None = None
    fallback_process_projection_fingerprint: str | None = None
    fallback_runtime_set_ref: str | None = None
    fallback_runtime_set_hash: str | None = None
    absence_proof_hash: str | None = None
    open_guard: bool = False
    guard_id: str | None = None
    guard_policy_hash: str | None = None
    rollback_plan: Mapping[str, JsonValue] | None = None
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class CapabilityGuardIncident:
    guard_id: str
    incident_id: str
    source_authority: str
    source_event_id: str
    binding_generation: int
    pack_id: str
    version: str
    manifest_hash: str
    runtime_generation: int
    failure_class: str
    failure_fingerprint: str
    source_receipt_ref: str
    source_receipt_hash: str
    observed_at: str
    dedupe_hash: str
    rollback_request_id: str
    request_fingerprint: str
    reason_code: str
    severity: str = "critical"
    schema_version: int = 1


DETAIL_VISIBLE_TABLES: frozenset[str] = frozenset(
    {
        "growth_events",
        "preferences",
        "preference_evidence",
        "candidate_packages",
        "candidate_package_files",
        "candidate_artifacts",
        "candidate_evidence",
        "evaluation_reports",
        "risk_assessments",
        "growth_decisions",
        "capability_activation_requests",
        "capability_activation_receipts",
        "capability_activation_guards",
        "capability_guard_incidents",
        "capability_quarantines",
        "notifications",
        "audit_events",
        "lineage_edges",
    }
)
