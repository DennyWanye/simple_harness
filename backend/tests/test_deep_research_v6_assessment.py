from __future__ import annotations

import random

from deskpet.workflows.definitions.deep_research_v6_assessment import (
    GenericAdmittedFactV1,
    assess_requirements,
    derive_collection_item_id,
    derive_matrix_cell_id,
    rank_collection_item_ids,
)
from deskpet.workflows.definitions.deep_research_v6_compiler import compile_research_spec


HEAD = "e" * 64


def _requirement(spec):
    return spec.requirements[0]


def _fact(requirement_id: str, binding_id: str, **values) -> GenericAdmittedFactV1:
    return GenericAdmittedFactV1.create(
        requirement_id=requirement_id,
        binding_ids=(binding_id,),
        **values,
    )


def test_matrix_cartesian_cells_are_independent_and_half_coverage_is_partial() -> None:
    spec = compile_research_spec(
        "Compare Alpha and Beta across product criteria", as_of_date="2026-07-18"
    )
    requirement = _requirement(spec)
    requirement_id = requirement["requirement_id"]
    subject_members = [item["member_id"] for item in requirement["axes"][0]["members"]]
    criterion_members = [item["member_id"] for item in requirement["axes"][1]["members"]]
    supported_cells = [
        (subject, criterion)
        for subject in subject_members
        for criterion in criterion_members[:3]
    ]
    facts = [
        _fact(requirement_id, f"binding-{index}", axis_member_ids=members)
        for index, members in enumerate(supported_cells)
    ]

    assessment = assess_requirements(spec=spec, evidence_head_hash=HEAD, facts=facts)

    assert len(assessment.requirement_results) == 12
    assert sum(item["support_status"] == "supported" for item in assessment.requirement_results) == 6
    assert assessment.status == "partial_candidate"
    assert assessment.minimum_useful is True
    expected_ids = {
        derive_matrix_cell_id(requirement_id, (subject, criterion))
        for subject in subject_members
        for criterion in criterion_members
    }
    assert {item["item_or_cell_id"] for item in assessment.requirement_results} == expected_ids


def test_matrix_inference_and_preference_do_not_impersonate_required_facts() -> None:
    spec = compile_research_spec(
        "Compare Alpha and Beta across product criteria", as_of_date="2026-07-18"
    )
    requirement = _requirement(spec)
    requirement_id = requirement["requirement_id"]
    members = (
        requirement["axes"][0]["members"][0]["member_id"],
        requirement["axes"][1]["members"][0]["member_id"],
    )
    facts = [
        _fact(requirement_id, "binding-inference", axis_member_ids=members, claim_kind="inference"),
        _fact(requirement_id, "binding-preference", axis_member_ids=members, claim_kind="preference"),
    ]
    assessment = assess_requirements(spec=spec, evidence_head_hash=HEAD, facts=facts)
    cell_id = derive_matrix_cell_id(requirement_id, members)
    cell = next(item for item in assessment.requirement_results if item["item_or_cell_id"] == cell_id)
    assert cell["support_status"] == "missing"
    assert cell["binding_ids"] == []
    assert assessment.status == "insufficient"


def _collection_facts(spec, count: int, *, duplicate_first: bool = False):
    requirement = _requirement(spec)
    requirement_id = requirement["requirement_id"]
    as_of = requirement["selection"]["as_of"]
    facts: list[GenericAdmittedFactV1] = []
    for index in range(1, count + 1):
        name = f"Product {index}"
        values = {
            "product_name": name,
            "attention_score": index,
            "advantages": [f"advantage {index}"],
            "disadvantages": [f"disadvantage {index}"],
        }
        for field_key, value in values.items():
            facts.append(_fact(
                requirement_id,
                f"binding-{index}-{field_key}",
                entity_values={"product_name": name},
                field_key=field_key,
                value=value,
                as_of=as_of,
            ))
    if duplicate_first:
        facts.append(_fact(
            requirement_id,
            "binding-duplicate-name",
            entity_values={"product_name": "ＰＲＯＤＵＣＴ １"},
            field_key="product_name",
            value="ＰＲＯＤＵＣＴ １",
            as_of=as_of,
        ))
    return facts


def test_top_n_dynamic_dedupe_ranking_and_nine_of_ten_is_partial() -> None:
    spec = compile_research_spec(
        "Top 10 AI products ranked by attention", as_of_date="2026-07-18"
    )
    requirement = _requirement(spec)
    facts = _collection_facts(spec, 9, duplicate_first=True)

    assessment = assess_requirements(spec=spec, evidence_head_hash=HEAD, facts=facts)

    assert assessment.status == "partial_candidate"
    assert len([item for item in assessment.requirement_results if item["support_status"] == "supported"]) == 9
    assert len(assessment.requirement_results) == 9
    ranked = rank_collection_item_ids(requirement, facts)
    assert ranked == tuple(
        derive_collection_item_id(requirement["requirement_id"], (f"Product {index}",))
        for index in range(9, 0, -1)
    )


def test_top_n_ten_items_is_completed_and_assessment_storage_is_identity_sorted() -> None:
    spec = compile_research_spec(
        "Top 10 AI products ranked by attention", as_of_date="2026-07-18"
    )
    assessment = assess_requirements(
        spec=spec, evidence_head_hash=HEAD, facts=_collection_facts(spec, 10)
    )
    assert assessment.status == "completed_candidate"
    identities = [
        (item["requirement_id"], item["item_or_cell_id"])
        for item in assessment.requirement_results
    ]
    assert identities == sorted(identities)


def test_policy_facts_and_registered_impact_inference_remain_separate() -> None:
    spec = compile_research_spec(
        "Explain the latest policy direction and impact", as_of_date="2026-07-18"
    )
    requirement_id = _requirement(spec)["requirement_id"]
    facts = [
        _fact(
            requirement_id,
            f"binding-{facet}",
            item_or_cell_id=f"claim-{facet}",
            claim_kind="policy_fact",
            facet_ids=(facet,),
        )
        for facet in ("issuer", "document", "date", "commitment")
    ]
    facts.append(GenericAdmittedFactV1.create(
        requirement_id=requirement_id,
        item_or_cell_id="claim-impact",
        claim_kind="impact_inference",
        facet_ids=("impact",),
        inference_ref="sha256:" + "f" * 64,
    ))

    assessment = assess_requirements(spec=spec, evidence_head_hash=HEAD, facts=facts)

    assert assessment.status == "completed_candidate"
    impact = next(item for item in assessment.requirement_results if item["item_or_cell_id"] == "claim-impact")
    assert impact["binding_ids"] == []
    assert impact["reason_codes"] == ["registered_inference"]


def test_open_research_enforces_all_four_claim_kind_minima() -> None:
    spec = compile_research_spec(
        "How will AI change design work?", as_of_date="2026-07-18"
    )
    requirement_id = _requirement(spec)["requirement_id"]
    kinds = ("conclusion", "limitation", "counterevidence", "uncertainty")
    facts = [
        _fact(
            requirement_id,
            f"binding-{kind}",
            item_or_cell_id=f"claim-{kind}",
            claim_kind=kind,
            facet_ids=("topic",),
        )
        for kind in kinds
    ]
    complete = assess_requirements(spec=spec, evidence_head_hash=HEAD, facts=facts)
    partial = assess_requirements(spec=spec, evidence_head_hash=HEAD, facts=facts[:-1])
    assert complete.status == "completed_candidate"
    assert partial.status == "partial_candidate"
    assert requirement_id in partial.missing_requirement_ids


def test_recomputation_is_deterministic_across_fact_order() -> None:
    spec = compile_research_spec(
        "Top 10 AI products ranked by attention", as_of_date="2026-07-18"
    )
    facts = _collection_facts(spec, 10)
    shuffled = list(facts)
    random.Random(7718).shuffle(shuffled)
    first = assess_requirements(spec=spec, evidence_head_hash=HEAD, facts=facts)
    second = assess_requirements(spec=spec, evidence_head_hash=HEAD, facts=shuffled)
    assert first == second
    assert first.assessment_hash == second.assessment_hash


def test_exact_scalar_remains_compatible_with_one_result_per_requirement() -> None:
    spec = compile_research_spec(
        "What was China's total population in 2024?", as_of_date="2026-07-18"
    )
    requirement_id = _requirement(spec)["requirement_id"]
    assessment = assess_requirements(
        spec=spec,
        evidence_head_hash=HEAD,
        facts=[_fact(requirement_id, "binding-population")],
    )
    assert assessment.status == "completed_candidate"
    assert assessment.requirement_results == ({
        "requirement_id": requirement_id,
        "item_or_cell_id": requirement_id,
        "support_status": "supported",
        "binding_ids": ["binding-population"],
        "reason_codes": ["binding_admitted"],
    },)


def test_two_required_scalars_with_one_supported_is_honest_partial() -> None:
    spec = compile_research_spec(
        "What were China's total population and number of births in 2024? "
        "Prefer the National Bureau of Statistics.",
        as_of_date="2026-07-18",
    )
    first = spec.requirements[0]
    fact = GenericAdmittedFactV1.create(
        requirement_id=str(first["requirement_id"]),
        binding_ids=("binding-population",),
    )

    assessment = assess_requirements(
        spec=spec,
        evidence_head_hash=HEAD,
        facts=(fact,),
    )

    assert assessment.status == "partial_candidate"
    assert assessment.minimum_useful is True
    assert len(assessment.missing_requirement_ids) == 1
