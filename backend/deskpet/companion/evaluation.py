"""Fail-closed evaluation policy and execution-permit issuance.

The service deliberately has no activation dependency.  Its strongest output
is an ``EvaluationExecutionPermit``, which authorizes evaluation only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Mapping, Protocol, Sequence

from .build_admission import CandidateDraftReceiptV1
from .contracts import CandidatePackage, EvaluationExecutionPermit, OwnerRef
from .risk import (
    CapabilityRiskPolicy,
    CandidateRiskInputV1,
    EffectTopologyDiffV1,
    RiskAssessmentV1,
    StaticRiskPreflight,
    StaticRiskPreflightResultV1,
)
from .store import canonical_hash


@dataclass(frozen=True, slots=True)
class EvaluationIdentityV1:
    evaluation_id: str
    candidate_id: str
    package_hash: str
    manifest_hash: str
    archive_hash: str
    suite_hash: str
    runner_policy_hash: str
    issued_revocation_epoch: int
    risk_ref: str

    def __post_init__(self) -> None:
        if any(
            not value
            for value in (
                self.evaluation_id,
                self.candidate_id,
                self.package_hash,
                self.manifest_hash,
                self.archive_hash,
                self.suite_hash,
                self.runner_policy_hash,
                self.risk_ref,
            )
        ):
            raise ValueError("evaluation identity fields are required")
        if self.issued_revocation_epoch < 0:
            raise ValueError("issued_revocation_epoch must be non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "evaluation_id": self.evaluation_id,
            "candidate_id": self.candidate_id,
            "package_hash": self.package_hash,
            "manifest_hash": self.manifest_hash,
            "archive_hash": self.archive_hash,
            "suite_hash": self.suite_hash,
            "runner_policy_hash": self.runner_policy_hash,
            "issued_revocation_epoch": self.issued_revocation_epoch,
            "risk_ref": self.risk_ref,
        }


@dataclass(frozen=True, slots=True)
class EvaluationGateRequestV1:
    identity: EvaluationIdentityV1
    candidate: CandidateRiskInputV1
    topology_diff: EffectTopologyDiffV1
    authorization_id: str | None = None
    zero_tools: bool = False

    def __post_init__(self) -> None:
        if (
            self.identity.candidate_id != self.candidate.candidate_id
            or self.identity.package_hash != self.candidate.package_hash
        ):
            raise ValueError("evaluation and risk candidate identities disagree")
        if not isinstance(self.zero_tools, bool):
            raise ValueError("zero_tools must be boolean")


class EvaluationPermitStorePort(Protocol):
    def record_risk_assessment(
        self,
        owner: OwnerRef,
        *,
        risk_id: str,
        candidate_id: str,
        candidate_package_hash: str,
        static_preflight: Mapping[str, object],
        effect_topology_diff: Mapping[str, object],
        risk: str,
        risk_hash: str,
        reason_code: str,
    ) -> Mapping[str, object]: ...

    def issue_evaluation_execution_permit(
        self, owner: OwnerRef, permit: EvaluationExecutionPermit
    ) -> Mapping[str, object]: ...


class EvaluationAuthorizationStorePort(Protocol):
    def create_evaluation_authorization(
        self,
        owner: OwnerRef,
        *,
        authorization_id: str,
        nonce: str,
        evaluation_id: str,
        candidate_id: str,
        package_hash: str,
        suite_hash: str,
        runner_policy_hash: str,
        expires_at: str,
        actor: str,
        risk_ack: str,
        reason_code: str,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class EvaluationAuthorizationCommandV1:
    authorization_id: str
    nonce: str
    evaluation_id: str
    candidate_id: str
    package_hash: str
    suite_hash: str
    runner_policy_hash: str
    reason_code: str
    ttl_seconds: int = 300

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0 or self.ttl_seconds > 900:
            raise ValueError("evaluation authorization TTL is out of range")


class EvaluationAuthorizationService:
    """Create the exact user authorization consumed by one eval permit."""

    def __init__(
        self,
        store: EvaluationAuthorizationStorePort,
        *,
        clock=lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._clock = clock

    def authorize(
        self,
        owner: OwnerRef,
        command: EvaluationAuthorizationCommandV1,
    ) -> Mapping[str, object]:
        expires_at = (
            self._clock() + timedelta(seconds=command.ttl_seconds)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return self._store.create_evaluation_authorization(
            owner,
            authorization_id=command.authorization_id,
            nonce=command.nonce,
            evaluation_id=command.evaluation_id,
            candidate_id=command.candidate_id,
            package_hash=command.package_hash,
            suite_hash=command.suite_hash,
            runner_policy_hash=command.runner_policy_hash,
            expires_at=expires_at,
            actor="user",
            risk_ack="no_os_sandbox",
            reason_code=command.reason_code,
        )


@dataclass(frozen=True, slots=True)
class EvaluationGateOutcomeV1:
    status: Literal[
        "issued",
        "awaiting_eval_authorization",
    ]
    preflight: StaticRiskPreflightResultV1
    risk: RiskAssessmentV1
    permit: EvaluationExecutionPermit | None
    reason_code: str


class EvaluationPermitIssuer:
    """Issue only an evaluation-scoped permit after deterministic risk policy."""

    def __init__(
        self,
        store: EvaluationPermitStorePort,
        *,
        preflight: StaticRiskPreflight | None = None,
        risk_policy: CapabilityRiskPolicy | None = None,
    ) -> None:
        self._store = store
        self._preflight = preflight or StaticRiskPreflight()
        self._risk_policy = risk_policy or CapabilityRiskPolicy()

    def issue(
        self, owner: OwnerRef, request: EvaluationGateRequestV1
    ) -> EvaluationGateOutcomeV1:
        preflight = self._preflight.inspect(request.candidate)
        risk = self._risk_policy.assess(preflight, request.topology_diff)
        self._store.record_risk_assessment(
            owner,
            risk_id=request.identity.risk_ref,
            candidate_id=request.identity.candidate_id,
            candidate_package_hash=request.identity.package_hash,
            static_preflight={
                "schema_version": 1,
                "candidate_id": preflight.candidate_id,
                "package_hash": preflight.package_hash,
                "candidate_kind": preflight.candidate_kind,
                "safe_auto_eligible": preflight.safe_auto_eligible,
                "direct_os_effects_unverifiable":
                    preflight.direct_os_effects_unverifiable,
                "high_risk_signals": list(preflight.high_risk_signals),
                "unknown_signals": list(preflight.unknown_signals),
                "facts_hash": preflight.facts_hash,
                "preflight_hash": preflight.preflight_hash,
            },
            effect_topology_diff=request.topology_diff.to_dict(),
            risk=risk.risk,
            risk_hash=risk.risk_hash,
            reason_code=(
                "manifest_risk_preflight_low"
                if risk.risk == "low"
                else "manifest_risk_safe_static_evaluation"
                if risk.permit_mode == "safe_static"
                else "manifest_risk_requires_user_authorization"
            ),
        )
        if risk.permit_mode == "safe_static" and (
            request.candidate.candidate_kind != "instruction"
            or not request.zero_tools
        ):
            return EvaluationGateOutcomeV1(
                status="awaiting_eval_authorization",
                preflight=preflight,
                risk=risk,
                permit=None,
                reason_code="safe_static_zero_tool_runner_required",
            )
        if risk.permit_mode == "user_authorized" and not request.authorization_id:
            return EvaluationGateOutcomeV1(
                status="awaiting_eval_authorization",
                preflight=preflight,
                risk=risk,
                permit=None,
                reason_code="evaluation_authorization_required",
            )
        mode = risk.permit_mode
        authorization_id = (
            None
            if mode in {"safe_auto", "safe_static"}
            else request.authorization_id
        )
        facts = {
            "schema_version": 1,
            "evaluation_id": request.identity.evaluation_id,
            "mode": mode,
            "candidate_id": request.identity.candidate_id,
            "package_hash": request.identity.package_hash,
            "manifest_hash": request.identity.manifest_hash,
            "archive_hash": request.identity.archive_hash,
            "suite_hash": request.identity.suite_hash,
            "runner_policy_hash": request.identity.runner_policy_hash,
            "issued_revocation_epoch": (
                request.identity.issued_revocation_epoch
            ),
            "preflight_ref": f"preflight:{preflight.preflight_hash}",
            "preflight_hash": preflight.preflight_hash,
            "risk_ref": request.identity.risk_ref,
            "risk_hash": risk.risk_hash,
            "authorization_id": authorization_id,
        }
        permit_hash = canonical_hash(facts)
        permit = EvaluationExecutionPermit(
            permit_id=f"evaluation-permit:{permit_hash}",
            evaluation_id=request.identity.evaluation_id,
            mode=mode,
            candidate_id=request.identity.candidate_id,
            package_hash=request.identity.package_hash,
            manifest_hash=request.identity.manifest_hash,
            archive_hash=request.identity.archive_hash,
            suite_hash=request.identity.suite_hash,
            runner_policy_hash=request.identity.runner_policy_hash,
            issued_revocation_epoch=request.identity.issued_revocation_epoch,
            preflight_ref=str(facts["preflight_ref"]),
            preflight_hash=preflight.preflight_hash,
            risk_ref=request.identity.risk_ref,
            risk_hash=risk.risk_hash,
            permit_hash=permit_hash,
            reason_code=(
                "safe_auto_manifest_proven"
                if mode == "safe_auto"
                else "safe_static_zero_tool_evaluation"
                if mode == "safe_static"
                else "exact_evaluation_authorization"
            ),
            authorization_id=authorization_id,
        )
        self._store.issue_evaluation_execution_permit(owner, permit)
        return EvaluationGateOutcomeV1(
            status="issued",
            preflight=preflight,
            risk=risk,
            permit=permit,
            reason_code=permit.reason_code,
        )


class ImmutableEvaluationGate:
    """Build risk input only from verified candidate bytes and host receipt."""

    def __init__(
        self,
        issuer: EvaluationPermitIssuer,
        *,
        facts_builder=None,
    ) -> None:
        if facts_builder is None:
            from .risk_facts import ImmutableCandidateRiskFactsBuilder

            facts_builder = ImmutableCandidateRiskFactsBuilder()
        self._issuer = issuer
        self._facts_builder = facts_builder

    def issue(
        self,
        owner: OwnerRef,
        *,
        identity: EvaluationIdentityV1,
        package: CandidatePackage,
        receipt: CandidateDraftReceiptV1,
        effect_topology: Mapping[str, object],
        tool_manifest: Mapping[str, Mapping[str, object]],
        topology_diff: EffectTopologyDiffV1,
        source_tool_refs: Sequence[str] = (),
        authorization_id: str | None = None,
        zero_tools: bool = False,
    ) -> EvaluationGateOutcomeV1:
        facts = self._facts_builder.build(
            package,
            receipt,
            candidate_id=identity.candidate_id,
            effect_topology=effect_topology,
            tool_manifest=tool_manifest,
            source_tool_refs=source_tool_refs,
            permissions_added=topology_diff.permissions_added,
            topology_expanded=topology_diff.topology_expanded,
        )
        if (
            facts.candidate.candidate_id != identity.candidate_id
            or facts.candidate.package_hash != identity.package_hash
            or facts.manifest_hash != identity.manifest_hash
            or facts.archive_hash != identity.archive_hash
        ):
            raise ValueError("immutable evaluation identity mismatch")
        return self._issuer.issue(
            owner,
            EvaluationGateRequestV1(
                identity=identity,
                candidate=facts.candidate,
                topology_diff=topology_diff,
                authorization_id=authorization_id,
                zero_tools=zero_tools,
            ),
        )


@dataclass(frozen=True, slots=True)
class EvaluationCaseComparisonV1:
    case_id: str
    old_status: str
    candidate_status: str
    improved: bool
    baseline_regression: bool


@dataclass(frozen=True, slots=True)
class EvaluationVerdictV1:
    verdict: Literal["passed", "failed", "inconclusive"]
    reason_code: str
    required_case_count: int
    committed_case_count: int


class IndependentEvaluator:
    """Reduce pairwise results without treating missing evidence as success."""

    def evaluate(
        self,
        *,
        required_case_ids: Sequence[str],
        comparisons: Sequence[EvaluationCaseComparisonV1],
    ) -> EvaluationVerdictV1:
        required = tuple(sorted(set(required_case_ids)))
        by_id = {item.case_id: item for item in comparisons}
        if not required or len(by_id) != len(comparisons):
            return EvaluationVerdictV1(
                "inconclusive", "required_case_manifest_invalid", len(required), 0
            )
        if any(case_id not in by_id for case_id in required):
            return EvaluationVerdictV1(
                "inconclusive",
                "required_case_missing",
                len(required),
                len(set(required) & by_id.keys()),
            )
        selected = tuple(by_id[case_id] for case_id in required)
        terminal = {"passed", "failed"}
        if any(
            item.old_status not in terminal
            or item.candidate_status not in terminal
            for item in selected
        ):
            return EvaluationVerdictV1(
                "inconclusive",
                "required_case_unknown_or_incomplete",
                len(required),
                sum(
                    item.old_status in terminal
                    and item.candidate_status in terminal
                    for item in selected
                ),
            )
        if any(
            item.baseline_regression or item.candidate_status == "failed"
            for item in selected
        ):
            return EvaluationVerdictV1(
                "failed", "baseline_regression_or_candidate_failure", len(required), len(selected)
            )
        if not any(item.improved for item in selected):
            return EvaluationVerdictV1(
                "inconclusive", "target_not_improved", len(required), len(selected)
            )
        return EvaluationVerdictV1(
            "passed", "all_required_cases_passed", len(required), len(selected)
        )


__all__ = [
    "EvaluationCaseComparisonV1",
    "EvaluationAuthorizationCommandV1",
    "EvaluationAuthorizationService",
    "EvaluationAuthorizationStorePort",
    "EvaluationGateOutcomeV1",
    "EvaluationGateRequestV1",
    "EvaluationIdentityV1",
    "ImmutableEvaluationGate",
    "EvaluationPermitIssuer",
    "EvaluationPermitStorePort",
    "EvaluationVerdictV1",
    "IndependentEvaluator",
]
