from __future__ import annotations

import copy

import pytest

from deskpet.workflows.definitions.deep_research_v6_evidence import (
    AnswerAssessmentV1,
    EvidenceFactBatchV1,
    GENESIS_EVIDENCE_HEAD,
)


def _batch() -> EvidenceFactBatchV1:
    policy_refs = {
        key: "sha256:" + format(index + 1, "064x")
        for index, key in enumerate(
            ("compiler", "route", "extraction", "llm_extract", "llm_repair",
             "llm_inference", "admission", "inference", "assessment", "claim", "quality")
        )
    }
    return EvidenceFactBatchV1.create(
        batch_kind="facts",
        run_id="run-ledger",
        spec_hash="a" * 64,
        previous_head_hash=GENESIS_EVIDENCE_HEAD,
        ordinal=0,
        page_result_refs=("sha256:" + "b" * 64,),
        candidate_slot_results=(),
        inference_slot_results=(),
        admitted_fact_refs=("sha256:" + "d" * 64,),
        registered_inference_refs=(),
        rejected_candidate_ids=(),
        conflict_ids=(),
        provenance_refs=("sha256:" + "b" * 64, "sha256:" + "c" * 64, "sha256:" + "d" * 64),
        policy_refs=policy_refs,
    )


def _assessment(head_hash: str) -> AnswerAssessmentV1:
    return AnswerAssessmentV1.create(
        spec_hash="a" * 64,
        evidence_head_hash=head_hash,
        requirement_results=(
            {
                "requirement_id": "req-1",
                "item_or_cell_id": "req-1",
                "support_status": "supported",
                "binding_ids": ["binding-1"],
                "reason_codes": ["binding_admitted"],
            },
        ),
        missing_requirement_ids=(),
        minimum_useful=True,
        status="completed_candidate",
        reason_codes=("all_required_supported",),
    )


def test_evidence_fact_batch_roundtrip_is_deterministic_and_strict() -> None:
    first = _batch()
    second = _batch()
    assert first == second
    assert EvidenceFactBatchV1.from_json(first.to_json()) == first

    unknown = {**first.to_json(), "compat": True}
    with pytest.raises(ValueError, match="keys/version"):
        EvidenceFactBatchV1.from_json(unknown)
    tampered = {**first.to_json(), "head_hash": "d" * 64}
    with pytest.raises(ValueError, match="head_hash mismatch"):
        EvidenceFactBatchV1.from_json(tampered)


def test_answer_assessment_roundtrip_is_deterministic_and_tamper_evident() -> None:
    batch = _batch()
    first = _assessment(batch.head_hash)
    second = _assessment(batch.head_hash)
    assert first == second
    assert AnswerAssessmentV1.from_json(first.to_json()) == first

    tampered = copy.deepcopy(first.to_json())
    tampered["minimum_useful"] = False
    with pytest.raises(ValueError, match="assessment_hash mismatch"):
        AnswerAssessmentV1.from_json(tampered)
    unknown = {**first.to_json(), "compat": True}
    with pytest.raises(ValueError, match="keys/version"):
        AnswerAssessmentV1.from_json(unknown)
