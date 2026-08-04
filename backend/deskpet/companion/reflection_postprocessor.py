"""Host-only admission of structured results from reflection Runs.

The model proposes a closed-schema growth decision.  This module reloads the
live evidence and binding facts from host-owned ports, validates the proposal
against those current facts, and only then asks ``CompanionStore`` to admit the
reflection decision.  It has no Builder, evaluator, activation, chat, or TTS
authority.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias

from .contracts import CandidateMode, OwnerRef
from .growth import (
    CandidateBindingFenceV1,
    GrowthEvidenceV1,
    GrowthProposalDecision,
    GrowthReflector,
    GrowthTargetIdentityV1,
    StructuredGrowthProposalV1,
    evidence_set_hash,
)
from .run_adapter import BackgroundRunCanonicalResultV1


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class LiveReflectionEvidenceV1:
    """Exact live evidence selected by the host for one reflection job."""

    evidence: tuple[GrowthEvidenceV1, ...]
    explicit_user_signal: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        if not isinstance(self.explicit_user_signal, bool):
            raise ValueError("explicit_user_signal_bool_required")
        if self.schema_version != 1:
            raise ValueError("live_reflection_evidence_schema_unsupported")


@dataclass(frozen=True, slots=True)
class LiveGrowthTargetFactsV1:
    """Current host-owned identity and binding fences for a proposed target."""

    target: GrowthTargetIdentityV1
    candidate_mode: CandidateMode | str
    source_fence: CandidateBindingFenceV1 | None
    target_fence: CandidateBindingFenceV1
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_mode", CandidateMode(self.candidate_mode)
        )
        if self.schema_version != 1:
            raise ValueError("live_growth_target_facts_schema_unsupported")
        if self.target_fence.pack_id != self.target.pack_id:
            raise ValueError("live_target_fence_pack_mismatch")
        if (
            self.source_fence is not None
            and self.source_fence.pack_id != self.target.pack_id
        ):
            raise ValueError("live_source_fence_pack_mismatch")


@dataclass(frozen=True, slots=True)
class HostNeutralSemanticGrowthResultV1:
    """Model-authored semantics with no owner, generation, or fence authority."""

    decision: str
    target_kind: str
    stable_name: str | None
    hypothesis: str | None
    requested_change: str | None
    risk_hints: tuple[str, ...]
    expected_improvement: str | None = None
    evaluation_plan: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, object],
    ) -> "HostNeutralSemanticGrowthResultV1":
        required = {
            "decision",
            "target_kind",
            "stable_name",
            "hypothesis",
            "requested_change",
            "risk_hints",
        }
        optional = {"expected_improvement", "evaluation_plan"}
        if not required.issubset(raw) or set(raw) - required - optional:
            raise ValueError("semantic_growth_result_fields_invalid")
        decision = str(raw["decision"])
        target_kind = str(raw["target_kind"])
        if decision not in {"propose", "abstain"}:
            raise ValueError("semantic_growth_decision_invalid")
        if target_kind not in {"skill", "workflow", "none"}:
            raise ValueError("semantic_growth_target_kind_invalid")
        risk_hints = raw["risk_hints"]
        if not isinstance(risk_hints, list) or not all(
            isinstance(item, str) and item.strip() for item in risk_hints
        ):
            raise ValueError("semantic_growth_risk_hints_invalid")
        evaluation_plan = raw.get("evaluation_plan", [])
        if not isinstance(evaluation_plan, list) or not all(
            isinstance(item, Mapping) for item in evaluation_plan
        ):
            raise ValueError("semantic_growth_evaluation_plan_invalid")
        result = cls(
            decision=decision,
            target_kind=target_kind,
            stable_name=_optional_semantic_text(raw["stable_name"]),
            hypothesis=_optional_semantic_text(raw["hypothesis"]),
            requested_change=_optional_semantic_text(raw["requested_change"]),
            risk_hints=tuple(str(item).strip() for item in risk_hints),
            expected_improvement=_optional_semantic_text(
                raw.get("expected_improvement")
            ),
            evaluation_plan=tuple(dict(item) for item in evaluation_plan),
        )
        result.validate()
        return result

    def validate(self) -> None:
        if self.decision == "abstain":
            if (
                self.target_kind != "none"
                or self.stable_name is not None
                or self.hypothesis is not None
                or self.requested_change is not None
                or self.risk_hints
                or self.expected_improvement is not None
                or self.evaluation_plan
            ):
                raise ValueError("semantic_abstain_cannot_carry_mutation")
            return
        if (
            self.target_kind not in {"skill", "workflow"}
            or self.stable_name is None
            or self.hypothesis is None
            or self.requested_change is None
        ):
            raise ValueError("semantic_candidate_fields_incomplete")


LiveEvidenceLoader: TypeAlias = Callable[
    [OwnerRef, tuple[str, ...]],
    LiveReflectionEvidenceV1 | Awaitable[LiveReflectionEvidenceV1],
]
LiveTargetFactsResolver: TypeAlias = Callable[
    [
        OwnerRef,
        GrowthTargetIdentityV1 | str,
        CandidateMode | str,
    ],
    LiveGrowthTargetFactsV1 | Awaitable[LiveGrowthTargetFactsV1],
]


class ReflectionDecisionStorePort(Protocol):
    def admit_reflection_decision(
        self,
        owner: OwnerRef,
        *,
        job_id: str,
        decision_id: str,
        decision: str,
        reason_code: str,
        evidence_event_ids: tuple[str, ...],
        source_ref: str,
        proposal_ref: str | None = None,
        proposal: Any | None = None,
        build_id: str | None = None,
    ) -> Mapping[str, Any]: ...


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class ReflectionResultPostprocessor:
    """Validate and durably admit a zero-tool reflection Run result."""

    def __init__(
        self,
        *,
        store: ReflectionDecisionStorePort,
        evidence_loader: LiveEvidenceLoader,
        target_facts_resolver: LiveTargetFactsResolver,
        reflector: GrowthReflector | None = None,
    ) -> None:
        self._store = store
        self._evidence_loader = evidence_loader
        self._target_facts_resolver = target_facts_resolver
        self._reflector = reflector or GrowthReflector()

    async def __call__(
        self, result: BackgroundRunCanonicalResultV1
    ) -> Mapping[str, Any]:
        if result.purpose != "reflection":
            raise ValueError("reflection_postprocessor_purpose_invalid")
        if result.status != "succeeded":
            raise ValueError("reflection_postprocessor_terminal_invalid")
        if result.structured_result is None:
            raise ValueError("structured_growth_proposal_required")

        raw_result = result.structured_result
        full_schema = "schema_id" in raw_result
        semantic = (
            None
            if full_schema
            else HostNeutralSemanticGrowthResultV1.from_mapping(raw_result)
        )
        proposed = (
            StructuredGrowthProposalV1.from_mapping(raw_result)
            if full_schema
            else None
        )
        evidence_bundle = await _resolve(
            self._evidence_loader(result.owner, result.evidence_ids)
        )
        if not isinstance(evidence_bundle, LiveReflectionEvidenceV1):
            raise TypeError("live_reflection_evidence_required")
        evidence = evidence_bundle.evidence
        actual_ids = tuple(sorted(item.event_id for item in evidence))
        expected_ids = tuple(sorted(result.evidence_ids))
        if (
            len(actual_ids) != len(set(actual_ids))
            or actual_ids != expected_ids
            or any(item.owner != result.owner for item in evidence)
        ):
            raise ValueError("live_reflection_evidence_mismatch")

        semantic_live_facts: LiveGrowthTargetFactsV1 | None = None
        if semantic is not None:
            proposed, semantic_live_facts = (
                await self._proposal_from_semantic_result(
                    result,
                    semantic=semantic,
                    evidence_event_ids=expected_ids,
                )
            )
        assert proposed is not None
        reviewed = self._reflector.review(
            proposed,
            evidence=evidence,
            explicit_user_signal=evidence_bundle.explicit_user_signal,
        )
        decision_id = "reflection-decision:" + _canonical_hash(
            {
                "schema": "reflection-result-admission-v1",
                "owner": {
                    "profile_id": result.owner.profile_id,
                    "profile_generation": result.owner.profile_generation,
                },
                "job_id": result.job_id,
                "result_ref": result.result_ref,
                "result_hash": result.result_hash,
            }
        )
        proposal_ref = (
            f"{result.result_ref}:proposal:{reviewed.proposal_hash}"
        )

        if reviewed.decision is GrowthProposalDecision.ABSTAIN:
            reason_code = (
                "reflection_insufficient_independent_evidence"
                if proposed.decision is GrowthProposalDecision.CANDIDATE
                else "reflection_model_abstained"
            )
            return self._store.admit_reflection_decision(
                result.owner,
                job_id=result.job_id,
                decision_id=decision_id,
                decision="abstain",
                reason_code=reason_code,
                evidence_event_ids=expected_ids,
                source_ref=result.result_ref,
                proposal_ref=proposal_ref,
                proposal=reviewed,
            )

        assert reviewed.target is not None
        mode = CandidateMode(reviewed.candidate_mode)
        live_facts = semantic_live_facts
        if live_facts is None:
            live_facts = await _resolve(
                self._target_facts_resolver(
                    result.owner,
                    reviewed.target,
                    mode,
                )
            )
        if not isinstance(live_facts, LiveGrowthTargetFactsV1):
            raise TypeError("live_growth_target_facts_required")
        expected_owner_key = (
            f"companion:{result.owner.profile_id}:"
            f"{result.owner.profile_generation}"
        )
        host_facts_match = (
            live_facts.target == reviewed.target
            and live_facts.candidate_mode == mode
            and live_facts.source_fence == reviewed.source_fence
            and live_facts.target_fence == reviewed.target_fence
            and live_facts.target_fence.owner_key == expected_owner_key
            and live_facts.target_fence.scope == "user"
            and live_facts.target_fence.scope_key == result.owner.profile_id
        )
        if not host_facts_match:
            return self._store.admit_reflection_decision(
                result.owner,
                job_id=result.job_id,
                decision_id=decision_id,
                decision="stale",
                reason_code="reflection_live_target_fence_mismatch",
                evidence_event_ids=expected_ids,
                source_ref=result.result_ref,
            )

        return self._store.admit_reflection_decision(
            result.owner,
            job_id=result.job_id,
            decision_id=decision_id,
            decision="candidate",
            reason_code="reflection_candidate_host_validated",
            evidence_event_ids=expected_ids,
            source_ref=result.result_ref,
            proposal_ref=proposal_ref,
            proposal=reviewed,
        )

    async def _proposal_from_semantic_result(
        self,
        result: BackgroundRunCanonicalResultV1,
        *,
        semantic: HostNeutralSemanticGrowthResultV1,
        evidence_event_ids: tuple[str, ...],
    ) -> tuple[
        StructuredGrowthProposalV1,
        LiveGrowthTargetFactsV1 | None,
    ]:
        evidence_hash = evidence_set_hash(evidence_event_ids)
        if semantic.decision == "abstain":
            return (
                StructuredGrowthProposalV1.abstain(
                    evidence_event_ids=evidence_event_ids,
                    evidence_set_hash=evidence_hash,
                    reason="model_abstained",
                ),
                None,
            )
        assert semantic.stable_name is not None
        live_facts = await _resolve(
            self._target_facts_resolver(
                result.owner,
                semantic.target_kind,
                semantic.stable_name,
            )
        )
        if not isinstance(live_facts, LiveGrowthTargetFactsV1):
            raise TypeError("live_growth_target_facts_required")
        if (
            live_facts.target.kind != semantic.target_kind
            or live_facts.target.stable_name != semantic.stable_name
        ):
            raise ValueError("semantic_growth_target_resolution_mismatch")
        assert semantic.hypothesis is not None
        assert semantic.requested_change is not None
        evaluation_plan = semantic.evaluation_plan or (
            {
                "case_id": "semantic-requested-change:"
                + _canonical_hash(
                    {
                        "target_kind": semantic.target_kind,
                        "stable_name": semantic.stable_name,
                        "requested_change": semantic.requested_change,
                    }
                )[:24],
                "assertion": "requested_change_observed",
            },
        )
        return (
            StructuredGrowthProposalV1(
                decision=GrowthProposalDecision.CANDIDATE,
                evidence_event_ids=evidence_event_ids,
                evidence_set_hash=evidence_hash,
                target=live_facts.target,
                candidate_mode=live_facts.candidate_mode,
                source_fence=live_facts.source_fence,
                target_fence=live_facts.target_fence,
                hypothesis=semantic.hypothesis,
                structured_diff={
                    "requested_change": semantic.requested_change
                },
                expected_improvement=(
                    semantic.expected_improvement or semantic.hypothesis
                ),
                risk_hints=semantic.risk_hints,
                evaluation_plan=evaluation_plan,
            ),
            live_facts,
        )


def _optional_semantic_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("semantic_growth_text_invalid")
    return value.strip()


__all__ = [
    "LiveEvidenceLoader",
    "LiveGrowthTargetFactsV1",
    "LiveReflectionEvidenceV1",
    "LiveTargetFactsResolver",
    "HostNeutralSemanticGrowthResultV1",
    "ReflectionDecisionStorePort",
    "ReflectionResultPostprocessor",
]
