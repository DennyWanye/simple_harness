from __future__ import annotations

import copy
import hashlib

import pytest

from deskpet.workflows.contracts import canonical_json
from deskpet.workflows.definitions.deep_research_v6_assessment import (
    assess_requirements,
    decode_assessment_inputs,
)
from deskpet.workflows.definitions.deep_research_v6_compiler import compile_research_spec
from deskpet.workflows.definitions.deep_research_v6_evidence import (
    AdmittedResearchFactV1,
    EvidenceFactBatchV1,
    GENESIS_EVIDENCE_HEAD,
    RegisteredInferenceV1,
    derive_assessment_input_hash,
)
from deskpet.workflows.definitions.deep_research_v6_evidence_contracts import (
    CandidateProducerOutcomeV1,
    EvidenceCandidateBundleV1,
    EvidenceCandidateV1,
    InferenceProposalBundleV1,
    InferenceProposalV1,
)


def _ref(number: int) -> str:
    return "sha256:" + format(number, "064x")


def _policy_refs() -> dict[str, str]:
    names = (
        "compiler", "route", "extraction", "llm_extract", "llm_repair",
        "llm_inference", "admission", "inference", "assessment", "claim", "quality",
    )
    return {name: _ref(index + 1) for index, name in enumerate(names)}


def _scalar_candidate(body: bytes) -> EvidenceCandidateV1:
    excerpt = "总人口140828万人".encode()
    start = body.index(excerpt)
    return EvidenceCandidateV1.create(
        body_bytes=body,
        candidate_kind="scalar",
        work_group_id="wg-exact",
        logical_page_id="page-one",
        page_plan_ordinal=0,
        candidate_ordinal=0,
        requirement_id="req-population",
        span_start_byte=start,
        span_end_byte=start + len(excerpt),
        normalized_proposition="2024 total population was 140828 ten-thousand persons",
        payload={
            "item_or_cell_id": None,
            "value": 140828,
            "canonical_unit": "ten_thousand_persons",
            "time_scope": "2024",
            "scope": "China",
            "definition": "year-end national population",
        },
    )


def test_candidate_bundle_verifies_real_utf8_byte_span_and_identity() -> None:
    body = "公报：总人口140828万人。".encode("utf-8")
    candidate = _scalar_candidate(body)
    bundle = EvidenceCandidateBundleV1.create(
        run_id="run-one",
        spec_hash="a" * 64,
        work_group_id="wg-exact",
        logical_page_id="page-one",
        page_plan_ordinal=0,
        page_result_ref=_ref(20),
        route_decision_ref=_ref(21),
        route_policy_ref=_ref(22),
        extraction_policy_ref=_ref(23),
        repair_round=0,
        candidates=(candidate,),
        bundle_reason_codes=(),
    )
    assert EvidenceCandidateBundleV1.from_json(bundle.to_json(), body_bytes=body) == bundle

    tampered_body = body.replace(b"140828", b"140829")
    with pytest.raises(ValueError, match="excerpt_hash_mismatch"):
        EvidenceCandidateBundleV1.from_json(bundle.to_json(), body_bytes=tampered_body)

    misaligned = copy.deepcopy(candidate.to_json())
    misaligned["span_start_byte"] += 1
    base = {key: value for key, value in misaligned.items() if key != "candidate_id"}
    misaligned["candidate_id"] = "ecd_" + hashlib.sha256(
        canonical_json(base).encode("utf-8")
    ).hexdigest()[:24]
    with pytest.raises(ValueError, match="span_not_utf8_aligned"):
        EvidenceCandidateV1.from_json(misaligned, body_bytes=body)


def test_producer_and_inference_bundle_truth_tables_are_strict() -> None:
    producer = CandidateProducerOutcomeV1.create(
        origin="deterministic",
        status="validated",
        logical_page_id="page-one",
        work_group_id="wg-exact",
        bundle_ref=_ref(30),
        llm_effect_outcome_ref=None,
        policy_ref=_ref(31),
        dependency_refs=(_ref(20), _ref(30), _ref(31)),
    )
    assert CandidateProducerOutcomeV1.from_json(producer.to_json()) == producer
    with pytest.raises(ValueError, match="deterministic producer truth table"):
        CandidateProducerOutcomeV1.create(
            origin="deterministic", status="malformed", logical_page_id="page-one",
            work_group_id="wg-exact", bundle_ref=None, llm_effect_outcome_ref=None,
            policy_ref=_ref(31), dependency_refs=(_ref(31),),
        )

    proposal = InferenceProposalV1.create(
        requirement_id="req-policy",
        inference_kind="impact",
        item_or_cell_id="claim-impact",
        facet_ids=("impact",),
        normalized_proposition="The policy changes procurement incentives.",
        premise_fact_refs=(_ref(40), _ref(41)),
        model_id="gpt-test",
        model_policy_ref=_ref(42),
    )
    bundle = InferenceProposalBundleV1.create(
        run_id="run-one", spec_hash="a" * 64, work_group_id="wg-policy",
        input_evidence_head_hash="b" * 64, ordinal=0, profile_ref=_ref(43),
        proposals=(proposal,),
    )
    assert bundle.premise_fact_refs == (_ref(40), _ref(41))
    assert InferenceProposalBundleV1.from_json(bundle.to_json()) == bundle


def _scalar_fact(requirement_id: str) -> AdmittedResearchFactV1:
    return AdmittedResearchFactV1.create(
        run_id="run-one", spec_hash="a" * 64, requirement_id=requirement_id,
        target_kind="scalar", item_or_cell_id=None, field_or_facet_key=None,
        candidate_id="ecd-candidate", page_id="page-one", span_id="span-one",
        binding_id="binding-one", source_family_id="nbs", source_tier="first_party",
        admission_policy_hash="c" * 64, status="admitted",
        semantic_payload={
            "value": 140828, "canonical_unit": "ten_thousand_persons",
            "time_scope": "2024", "scope": "China",
            "definition": "year-end national population",
        },
    )


def test_registered_fact_and_inference_truth_tables_and_final_batches() -> None:
    fact = _scalar_fact("req-one")
    assert AdmittedResearchFactV1.from_json(fact.to_json()) == fact
    registered = RegisteredInferenceV1.create(
        run_id="run-one", spec_hash="a" * 64, requirement_id="req-one",
        item_or_cell_id="claim-one", inference_kind="conclusion", facet_ids=("topic",),
        normalized_proposition="A supported conclusion", proposed_premise_fact_refs=(_ref(50),),
        premise_fact_refs=(_ref(50),), premise_binding_ids=("binding-one",),
        model_id="gpt-test", model_policy_ref=_ref(51), inference_policy_hash="d" * 64,
        status="registered", reason_codes=(),
    )
    assert RegisteredInferenceV1.from_json(registered.to_json()) == registered
    with pytest.raises(ValueError, match="registered inference truth table"):
        RegisteredInferenceV1.create(
            run_id="run-one", spec_hash="a" * 64, requirement_id="req-one",
            item_or_cell_id="claim-one", inference_kind="conclusion", facet_ids=("topic",),
            normalized_proposition="bad", proposed_premise_fact_refs=(_ref(50),),
            premise_fact_refs=(), premise_binding_ids=(), model_id="gpt-test",
            model_policy_ref=_ref(51), inference_policy_hash="d" * 64,
            status="registered", reason_codes=(),
        )

    facts_batch = EvidenceFactBatchV1.create(
        batch_kind="facts", run_id="run-one", spec_hash="a" * 64,
        previous_head_hash=GENESIS_EVIDENCE_HEAD, ordinal=0,
        page_result_refs=(_ref(20),),
        candidate_slot_results=({"logical_page_id": "page-one", "work_group_id": "wg-one", "producer_outcome_ref": _ref(30), "bundle_ref": _ref(31)},),
        inference_slot_results=(), admitted_fact_refs=(_ref(50),),
        registered_inference_refs=(), rejected_candidate_ids=(), conflict_ids=(),
        provenance_refs=(_ref(20), _ref(30), _ref(31), _ref(50)), policy_refs=_policy_refs(),
    )
    inference_batch = EvidenceFactBatchV1.create(
        batch_kind="inferences", run_id="run-one", spec_hash="a" * 64,
        previous_head_hash=facts_batch.head_hash, ordinal=1, page_result_refs=(),
        candidate_slot_results=(),
        inference_slot_results=({"work_group_id": "wg-one", "effect_outcome_ref": _ref(60), "proposal_bundle_ref": _ref(61)},),
        admitted_fact_refs=(), registered_inference_refs=(_ref(62),),
        rejected_candidate_ids=(), conflict_ids=(), provenance_refs=(_ref(60), _ref(61), _ref(62)),
        policy_refs=_policy_refs(),
    )
    assert EvidenceFactBatchV1.from_json(facts_batch.to_json()) == facts_batch
    assert EvidenceFactBatchV1.from_json(inference_batch.to_json()) == inference_batch
    old_q1_shape = {"schema_version": 1, "page_result_ref": _ref(20)}
    with pytest.raises(ValueError, match="keys/version"):
        EvidenceFactBatchV1.from_json(old_q1_shape)


def test_decoder_and_assessment_input_hash_are_ref_order_sensitive() -> None:
    spec = compile_research_spec(
        "What was China's total population in 2024?", as_of_date="2026-07-18"
    )
    requirement_id = spec.requirements[0]["requirement_id"]
    fact = _scalar_fact(requirement_id)
    fact_ref = _ref(70)
    facts, inferences = decode_assessment_inputs(
        (fact_ref,), (), spec, registered_objects={fact_ref: fact.to_json()}
    )
    assessment = assess_requirements(
        spec=spec, evidence_head_hash="e" * 64, facts=facts, inferences=inferences,
        ordered_fact_refs=(fact_ref,), ordered_inference_refs=(),
    )
    expected = derive_assessment_input_hash(
        spec_hash=spec.spec_hash, evidence_head_hash="e" * 64,
        ordered_fact_refs=(fact_ref,), ordered_inference_refs=(),
        assessment_policy_hash=assessment.policy_hash,
    )
    assert assessment.assessment_input_hash == expected
    reordered = derive_assessment_input_hash(
        spec_hash=spec.spec_hash, evidence_head_hash="e" * 64,
        ordered_fact_refs=(_ref(71), fact_ref), ordered_inference_refs=(),
        assessment_policy_hash=assessment.policy_hash,
    )
    assert reordered != expected
