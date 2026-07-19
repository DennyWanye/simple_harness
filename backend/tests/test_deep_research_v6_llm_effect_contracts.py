from __future__ import annotations

import copy
import hashlib

import pytest

from deskpet.workflows.definitions.deep_research_v6_evidence_contracts import (
    EvidenceRepairRequestV1,
    ResearchLLMEffectOutcomeV1,
)


def _ref(number: int) -> str:
    return "sha256:" + format(number, "064x")


def _sample_journal_effect_id(logical: str) -> str:
    # Tests use a deterministic fixture value; production supplies the real
    # workflow_effects journal row ID, which is not derived from logical alone.
    return hashlib.sha256(logical.encode("utf-8")).hexdigest()


def test_repair_request_roundtrip_identity_and_exact_keys() -> None:
    request = EvidenceRepairRequestV1.create(
        result_kind="candidate_bundle",
        original_profile_ref=_ref(1),
        repair_profile_ref=_ref(2),
        original_prompt_ref=_ref(3),
        prior_outcome_ref=_ref(4),
        raw_result_ref=_ref(5),
        validation_reason_codes=("schema_invalid", "candidate_id_mismatch"),
        logical_page_id="page-one",
        work_group_id="work-group-one",
        repair_round=1,
    )
    assert request.repair_id == "err_79ae49e47b777283cf6bad66"
    assert request.validation_reason_codes == (
        "candidate_id_mismatch", "schema_invalid",
    )
    assert EvidenceRepairRequestV1.from_json(request.to_json()) == request
    assert set(request.to_json()) == {
        "schema_version", "repair_id", "result_kind", "original_profile_ref",
        "repair_profile_ref", "original_prompt_ref", "prior_outcome_ref",
        "raw_result_ref", "validation_reason_codes", "logical_page_id",
        "work_group_id", "repair_round",
    }


def test_round_zero_validated_outcome_has_exact_dependencies_and_golden_id() -> None:
    logical = "v6-extract:ns:checkpoint:task:work-group:page-one:r0"
    outcome = ResearchLLMEffectOutcomeV1.create(
        logical_effect_id=logical,
        effect_id=_sample_journal_effect_id(logical),
        status="validated",
        result_kind="candidate_bundle",
        profile_ref=_ref(10),
        prompt_ref=_ref(11),
        raw_result_ref=_ref(12),
        result_ref=_ref(13),
        repair_request_ref=None,
        prior_outcome_ref=None,
        repair_round=0,
        reason_codes=(),
    )
    assert outcome.effect_id == "c15cfa4e66b38b7a5756e4e3d0d776410c3ff5fee5ee6ed85ad83406d54d8d90"
    assert outcome.outcome_id == "rlo_9138fe336693124c14196b30"
    assert outcome.dependency_refs == (_ref(10), _ref(11), _ref(12), _ref(13))
    assert ResearchLLMEffectOutcomeV1.from_json(outcome.to_json()) == outcome


def test_round_one_validated_outcome_requires_repair_and_prior_refs() -> None:
    logical = "v6-repair:ns:checkpoint:task:work-group:page-one:r1"
    outcome = ResearchLLMEffectOutcomeV1.create(
        logical_effect_id=logical,
        effect_id=_sample_journal_effect_id(logical),
        status="validated",
        result_kind="candidate_bundle",
        profile_ref=_ref(20),
        prompt_ref=_ref(21),
        raw_result_ref=_ref(22),
        result_ref=_ref(23),
        repair_request_ref=_ref(24),
        prior_outcome_ref=_ref(25),
        repair_round=1,
        reason_codes=("repair_validated",),
    )
    assert outcome.outcome_id == "rlo_d47ae9f23243e72fa6773dc5"
    assert outcome.dependency_refs == tuple(_ref(number) for number in range(20, 26))
    assert ResearchLLMEffectOutcomeV1.from_json(outcome.to_json()) == outcome


@pytest.mark.parametrize(
    "status,raw_ref,result_ref,valid",
    [
        ("validated", _ref(3), _ref(4), True),
        ("validated", None, _ref(4), False),
        ("malformed", _ref(3), None, True),
        ("malformed", _ref(3), _ref(4), False),
        ("opaque_uncertain", None, None, True),
        ("opaque_uncertain", _ref(3), None, False),
        ("deadline", None, None, True),
        ("budget_denied", None, None, True),
    ],
)
def test_outcome_status_result_truth_table(status, raw_ref, result_ref, valid) -> None:
    logical = "v6-infer:ns:checkpoint:task:work-group:head:r0"
    kwargs = dict(
        logical_effect_id=logical,
        effect_id=_sample_journal_effect_id(logical),
        status=status,
        result_kind="inference_bundle",
        profile_ref=_ref(1),
        prompt_ref=_ref(2),
        raw_result_ref=raw_ref,
        result_ref=result_ref,
        repair_request_ref=None,
        prior_outcome_ref=None,
        repair_round=0,
        reason_codes=(status,),
    )
    if valid:
        ResearchLLMEffectOutcomeV1.create(**kwargs)
    else:
        with pytest.raises(ValueError):
            ResearchLLMEffectOutcomeV1.create(**kwargs)


def test_round_dependency_and_journal_effect_identity_truth_tables_fail_closed() -> None:
    logical = "v6-repair:ns:checkpoint:task:work-group:head:r1"
    base = dict(
        logical_effect_id=logical,
        effect_id=_sample_journal_effect_id(logical),
        status="malformed",
        result_kind="inference_bundle",
        profile_ref=_ref(1),
        prompt_ref=_ref(2),
        raw_result_ref=_ref(3),
        result_ref=None,
        repair_request_ref=_ref(4),
        prior_outcome_ref=_ref(5),
        repair_round=1,
        reason_codes=("schema_invalid",),
    )
    value = ResearchLLMEffectOutcomeV1.create(**base).to_json()
    extra = copy.deepcopy(value)
    extra["dependency_refs"].append(_ref(99))
    with pytest.raises(ValueError, match="exact non-null ref set"):
        ResearchLLMEffectOutcomeV1.from_json(extra)
    wrong_effect = copy.deepcopy(value)
    wrong_effect["effect_id"] = "not-a-journal-digest"
    with pytest.raises(ValueError, match="effect_id must be 64"):
        ResearchLLMEffectOutcomeV1.from_json(wrong_effect)
    wrong_round = copy.deepcopy(value)
    wrong_round["repair_round"] = 0
    with pytest.raises(ValueError, match="round/namespace"):
        ResearchLLMEffectOutcomeV1.from_json(wrong_round)


@pytest.mark.parametrize("contract", ["repair", "outcome"])
def test_old_or_extra_wire_shapes_are_rejected(contract: str) -> None:
    if contract == "repair":
        value = EvidenceRepairRequestV1.create(
            result_kind="inference_bundle",
            original_profile_ref=_ref(1),
            repair_profile_ref=_ref(2),
            original_prompt_ref=_ref(3),
            prior_outcome_ref=_ref(4),
            raw_result_ref=_ref(5),
            validation_reason_codes=("schema_invalid",),
            logical_page_id="head-one",
            work_group_id="work-group-one",
            repair_round=1,
        ).to_json()
        loader = EvidenceRepairRequestV1.from_json
    else:
        logical = "v6-extract:ns:checkpoint:task:work-group:page:r0"
        value = ResearchLLMEffectOutcomeV1.create(
            logical_effect_id=logical,
            effect_id=_sample_journal_effect_id(logical),
            status="malformed",
            result_kind="candidate_bundle",
            profile_ref=_ref(1),
            prompt_ref=_ref(2),
            raw_result_ref=_ref(3),
            result_ref=None,
            repair_request_ref=None,
            prior_outcome_ref=None,
            repair_round=0,
            reason_codes=("schema_invalid",),
        ).to_json()
        loader = ResearchLLMEffectOutcomeV1.from_json

    extra = copy.deepcopy(value)
    extra["legacy_payload"] = {}
    with pytest.raises(ValueError, match="keys/version"):
        loader(extra)
    old = copy.deepcopy(value)
    old.pop("dependency_refs" if contract == "outcome" else "original_profile_ref")
    with pytest.raises(ValueError, match="keys/version"):
        loader(old)
