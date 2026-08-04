"""Host-owned admission contracts for governed Skill/Workflow builds.

Nothing in this module launches a child or mutates CapabilityStore.  It makes
the authority boundary explicit: trusted root request facts are routed before
general builder admission, and only host-issued permits/receipts may cross the
Companion/execution database boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import InitVar, dataclass
from enum import StrEnum
from typing import Mapping, Protocol

from .contracts import JsonValue, OwnerRef
from .growth import StructuredGrowthProposalV1

_HOST_ISSUER = object()
_DIGEST_LENGTH = 64


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}_required")
    return value.strip()


def _digest(value: object, name: str) -> str:
    text = _required(value, name)
    if len(text) != _DIGEST_LENGTH or any(ch not in "0123456789abcdef" for ch in text):
        raise ValueError(f"{name}_invalid")
    return text


class RequestedEntryKind(StrEnum):
    SKILL = "skill"
    WORKFLOW = "workflow"
    TOOL = "tool"
    MCP_SERVER = "mcp_server"


class CapabilityBuildPublishPolicy(StrEnum):
    GENERAL_INSTALL = "general_install"
    CANDIDATE_ONLY = "candidate_only"


class GrowthBuildAdmissionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TrustedRootBuildRequestV1:
    """Facts derived by the product host, never from builder/model JSON."""

    owner: OwnerRef
    owner_key: str
    request_id: str
    root_run_id: str
    user_message_ref: str
    user_message_hash: str
    requested_entry_kinds: tuple[RequestedEntryKind | str, ...]
    explicit_capability_request: bool
    _host_token: InitVar[object] = None

    def __post_init__(self, _host_token: object) -> None:
        if _host_token is not _HOST_ISSUER:
            raise GrowthBuildAdmissionError("trusted_build_request_host_only")
        for name in (
            "owner_key",
            "request_id",
            "root_run_id",
            "user_message_ref",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        object.__setattr__(
            self, "user_message_hash", _digest(self.user_message_hash, "user_message_hash")
        )
        kinds = tuple(
            sorted(
                {RequestedEntryKind(item) for item in self.requested_entry_kinds},
                key=lambda item: item.value,
            )
        )
        if not kinds:
            raise ValueError("requested_entry_kinds_required")
        object.__setattr__(self, "requested_entry_kinds", kinds)
        if not isinstance(self.explicit_capability_request, bool):
            raise ValueError("explicit_capability_request_bool_required")

    @classmethod
    def issue(
        cls,
        *,
        owner: OwnerRef,
        owner_key: str,
        request_id: str,
        root_run_id: str,
        user_message_ref: str,
        user_message_hash: str,
        requested_entry_kinds: tuple[RequestedEntryKind | str, ...],
        explicit_capability_request: bool,
    ) -> "TrustedRootBuildRequestV1":
        return cls(
            owner=owner,
            owner_key=owner_key,
            request_id=request_id,
            root_run_id=root_run_id,
            user_message_ref=user_message_ref,
            user_message_hash=user_message_hash,
            requested_entry_kinds=requested_entry_kinds,
            explicit_capability_request=explicit_capability_request,
            _host_token=_HOST_ISSUER,
        )


@dataclass(frozen=True, slots=True)
class ExplicitGrowthBuildAdmissionV1:
    owner: OwnerRef
    owner_key: str
    request_id: str
    root_run_id: str
    user_message_ref: str
    user_message_hash: str
    source_ref: str
    source_hash: str
    proposal: StructuredGrowthProposalV1
    proposal_ref: str
    proposal_hash: str
    build_id: str
    evidence_set_hash: str
    reservation_key: str
    publish_policy: CapabilityBuildPublishPolicy = (
        CapabilityBuildPublishPolicy.CANDIDATE_ONLY
    )

    def __post_init__(self) -> None:
        for name in (
            "owner_key",
            "request_id",
            "root_run_id",
            "user_message_ref",
            "source_ref",
            "proposal_ref",
            "build_id",
            "reservation_key",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in (
            "user_message_hash",
            "source_hash",
            "proposal_hash",
            "evidence_set_hash",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        if self.proposal.proposal_hash != self.proposal_hash:
            raise GrowthBuildAdmissionError("proposal_hash_mismatch")
        if self.proposal.evidence_set_hash != self.evidence_set_hash:
            raise GrowthBuildAdmissionError("proposal_evidence_hash_mismatch")
        if self.proposal.decision.value != "candidate":
            raise GrowthBuildAdmissionError("explicit_build_requires_candidate_proposal")
        if self.publish_policy is not CapabilityBuildPublishPolicy.CANDIDATE_ONLY:
            raise GrowthBuildAdmissionError("governed_build_must_be_candidate_only")


@dataclass(frozen=True, slots=True)
class GrowthBuildAdmissionResultV1:
    publish_policy: CapabilityBuildPublishPolicy
    reason_code: str
    admission: ExplicitGrowthBuildAdmissionV1 | None = None
    durable_receipt: Mapping[str, JsonValue] | None = None


class GrowthBuildAdmissionStorePort(Protocol):
    """One Companion transaction must persist every fact in the admission."""

    def admit_explicit_build(
        self, admission: ExplicitGrowthBuildAdmissionV1
    ) -> Mapping[str, JsonValue]: ...


class GrowthBuildAdmissionRouter:
    def __init__(self, store: GrowthBuildAdmissionStorePort) -> None:
        self._store = store

    def admit(
        self,
        trusted: TrustedRootBuildRequestV1,
        *,
        proposal: StructuredGrowthProposalV1 | None,
    ) -> GrowthBuildAdmissionResultV1:
        if not isinstance(trusted, TrustedRootBuildRequestV1):
            raise GrowthBuildAdmissionError("trusted_build_request_required")
        governed_kind = any(
            item in {RequestedEntryKind.SKILL, RequestedEntryKind.WORKFLOW}
            for item in trusted.requested_entry_kinds
        )
        if not governed_kind:
            return GrowthBuildAdmissionResultV1(
                publish_policy=CapabilityBuildPublishPolicy.GENERAL_INSTALL,
                reason_code="general_tool_only_build",
            )
        if not trusted.explicit_capability_request:
            raise GrowthBuildAdmissionError("governance_admission_required")
        if proposal is None or proposal.target is None:
            raise GrowthBuildAdmissionError("structured_growth_proposal_required")
        if proposal.target.kind.value not in {
            item.value for item in trusted.requested_entry_kinds
        }:
            raise GrowthBuildAdmissionError("proposal_entry_kind_mismatch")

        source_ref = (
            f"explicit-user-build:{trusted.owner.profile_id}:"
            f"{trusted.owner.profile_generation}:{trusted.user_message_ref}"
        )
        source_hash = _hash(
            {
                "schema": "explicit-growth-build-source-v1",
                "owner_key": trusted.owner_key,
                "request_id": trusted.request_id,
                "root_run_id": trusted.root_run_id,
                "user_message_ref": trusted.user_message_ref,
                "user_message_hash": trusted.user_message_hash,
                "requested_entry_kinds": [
                    item.value for item in trusted.requested_entry_kinds
                ],
            }
        )
        proposal_ref = "proposal:" + _hash(
            {"source_ref": source_ref, "proposal_hash": proposal.proposal_hash}
        )
        reservation_key = "reservation:" + _hash(
            {
                "owner_key": trusted.owner_key,
                "kind": proposal.target.kind.value,
                "stable_name": proposal.target.stable_name,
                "pack_id": proposal.target.pack_id,
            }
        )
        build_id = "build:" + _hash(
            {
                "source_ref": source_ref,
                "proposal_ref": proposal_ref,
                "reservation_key": reservation_key,
            }
        )
        admission = ExplicitGrowthBuildAdmissionV1(
            owner=trusted.owner,
            owner_key=trusted.owner_key,
            request_id=trusted.request_id,
            root_run_id=trusted.root_run_id,
            user_message_ref=trusted.user_message_ref,
            user_message_hash=trusted.user_message_hash,
            source_ref=source_ref,
            source_hash=source_hash,
            proposal=proposal,
            proposal_ref=proposal_ref,
            proposal_hash=proposal.proposal_hash,
            build_id=build_id,
            evidence_set_hash=proposal.evidence_set_hash,
            reservation_key=reservation_key,
        )
        receipt = self._store.admit_explicit_build(admission)
        return GrowthBuildAdmissionResultV1(
            publish_policy=CapabilityBuildPublishPolicy.CANDIDATE_ONLY,
            reason_code="governed_growth_build_admitted",
            admission=admission,
            durable_receipt=receipt,
        )


@dataclass(frozen=True, slots=True)
class GrowthCandidateBuildPermitV1:
    permit_id: str
    build_id: str
    owner_key: str
    proposal_ref: str
    proposal_hash: str
    evidence_set_hash: str
    source_fence_hash: str
    target_fence_hash: str
    lease_epoch: int
    permit_hash: str
    _host_token: InitVar[object] = None

    def __post_init__(self, _host_token: object) -> None:
        if _host_token is not _HOST_ISSUER:
            raise GrowthBuildAdmissionError("growth_build_permit_host_only")
        for name in ("permit_id", "build_id", "owner_key", "proposal_ref"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in (
            "proposal_hash",
            "evidence_set_hash",
            "source_fence_hash",
            "target_fence_hash",
            "permit_hash",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        if isinstance(self.lease_epoch, bool) or self.lease_epoch < 1:
            raise ValueError("build_permit_lease_epoch_invalid")
        if self.permit_hash != _hash(self._payload()):
            raise GrowthBuildAdmissionError("growth_build_permit_hash_mismatch")

    def _payload(self) -> dict[str, object]:
        return {
            "schema": "growth-candidate-build-permit-v1",
            "permit_id": self.permit_id,
            "build_id": self.build_id,
            "owner_key": self.owner_key,
            "proposal_ref": self.proposal_ref,
            "proposal_hash": self.proposal_hash,
            "evidence_set_hash": self.evidence_set_hash,
            "source_fence_hash": self.source_fence_hash,
            "target_fence_hash": self.target_fence_hash,
            "lease_epoch": self.lease_epoch,
        }

    @classmethod
    def issue(
        cls,
        *,
        build_id: str,
        owner_key: str,
        proposal_ref: str,
        proposal_hash: str,
        evidence_set_hash: str,
        source_fence_hash: str,
        target_fence_hash: str,
        lease_epoch: int,
    ) -> "GrowthCandidateBuildPermitV1":
        permit_id = "permit:" + _hash(
            {
                "build_id": build_id,
                "owner_key": owner_key,
                "lease_epoch": lease_epoch,
            }
        )
        values = {
            "permit_id": permit_id,
            "build_id": build_id,
            "owner_key": owner_key,
            "proposal_ref": proposal_ref,
            "proposal_hash": proposal_hash,
            "evidence_set_hash": evidence_set_hash,
            "source_fence_hash": source_fence_hash,
            "target_fence_hash": target_fence_hash,
            "lease_epoch": lease_epoch,
        }
        permit_hash = _hash(
            {"schema": "growth-candidate-build-permit-v1", **values}
        )
        return cls(
            **values,
            permit_hash=permit_hash,
            _host_token=_HOST_ISSUER,
        )


@dataclass(frozen=True, slots=True)
class CandidateDraftReceiptV1:
    receipt_id: str
    builder_launch_id: str
    child_run_id: str
    child_start_hash: str
    proposal_ref: str
    proposal_hash: str
    evidence_set_hash: str
    target_fence_hash: str
    validated_draft_hash: str
    manifest_hash: str
    archive_hash: str
    file_set_hash: str
    effect_topology_hash: str
    receipt_hash: str
    _host_token: InitVar[object] = None

    def __post_init__(self, _host_token: object) -> None:
        if _host_token is not _HOST_ISSUER:
            raise GrowthBuildAdmissionError("candidate_draft_receipt_host_only")
        for name in (
            "receipt_id",
            "builder_launch_id",
            "child_run_id",
            "proposal_ref",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in (
            "child_start_hash",
            "proposal_hash",
            "evidence_set_hash",
            "target_fence_hash",
            "validated_draft_hash",
            "manifest_hash",
            "archive_hash",
            "file_set_hash",
            "effect_topology_hash",
            "receipt_hash",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))
        if self.receipt_hash != _hash(self._payload()):
            raise GrowthBuildAdmissionError("candidate_draft_receipt_hash_mismatch")

    def _payload(self) -> dict[str, str]:
        return {
            "schema": "candidate-draft-receipt-v1",
            "receipt_id": self.receipt_id,
            "builder_launch_id": self.builder_launch_id,
            "child_run_id": self.child_run_id,
            "child_start_hash": self.child_start_hash,
            "proposal_ref": self.proposal_ref,
            "proposal_hash": self.proposal_hash,
            "evidence_set_hash": self.evidence_set_hash,
            "target_fence_hash": self.target_fence_hash,
            "validated_draft_hash": self.validated_draft_hash,
            "manifest_hash": self.manifest_hash,
            "archive_hash": self.archive_hash,
            "file_set_hash": self.file_set_hash,
            "effect_topology_hash": self.effect_topology_hash,
        }

    @classmethod
    def from_authoritative_row(
        cls, row: Mapping[str, object]
    ) -> "CandidateDraftReceiptV1":
        fields = (
            "receipt_id",
            "builder_launch_id",
            "child_run_id",
            "child_start_hash",
            "proposal_ref",
            "proposal_hash",
            "evidence_set_hash",
            "target_fence_hash",
            "validated_draft_hash",
            "manifest_hash",
            "archive_hash",
            "file_set_hash",
            "effect_topology_hash",
            "receipt_hash",
        )
        if any(name not in row for name in fields):
            raise GrowthBuildAdmissionError("candidate_draft_receipt_row_incomplete")
        return cls(
            **{name: str(row[name]) for name in fields},
            _host_token=_HOST_ISSUER,
        )

    @classmethod
    def issue_from_host(
        cls,
        *,
        builder_launch_id: str,
        child_run_id: str,
        child_start_hash: str,
        proposal_ref: str,
        proposal_hash: str,
        evidence_set_hash: str,
        target_fence_hash: str,
        validated_draft_hash: str,
        manifest_hash: str,
        archive_hash: str,
        file_set_hash: str,
        effect_topology_hash: str,
    ) -> "CandidateDraftReceiptV1":
        receipt_id = "candidate-draft:" + _hash(
            {
                "builder_launch_id": builder_launch_id,
                "child_run_id": child_run_id,
                "child_start_hash": child_start_hash,
                "proposal_ref": proposal_ref,
                "proposal_hash": proposal_hash,
            }
        )
        values = {
            "receipt_id": receipt_id,
            "builder_launch_id": builder_launch_id,
            "child_run_id": child_run_id,
            "child_start_hash": child_start_hash,
            "proposal_ref": proposal_ref,
            "proposal_hash": proposal_hash,
            "evidence_set_hash": evidence_set_hash,
            "target_fence_hash": target_fence_hash,
            "validated_draft_hash": validated_draft_hash,
            "manifest_hash": manifest_hash,
            "archive_hash": archive_hash,
            "file_set_hash": file_set_hash,
            "effect_topology_hash": effect_topology_hash,
        }
        return cls(
            **values,
            receipt_hash=_hash(
                {"schema": "candidate-draft-receipt-v1", **values}
            ),
            _host_token=_HOST_ISSUER,
        )


@dataclass(frozen=True, slots=True)
class CandidateDraftReceiptExpectationV1:
    receipt_id: str
    receipt_hash: str
    builder_launch_id: str
    child_run_id: str
    child_start_hash: str
    proposal_ref: str
    proposal_hash: str
    evidence_set_hash: str
    target_fence_hash: str
    validated_draft_hash: str
    manifest_hash: str
    archive_hash: str
    file_set_hash: str
    effect_topology_hash: str

    def __post_init__(self) -> None:
        for name in (
            "receipt_id",
            "builder_launch_id",
            "child_run_id",
            "proposal_ref",
        ):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        for name in (
            "receipt_hash",
            "child_start_hash",
            "proposal_hash",
            "evidence_set_hash",
            "target_fence_hash",
            "validated_draft_hash",
            "manifest_hash",
            "archive_hash",
            "file_set_hash",
            "effect_topology_hash",
        ):
            object.__setattr__(self, name, _digest(getattr(self, name), name))

    def verify(self, receipt: CandidateDraftReceiptV1) -> None:
        for name in self.__dataclass_fields__:
            if getattr(receipt, name) != getattr(self, name):
                raise GrowthBuildAdmissionError(
                    f"candidate_draft_receipt_{name}_mismatch"
                )


class GrowthCandidateBuildPermitQueryPort(Protocol):
    def issue_for_claim(
        self, *, build_id: str, claim_owner: str, claim_epoch: int
    ) -> GrowthCandidateBuildPermitV1: ...


class CandidateDraftReceiptQueryPort(Protocol):
    def read_exact(
        self, expectation: CandidateDraftReceiptExpectationV1
    ) -> CandidateDraftReceiptV1: ...


class ReservedBuilderLaunchPort(Protocol):
    async def precreate_exact(
        self, permit: GrowthCandidateBuildPermitV1, *, task_workspace: str
    ) -> Mapping[str, JsonValue]: ...

    async def start_precreated(
        self, permit: GrowthCandidateBuildPermitV1
    ) -> Mapping[str, JsonValue]: ...

    async def read_exact(
        self, permit: GrowthCandidateBuildPermitV1
    ) -> Mapping[str, JsonValue] | None: ...


class CapabilityBuildOutputPort(Protocol):
    def handoff_candidate(
        self,
        *,
        permit: GrowthCandidateBuildPermitV1,
        receipt: CandidateDraftReceiptV1,
    ) -> Mapping[str, JsonValue]: ...


__all__ = [
    "CandidateDraftReceiptExpectationV1",
    "CandidateDraftReceiptQueryPort",
    "CandidateDraftReceiptV1",
    "CapabilityBuildOutputPort",
    "CapabilityBuildPublishPolicy",
    "ExplicitGrowthBuildAdmissionV1",
    "GrowthBuildAdmissionError",
    "GrowthBuildAdmissionResultV1",
    "GrowthBuildAdmissionRouter",
    "GrowthBuildAdmissionStorePort",
    "GrowthCandidateBuildPermitQueryPort",
    "GrowthCandidateBuildPermitV1",
    "RequestedEntryKind",
    "ReservedBuilderLaunchPort",
    "TrustedRootBuildRequestV1",
]
