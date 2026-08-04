from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from deskpet.companion.contracts import CandidateMode, OwnerRef
from deskpet.companion.growth import (
    CandidateBindingFenceV1,
    EvidenceClusterer,
    GrowthEvidenceV1,
    GrowthProposalDecision,
    GrowthReflector,
    GrowthTargetIdentityV1,
    StructuredGrowthProposalV1,
    evidence_set_hash,
)


OWNER = OwnerRef("profile-a", 3)
OWNER_KEY = "companion:profile-a:3"
MANIFEST_HASH = "a" * 64
NOW = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)


def evidence(
    event_id: str,
    *,
    root: str,
    intent: str = "Only keep the conclusion",
    at: datetime = NOW,
    retry_of: str | None = None,
    previous_run: str | None = None,
) -> GrowthEvidenceV1:
    return GrowthEvidenceV1(
        owner=OWNER,
        event_id=event_id,
        capability_id="daily-three-pack",
        root_run_id=root,
        intent=intent,
        occurred_at=at,
        source_kind="message_ingress",
        source_ref=f"message:{event_id}",
        retry_of_event_id=retry_of,
        previous_run_id=previous_run,
        current_run_id=root if previous_run else None,
    )


def genesis_proposal(*event_ids: str) -> StructuredGrowthProposalV1:
    ids = tuple(sorted(event_ids))
    return StructuredGrowthProposalV1(
        decision="candidate",
        evidence_event_ids=ids,
        evidence_set_hash=evidence_set_hash(ids),
        target=GrowthTargetIdentityV1(
            kind="skill",
            target_id="daily-three",
            stable_name="daily-three",
            pack_id="daily-three-pack",
        ),
        candidate_mode=CandidateMode.GENESIS,
        target_fence=CandidateBindingFenceV1(
            owner_key=OWNER_KEY,
            scope="user",
            scope_key="profile-a",
            pack_id="daily-three-pack",
            expected_absent=True,
            binding_generation=0,
        ),
        hypothesis="A shorter daily plan will be easier to act on.",
        structured_diff={"instructions": {"add": ["keep three priorities"]}},
        expected_improvement="The output contains exactly three actionable priorities.",
        risk_hints=("instruction_only",),
        evaluation_plan=({"case_id": "three-priorities"},),
    )


def test_clusterer_counts_independent_context_not_repeated_text() -> None:
    clusterer = EvidenceClusterer(time_window=timedelta(hours=24))
    values = (
        evidence("event-1", root="run-a"),
        evidence("event-2", root="run-a", intent="  only KEEP the conclusion  "),
        evidence("event-3", root="run-b"),
    )

    clusters = clusterer.cluster(values)

    assert len(clusters) == 2
    assert sorted(cluster.evidence_count for cluster in clusters) == [1, 2]


def test_explicit_retry_links_fold_different_root_and_window() -> None:
    clusterer = EvidenceClusterer(time_window=timedelta(hours=1))
    values = (
        evidence("event-1", root="run-a"),
        evidence(
            "event-2",
            root="run-b",
            at=NOW + timedelta(days=2),
            retry_of="event-1",
            previous_run="run-a",
        ),
    )

    [cluster] = clusterer.cluster(values)

    assert cluster.event_ids == ("event-1", "event-2")
    assert cluster.root_run_ids == ("run-a", "run-b")


def test_contexts_never_cross_capability_boundary() -> None:
    first = evidence("event-1", root="run-a")
    second = replace(
        evidence("event-2", root="run-a", retry_of="event-1"),
        capability_id="another-pack",
    )

    clusters = EvidenceClusterer().cluster((first, second))

    assert len(clusters) == 2


def test_reflector_abstains_on_one_implicit_context() -> None:
    item = evidence("event-1", root="run-a")
    result = GrowthReflector().review(
        genesis_proposal(item.event_id),
        evidence=(item,),
    )

    assert result.decision is GrowthProposalDecision.ABSTAIN
    assert result.abstain_reason == "insufficient_independent_evidence"
    assert result.target is None
    assert result.candidate_mode is None
    assert result.structured_diff == {}
    assert result.evaluation_plan == ()


def test_model_self_assessment_without_evidence_cannot_become_candidate() -> None:
    payload = genesis_proposal("event-1").to_dict()
    payload["evidence_event_ids"] = []
    payload["evidence_set_hash"] = evidence_set_hash(())
    payload["hypothesis"] = "The user may prefer every answer to be more lively."

    with pytest.raises(ValueError, match="candidate_proposal_requires_evidence"):
        GrowthReflector().review(payload, evidence=())


def test_reflector_accepts_two_independent_contexts_and_explicit_single_signal() -> None:
    first = evidence("event-1", root="run-a")
    second = evidence("event-2", root="run-b")
    reflector = GrowthReflector()

    accepted = reflector.review(
        genesis_proposal(first.event_id, second.event_id),
        evidence=(second, first),
    )
    explicit = reflector.review(
        genesis_proposal(first.event_id),
        evidence=(first,),
        explicit_user_signal=True,
    )

    assert accepted.decision is GrowthProposalDecision.CANDIDATE
    assert explicit.decision is GrowthProposalDecision.CANDIDATE


def test_proposal_schema_is_closed_and_round_trips() -> None:
    original = genesis_proposal("event-1")
    payload = original.to_dict()

    assert StructuredGrowthProposalV1.from_mapping(payload) == original
    payload["model_claimed_installed"] = True
    with pytest.raises(ValueError, match="growth_proposal_fields_invalid"):
        StructuredGrowthProposalV1.from_mapping(payload)


def test_candidate_modes_enforce_exact_source_and_target_fences() -> None:
    source = CandidateBindingFenceV1(
        owner_key=OWNER_KEY,
        scope="user",
        scope_key="profile-a",
        pack_id="daily-three-pack",
        expected_absent=False,
        binding_generation=4,
        version="1.0.0",
        manifest_hash=MANIFEST_HASH,
    )
    stale_target = CandidateBindingFenceV1(
        owner_key=OWNER_KEY,
        scope="user",
        scope_key="profile-a",
        pack_id="daily-three-pack",
        expected_absent=False,
        binding_generation=5,
        version="1.0.0",
        manifest_hash=MANIFEST_HASH,
    )
    base = genesis_proposal("event-1")

    with pytest.raises(ValueError, match="update_source_target_fence_mismatch"):
        StructuredGrowthProposalV1(
            decision="candidate",
            evidence_event_ids=base.evidence_event_ids,
            evidence_set_hash=base.evidence_set_hash,
            target=base.target,
            candidate_mode="update",
            source_fence=source,
            target_fence=stale_target,
            hypothesis=base.hypothesis,
            structured_diff=base.structured_diff,
            expected_improvement=base.expected_improvement,
            risk_hints=base.risk_hints,
            evaluation_plan=base.evaluation_plan,
        )


def test_custom_genesis_cannot_masquerade_as_builtin_override() -> None:
    base = genesis_proposal("event-1")
    custom_source = CandidateBindingFenceV1(
        owner_key=OWNER_KEY,
        scope="user",
        scope_key="profile-a",
        pack_id="daily-three-pack",
        expected_absent=False,
        binding_generation=4,
        version="1.0.0",
        manifest_hash=MANIFEST_HASH,
    )

    with pytest.raises(ValueError, match="builtin_override_fence_invalid"):
        StructuredGrowthProposalV1(
            decision="candidate",
            evidence_event_ids=base.evidence_event_ids,
            evidence_set_hash=base.evidence_set_hash,
            target=base.target,
            candidate_mode="builtin_override",
            source_fence=custom_source,
            target_fence=base.target_fence,
            hypothesis=base.hypothesis,
            structured_diff=base.structured_diff,
            expected_improvement=base.expected_improvement,
            risk_hints=base.risk_hints,
            evaluation_plan=base.evaluation_plan,
        )


def test_abstain_cannot_smuggle_mutation_fields() -> None:
    empty_hash = evidence_set_hash(())
    abstain = StructuredGrowthProposalV1.abstain(
        evidence_event_ids=(),
        evidence_set_hash=empty_hash,
        reason="insufficient_evidence",
    )
    assert abstain.decision is GrowthProposalDecision.ABSTAIN

    with pytest.raises(ValueError, match="abstain_cannot_carry_mutation"):
        StructuredGrowthProposalV1(
            decision="abstain",
            evidence_event_ids=(),
            evidence_set_hash=empty_hash,
            target=genesis_proposal("event-1").target,
            abstain_reason="insufficient_evidence",
        )
