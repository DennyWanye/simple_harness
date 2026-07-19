from __future__ import annotations

import copy

import pytest

from deskpet.workflows.definitions.deep_research_v6_contracts import sha256_json
from deskpet.workflows.definitions.deep_research_v6_integrity import (
    ClaimBatchV1,
    ClaimRecordV1,
    IntegrityValidationError,
    QualityAuditV1,
    build_q1_exact_scalar_integrity,
    derive_fact_batch_head,
    validate_q1_exact_scalar_integrity,
)


RUN_ID = "run-q1-integrity"
SPEC_HASH = "a" * 64
CLAIM_POLICY_HASH = "b" * 64
QUALITY_POLICY_HASH = "c" * 64
REQUIREMENTS = ("req_births", "req_population")
HIGH_SCORES = {
    "readability": 900_000,
    "source_diversity": 900_000,
    "analysis_depth": 900_000,
    "counterevidence": 900_000,
    "uncertainty": 900_000,
}


def _fact_batch(*, admitted: tuple[str, ...]) -> dict:
    value = {
        "schema_version": 1,
        "batch_id": "efb_test",
        "run_id": RUN_ID,
        "spec_hash": SPEC_HASH,
        "previous_head_hash": "0" * 64,
        "ordinal": 0,
        "page_result_ref": "sha256:" + "d" * 64,
        "admitted_binding_ids": sorted(admitted),
        "rejected_binding_ids": [],
        "conflict_ids": [],
        "provenance_refs": ["sha256:" + "e" * 64],
        "policy_hash": "f" * 64,
        "head_hash": "0" * 64,
    }
    value["head_hash"] = derive_fact_batch_head(value)
    return value


def _assessment(
    *,
    supported: dict[str, str],
    head_hash: str,
    status: str | None = None,
) -> dict:
    results = []
    for requirement_id in sorted(REQUIREMENTS):
        binding_id = supported.get(requirement_id)
        results.append(
            {
                "requirement_id": requirement_id,
                "item_or_cell_id": None,
                "support_status": "supported" if binding_id else "missing",
                "binding_ids": [binding_id] if binding_id else [],
                "reason_codes": [] if binding_id else ["missing"],
            }
        )
    missing = sorted(set(REQUIREMENTS) - set(supported))
    if status is None:
        status = (
            "completed_candidate"
            if not missing
            else "partial_candidate"
            if supported
            else "insufficient"
        )
    payload = {
        "schema_version": 1,
        "assessment_id": "pending",
        "assessment_hash": "0" * 64,
        "spec_hash": SPEC_HASH,
        "evidence_head_hash": head_hash,
        "policy_hash": "1" * 64,
        "requirement_results": results,
        "conflicts": [],
        "missing_requirement_ids": missing,
        "minimum_useful": bool(supported),
        "status": status,
        "reason_codes": [],
    }
    identity = copy.deepcopy(payload)
    identity.pop("assessment_id")
    identity.pop("assessment_hash")
    assessment_hash = sha256_json(identity)
    payload["assessment_hash"] = assessment_hash
    payload["assessment_id"] = "asa_" + assessment_hash[:24]
    return payload


def _claim(
    requirement_id: str,
    binding_id: str,
    *,
    support_status: str = "supported",
) -> ClaimRecordV1:
    return ClaimRecordV1.create(
        requirement_id=requirement_id,
        item_or_cell_id=None,
        claim_kind="fact",
        normalized_proposition=f"{requirement_id} has an exact official value",
        binding_ids=(binding_id,),
        inference_ref=None,
        support_status=support_status,
        visibility="user",
    )


def _inputs(
    *,
    supported: dict[str, str],
    claims: tuple[ClaimRecordV1, ...],
    requested_answer_status: str,
    admitted: tuple[str, ...] | None = None,
) -> dict:
    admitted = admitted if admitted is not None else tuple(sorted(supported.values()))
    batches = [_fact_batch(admitted=admitted)] if admitted else []
    head_hash = batches[-1]["head_hash"] if batches else "0" * 64
    return {
        "run_id": RUN_ID,
        "spec_hash": SPEC_HASH,
        "required_requirement_ids": REQUIREMENTS,
        "answer_assessment": _assessment(supported=supported, head_hash=head_hash),
        "evidence_fact_batches": batches,
        "claims": claims,
        "requested_answer_status": requested_answer_status,
        "claim_policy_hash": CLAIM_POLICY_HASH,
        "quality_policy_hash": QUALITY_POLICY_HASH,
        "soft_scores": HIGH_SCORES,
    }


@pytest.mark.parametrize(
    ("supported", "claims", "status"),
    [
        (
            {"req_births": "binding_births", "req_population": "binding_population"},
            (
                _claim("req_births", "binding_births"),
                _claim("req_population", "binding_population"),
            ),
            "completed",
        ),
        (
            {"req_population": "binding_population"},
            (_claim("req_population", "binding_population"),),
            "partial",
        ),
        ({}, (), "insufficient_evidence"),
    ],
)
def test_completed_partial_insufficient_matrix_is_derived_and_passes(
    supported, claims, status
) -> None:
    result = build_q1_exact_scalar_integrity(
        **_inputs(supported=supported, claims=claims, requested_answer_status=status)
    )

    assert result.answer_status == status
    assert result.claim_batch.status == "valid"
    assert result.quality_audit.hard_gate_status == "passed"
    assert result.hard_failure_codes == ()
    assert result.quality_audit.soft_scores == HIGH_SCORES


def test_high_soft_quality_cannot_override_missing_required_result() -> None:
    inputs = _inputs(
        supported={"req_population": "binding_population"},
        claims=(_claim("req_population", "binding_population"),),
        requested_answer_status="completed",
    )

    result = build_q1_exact_scalar_integrity(**inputs)

    assert result.answer_status == "partial"
    assert result.claim_batch.status == "invalid"
    assert result.quality_audit.hard_gate_status == "failed"
    assert "answer_status_mismatch" in result.hard_failure_codes
    assert set(result.quality_audit.soft_scores.values()) == {0}


def test_dangling_binding_hard_fails_and_downgrades_completed() -> None:
    inputs = _inputs(
        supported={"req_births": "binding_births", "req_population": "binding_population"},
        claims=(
            _claim("req_births", "binding_births"),
            _claim("req_population", "binding_dangling"),
        ),
        requested_answer_status="completed",
    )

    result = build_q1_exact_scalar_integrity(**inputs)

    assert result.answer_status == "partial"
    assert result.quality_audit.hard_gate_status == "failed"
    assert {"claim_binding_dangling", "claim_binding_mismatch"} <= set(
        result.hard_failure_codes
    )


def test_unsupported_visible_claim_is_never_admitted_by_soft_quality() -> None:
    inputs = _inputs(
        supported={"req_population": "binding_population"},
        claims=(
            _claim(
                "req_population",
                "binding_population",
                support_status="unsupported",
            ),
        ),
        requested_answer_status="partial",
    )

    result = build_q1_exact_scalar_integrity(**inputs)

    assert result.answer_status == "insufficient_evidence"
    assert result.claim_batch.visible_claim_ids == []
    assert result.quality_audit.hard_gate_status == "failed"
    assert "claim_unsupported" in result.hard_failure_codes


def test_supported_requirement_with_omitted_claim_hard_fails() -> None:
    inputs = _inputs(
        supported={"req_births": "binding_births", "req_population": "binding_population"},
        claims=(_claim("req_births", "binding_births"),),
        requested_answer_status="completed",
    )

    result = build_q1_exact_scalar_integrity(**inputs)

    assert result.answer_status == "partial"
    assert result.quality_audit.hard_gate_status == "failed"
    assert "claim_omitted" in result.hard_failure_codes


def test_claim_batch_and_audit_roundtrip_are_deterministic_and_tamper_evident() -> None:
    inputs = _inputs(
        supported={"req_births": "binding_births", "req_population": "binding_population"},
        claims=(
            _claim("req_population", "binding_population"),
            _claim("req_births", "binding_births"),
        ),
        requested_answer_status="completed",
    )
    first = build_q1_exact_scalar_integrity(**inputs)
    second = build_q1_exact_scalar_integrity(**inputs)

    assert first.claim_batch.to_json() == second.claim_batch.to_json()
    assert first.quality_audit.to_json() == second.quality_audit.to_json()
    assert ClaimBatchV1.from_json(first.claim_batch.to_json()).to_json() == first.claim_batch.to_json()
    assert QualityAuditV1.from_json(first.quality_audit.to_json()).to_json() == first.quality_audit.to_json()
    assert validate_q1_exact_scalar_integrity(
        claim_batch=first.claim_batch,
        quality_audit=first.quality_audit,
        **inputs,
    ) == first

    tampered_claim = first.claim_batch.to_json()["claims"][0]
    tampered_claim["normalized_proposition"] = "tampered"
    with pytest.raises(IntegrityValidationError, match="claim_id_mismatch"):
        ClaimRecordV1.from_json(tampered_claim)

    tampered_batch = first.claim_batch.to_json()
    tampered_batch["status"] = "invalid"
    with pytest.raises(IntegrityValidationError, match="claim_batch_id_mismatch"):
        ClaimBatchV1.from_json(tampered_batch)

    tampered_audit = first.quality_audit.to_json()
    tampered_audit["soft_scores"]["readability"] -= 1
    with pytest.raises(IntegrityValidationError, match="quality_audit_id_mismatch"):
        QualityAuditV1.from_json(tampered_audit)


def test_contracts_reject_unknown_keys_and_caller_forged_pass() -> None:
    claim = _claim("req_population", "binding_population")
    malformed = claim.to_json()
    malformed["unknown"] = True
    with pytest.raises(IntegrityValidationError, match="integrity_keys_differ"):
        ClaimRecordV1.from_json(malformed)

    inputs = _inputs(
        supported={"req_population": "binding_population"},
        claims=(claim,),
        requested_answer_status="completed",
    )
    result = build_q1_exact_scalar_integrity(**inputs)
    forged = result.quality_audit.to_json()
    forged["hard_gate_status"] = "passed"
    forged["hard_failure_codes"] = []
    with pytest.raises(IntegrityValidationError, match="quality_audit_id_mismatch"):
        QualityAuditV1.from_json(forged)
