from __future__ import annotations

from datetime import UTC, datetime

import pytest

from deskpet.companion.contracts import OwnerRef
from deskpet.companion.growth import (
    CandidateBindingFenceV1,
    GrowthEvidenceV1,
    GrowthTargetIdentityV1,
    StructuredGrowthProposalV1,
    evidence_set_hash,
)
from deskpet.companion.reflection_postprocessor import (
    LiveGrowthTargetFactsV1,
    LiveReflectionEvidenceV1,
    ReflectionResultPostprocessor,
)
from deskpet.companion.run_adapter import BackgroundRunCanonicalResultV1


OWNER = OwnerRef("profile-a", 3)
OWNER_KEY = "companion:profile-a:3"
EVENT_ID = "event-1"


class FakeStore:
    def __init__(self) -> None:
        self.calls = []

    def admit_reflection_decision(self, owner, **kwargs):
        self.calls.append((owner, kwargs))
        return {"decision": kwargs, "candidate_build": None}


def _evidence(event_id: str = EVENT_ID) -> GrowthEvidenceV1:
    return GrowthEvidenceV1(
        owner=OWNER,
        event_id=event_id,
        capability_id="core.summarize-day",
        root_run_id="run-1",
        intent="shorter actionable summary",
        occurred_at=datetime(2026, 7, 25, tzinfo=UTC),
        source_kind="message_ingress",
        source_ref="message:1",
    )


def _proposal() -> StructuredGrowthProposalV1:
    source = CandidateBindingFenceV1(
        owner_key="builtin",
        scope="builtin",
        scope_key="global",
        pack_id="core.summarize-day",
        expected_absent=False,
        binding_generation=4,
        version="1.0.0",
        manifest_hash="a" * 64,
    )
    target = CandidateBindingFenceV1(
        owner_key=OWNER_KEY,
        scope="user",
        scope_key=OWNER.profile_id,
        pack_id="core.summarize-day",
        expected_absent=True,
        binding_generation=0,
    )
    return StructuredGrowthProposalV1(
        decision="candidate",
        evidence_event_ids=(EVENT_ID,),
        evidence_set_hash=evidence_set_hash((EVENT_ID,)),
        target=GrowthTargetIdentityV1(
            kind="skill",
            target_id="summarize-day",
            stable_name="summarize-day",
            pack_id="core.summarize-day",
        ),
        candidate_mode="builtin_override",
        source_fence=source,
        target_fence=target,
        hypothesis="A shorter summary is easier to act on.",
        structured_diff={"instructions": {"replace": ["summary contract"]}},
        expected_improvement="The summary follows the corrected format.",
        risk_hints=("instruction_only",),
        evaluation_plan=({"case_id": "daily-summary-contract"},),
    )


def _result(
    proposal: dict | None = None,
    *,
    evidence_ids: tuple[str, ...] = (EVENT_ID,),
) -> BackgroundRunCanonicalResultV1:
    return BackgroundRunCanonicalResultV1(
        owner=OWNER,
        job_id="reflection-job-1",
        claim_owner="runtime-1",
        claim_epoch=2,
        purpose="reflection",
        evidence_ids=evidence_ids,
        execution_run_id="run-bg-1",
        execution_session_id="session-bg-1",
        status="succeeded",
        result_ref="companion-background:reflection-job-1:run-bg-1",
        result_hash="b" * 64,
        text="{}",
        structured_result=proposal or _proposal().to_dict(),
        terminal_payload={"text": "{}"},
        artifact_refs=(),
    )


def _live_facts(
    proposal: StructuredGrowthProposalV1 | None = None,
) -> LiveGrowthTargetFactsV1:
    proposal = proposal or _proposal()
    assert proposal.target is not None
    assert proposal.target_fence is not None
    return LiveGrowthTargetFactsV1(
        target=proposal.target,
        candidate_mode=proposal.candidate_mode,
        source_fence=proposal.source_fence,
        target_fence=proposal.target_fence,
    )


@pytest.mark.asyncio
async def test_exact_model_proposal_is_admitted_with_host_live_facts() -> None:
    store = FakeStore()
    target_calls = []

    async def load_evidence(owner, event_ids):
        assert owner == OWNER
        assert event_ids == (EVENT_ID,)
        return LiveReflectionEvidenceV1(
            evidence=(_evidence(),),
            explicit_user_signal=True,
        )

    def resolve_target(owner, proposed_target, proposed_mode):
        target_calls.append((owner, proposed_target, proposed_mode))
        return _live_facts()

    postprocessor = ReflectionResultPostprocessor(
        store=store,
        evidence_loader=load_evidence,
        target_facts_resolver=resolve_target,
    )

    await postprocessor(_result())
    await postprocessor(_result())

    assert len(target_calls) == 2
    assert len(store.calls) == 2
    assert store.calls[0] == store.calls[1]
    owner, admitted = store.calls[0]
    assert owner == OWNER
    assert admitted["decision"] == "candidate"
    assert admitted["reason_code"] == "reflection_candidate_host_validated"
    assert admitted["evidence_event_ids"] == (EVENT_ID,)
    assert admitted["source_ref"] == (
        "companion-background:reflection-job-1:run-bg-1"
    )
    assert isinstance(admitted["proposal"], StructuredGrowthProposalV1)
    assert admitted["proposal"].to_dict() == _proposal().to_dict()
    assert admitted["proposal_ref"].endswith(
        admitted["proposal"].proposal_hash
    )


@pytest.mark.asyncio
async def test_closed_schema_is_enforced_before_host_fact_loads() -> None:
    store = FakeStore()
    calls = []
    malformed = _proposal().to_dict()
    malformed["model_claimed_authority"] = True

    def should_not_load(*args):
        calls.append(args)
        raise AssertionError("host loaders must not run for malformed output")

    postprocessor = ReflectionResultPostprocessor(
        store=store,
        evidence_loader=should_not_load,
        target_facts_resolver=should_not_load,
    )

    with pytest.raises(ValueError, match="growth_proposal_fields_invalid"):
        await postprocessor(_result(malformed))

    assert calls == []
    assert store.calls == []


@pytest.mark.asyncio
async def test_live_evidence_must_match_host_prepared_evidence_ids() -> None:
    store = FakeStore()
    postprocessor = ReflectionResultPostprocessor(
        store=store,
        evidence_loader=lambda owner, event_ids: LiveReflectionEvidenceV1(
            evidence=(_evidence("different-event"),),
            explicit_user_signal=True,
        ),
        target_facts_resolver=lambda *args: _live_facts(),
    )

    with pytest.raises(ValueError, match="live_reflection_evidence_mismatch"):
        await postprocessor(_result())

    assert store.calls == []


@pytest.mark.asyncio
async def test_live_fence_mismatch_admits_stale_without_candidate() -> None:
    store = FakeStore()
    proposal = _proposal()
    assert proposal.target is not None
    assert proposal.target_fence is not None
    stale_source = CandidateBindingFenceV1(
        owner_key="builtin",
        scope="builtin",
        scope_key="global",
        pack_id="core.summarize-day",
        expected_absent=False,
        binding_generation=5,
        version="1.0.1",
        manifest_hash="c" * 64,
    )
    postprocessor = ReflectionResultPostprocessor(
        store=store,
        evidence_loader=lambda owner, event_ids: LiveReflectionEvidenceV1(
            evidence=(_evidence(),),
            explicit_user_signal=True,
        ),
        target_facts_resolver=lambda owner, target, mode: (
            LiveGrowthTargetFactsV1(
                target=proposal.target,
                candidate_mode=mode,
                source_fence=stale_source,
                target_fence=proposal.target_fence,
            )
        ),
    )

    await postprocessor(_result())

    _, admitted = store.calls[0]
    assert admitted["decision"] == "stale"
    assert admitted["reason_code"] == "reflection_live_target_fence_mismatch"
    assert "proposal" not in admitted
    assert "proposal_ref" not in admitted


@pytest.mark.asyncio
async def test_insufficient_independent_evidence_abstains_before_fence_load() -> None:
    store = FakeStore()
    target_calls = []
    postprocessor = ReflectionResultPostprocessor(
        store=store,
        evidence_loader=lambda owner, event_ids: LiveReflectionEvidenceV1(
            evidence=(_evidence(),),
            explicit_user_signal=False,
        ),
        target_facts_resolver=lambda *args: target_calls.append(args),
    )

    await postprocessor(_result())

    assert target_calls == []
    _, admitted = store.calls[0]
    assert admitted["decision"] == "abstain"
    assert admitted["reason_code"] == (
        "reflection_insufficient_independent_evidence"
    )
    assert admitted["proposal"].decision.value == "abstain"
    assert admitted["proposal"].abstain_reason == (
        "insufficient_independent_evidence"
    )


@pytest.mark.asyncio
async def test_host_neutral_semantic_result_gets_host_target_and_fences() -> None:
    store = FakeStore()
    target_calls = []
    semantic = {
        "decision": "propose",
        "target_kind": "skill",
        "stable_name": "summarize-day",
        "hypothesis": "A two-item summary is easier to act on.",
        "requested_change": (
            "Keep only the two most important items and attach one next step "
            "to each; do not add a separate follow-up section."
        ),
        "risk_hints": ["instruction_only"],
    }

    def resolve_target(owner, target_kind, stable_name):
        target_calls.append((owner, target_kind, stable_name))
        return _live_facts()

    postprocessor = ReflectionResultPostprocessor(
        store=store,
        evidence_loader=lambda owner, event_ids: LiveReflectionEvidenceV1(
            evidence=(_evidence(),),
            explicit_user_signal=True,
        ),
        target_facts_resolver=resolve_target,
    )

    await postprocessor(_result(semantic))

    assert target_calls == [(OWNER, "skill", "summarize-day")]
    _, admitted = store.calls[0]
    proposal = admitted["proposal"]
    assert admitted["decision"] == "candidate"
    assert proposal.target == _proposal().target
    assert proposal.candidate_mode == _proposal().candidate_mode
    assert proposal.source_fence == _proposal().source_fence
    assert proposal.target_fence == _proposal().target_fence
    assert proposal.evidence_event_ids == (EVENT_ID,)
    assert proposal.evidence_set_hash == evidence_set_hash((EVENT_ID,))
    assert proposal.structured_diff == {
        "requested_change": semantic["requested_change"]
    }
    assert proposal.expected_improvement == semantic["hypothesis"]
    assert proposal.risk_hints == ("instruction_only",)
    assert len(proposal.evaluation_plan) == 1
    assert proposal.evaluation_plan[0]["assertion"] == (
        "requested_change_observed"
    )


@pytest.mark.asyncio
async def test_semantic_result_optional_quality_fields_are_preserved() -> None:
    store = FakeStore()
    semantic = {
        "decision": "propose",
        "target_kind": "skill",
        "stable_name": "summarize-day",
        "hypothesis": "A shorter summary is easier to scan.",
        "requested_change": "Return exactly two actionable items.",
        "risk_hints": ["instruction_only"],
        "expected_improvement": "Output has exactly two actionable items.",
        "evaluation_plan": [
            {
                "case_id": "two-items",
                "assertion": "exactly_two_items",
            }
        ],
    }
    postprocessor = ReflectionResultPostprocessor(
        store=store,
        evidence_loader=lambda owner, event_ids: LiveReflectionEvidenceV1(
            evidence=(_evidence(),),
            explicit_user_signal=True,
        ),
        target_facts_resolver=lambda owner, kind, name: _live_facts(),
    )

    await postprocessor(_result(semantic))

    proposal = store.calls[0][1]["proposal"]
    assert proposal.expected_improvement == semantic["expected_improvement"]
    assert proposal.evaluation_plan == (
        {"case_id": "two-items", "assertion": "exactly_two_items"},
    )


@pytest.mark.asyncio
async def test_semantic_model_cannot_supply_owner_or_fence_authority() -> None:
    store = FakeStore()
    calls = []
    semantic = {
        "decision": "propose",
        "target_kind": "skill",
        "stable_name": "summarize-day",
        "hypothesis": "Shorter is easier to scan.",
        "requested_change": "Return two items.",
        "risk_hints": ["instruction_only"],
        "owner_key": OWNER_KEY,
        "target_fence": _proposal().target_fence.to_dict(),
    }

    def should_not_load(*args):
        calls.append(args)
        raise AssertionError("host loaders must not run")

    postprocessor = ReflectionResultPostprocessor(
        store=store,
        evidence_loader=should_not_load,
        target_facts_resolver=should_not_load,
    )

    with pytest.raises(
        ValueError,
        match="semantic_growth_result_fields_invalid",
    ):
        await postprocessor(_result(semantic))

    assert calls == []
    assert store.calls == []


@pytest.mark.asyncio
async def test_semantic_abstain_never_resolves_target() -> None:
    store = FakeStore()
    target_calls = []
    semantic = {
        "decision": "abstain",
        "target_kind": "none",
        "stable_name": None,
        "hypothesis": None,
        "requested_change": None,
        "risk_hints": [],
    }
    postprocessor = ReflectionResultPostprocessor(
        store=store,
        evidence_loader=lambda owner, event_ids: LiveReflectionEvidenceV1(
            evidence=(_evidence(),),
            explicit_user_signal=True,
        ),
        target_facts_resolver=lambda *args: target_calls.append(args),
    )

    await postprocessor(_result(semantic))

    assert target_calls == []
    _, admitted = store.calls[0]
    assert admitted["decision"] == "abstain"
    assert admitted["reason_code"] == "reflection_model_abstained"
    assert admitted["proposal"].abstain_reason == "model_abstained"
