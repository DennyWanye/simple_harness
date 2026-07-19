from __future__ import annotations

import pytest

from deskpet.workflows.definitions.deep_research_v6_compiler import (
    compile_official_exact_fact,
    compile_research_spec,
)
from deskpet.workflows.definitions.deep_research_v6_contracts import ResearchSpecValidationError


@pytest.mark.parametrize(
    "question,locale",
    [
        ("深入调研：2024年中国总人口和出生人口分别是多少？优先国家统计局。", "zh-CN"),
        ("请以国家统计局为优先来源，查清中国2024年人口总数以及出生人数。", "zh-CN"),
        ("What were China's total population and number of births in 2024? Prefer the National Bureau of Statistics.", "en-US"),
    ],
)
def test_stats2_paraphrases_compile_to_same_semantics(question: str, locale: str) -> None:
    spec = compile_official_exact_fact(question, answer_locale=locale)
    assert spec.intent_type == "official_exact_fact"
    assert [item["key"] for item in spec.requirements] == [
        "population.total.year_end",
        "population.births.period",
    ]
    total, births = spec.requirements
    assert total["time_scope"] == {"schema_version": 1, "kind": "instant", "start": None, "end": None, "as_of": "2024-12-31", "label": "2024 year end"}
    assert births["time_scope"]["start"] == "2024-01-01"
    assert births["time_scope"]["end"] == "2024-12-31"
    assert all(item["value_schema"]["canonical_unit"]["unit_id"] == "person" for item in spec.requirements)
    assert all("answer" not in item and "numeric_value" not in item for item in spec.requirements)
    concepts = {
        term
        for dimension in spec.work_dimensions
        for term in dimension["search_concepts"]
    }
    assert {"全国人口", "年末人口", "出生人口"} <= concepts


def test_missing_or_illegal_llm_bundle_does_not_remove_deterministic_base() -> None:
    question = "2024年中国总人口和出生人口，优先国家统计局"
    baseline = compile_official_exact_fact(question)
    for candidates in (None, "illegal json", [{"not_key": True}]):
        result = compile_official_exact_fact(question, llm_candidates=candidates)
        assert [item["key"] for item in result.requirements] == [item["key"] for item in baseline.requirements]


def test_conflicting_llm_candidate_fails_closed() -> None:
    baseline = compile_official_exact_fact("2024年中国总人口，优先国家统计局")
    candidate = baseline.requirements[0] | {
        "time_scope": {"schema_version": 1, "kind": "instant", "start": None, "end": None, "as_of": "2023-12-31", "label": "2023"}
    }
    with pytest.raises(ResearchSpecValidationError) as error:
        compile_official_exact_fact("2024年中国总人口，优先国家统计局", llm_candidates=[candidate])
    assert error.value.code == "spec_merge_conflict"


def test_unknown_intent_gets_deterministic_claim_set() -> None:
    spec = compile_research_spec("人工智能会怎样影响设计行业？", as_of_date="2026-07-18")
    assert spec.intent_type == "open_research"
    assert [item["kind"] for item in spec.requirements] == ["claim_set"]
    assert [item["claim_kind"] for item in spec.requirements[0]["claim_kinds"]] == [
        "conclusion",
        "limitation",
        "counterevidence",
        "uncertainty",
    ]


def test_comparison_golden_spec_freezes_two_subjects_and_six_axes() -> None:
    spec = compile_research_spec(
        "比较 ChatGPT 和 Claude，按功能、价格、易用性、性能、安全性、生态六个轴比较。",
        as_of_date="2026-07-18",
    )

    assert spec.intent_type == "comparison"
    assert [subject["label"] for subject in spec.subjects] == ["ChatGPT", "Claude"]
    assert len(spec.requirements) == 1
    requirement = spec.requirements[0]
    assert requirement["kind"] == "matrix"
    assert [axis["role"] for axis in requirement["axes"]] == ["subject", "criterion"]
    assert len(requirement["axes"][0]["members"]) == 2
    assert [item["label"] for item in requirement["axes"][1]["members"]] == [
        "功能",
        "价格",
        "易用性",
        "性能",
        "安全性",
        "生态",
    ]
    assert requirement["required_cells"] == {"mode": "cartesian_product", "excluded": []}
    assert requirement["coverage"]["minimum_ratio_ppm"] == 1_000_000
    assert "cells" not in requirement
    assert len(spec.work_dimensions) == 6
    assert all(
        dimension["requirement_ids"] == [requirement["requirement_id"]]
        for dimension in spec.work_dimensions
    )


def test_top_n_golden_spec_is_one_dynamic_collection_without_empty_slots() -> None:
    spec = compile_research_spec(
        "当前最值得关注的 10 个 AI 产品及其优缺点",
        as_of_date="2026-07-18",
    )

    assert spec.intent_type == "top_n"
    assert len(spec.requirements) == 1
    requirement = spec.requirements[0]
    assert requirement["kind"] == "collection"
    assert requirement["item_schema"]["unique_key"] == ["product_name"]
    assert [field["field_key"] for field in requirement["item_schema"]["fields"]] == [
        "product_name",
        "attention_score",
        "advantages",
        "disadvantages",
    ]
    assert requirement["selection"] == {
        "mode": "top_n",
        "minimum_items": 10,
        "maximum_items": 10,
        "as_of": "2026-07-18",
        "ranking_rule": {
            "metric_key": "attention_score",
            "direction": "descending",
            "tie_breakers": ["product_name"],
            "missing_metric": "ineligible",
        },
    }
    assert not ({"items", "item_ids", "slots"} & set(requirement))


def test_policy_golden_spec_separates_document_facts_from_impact_inference() -> None:
    spec = compile_research_spec(
        "最新人工智能政策方向及影响是什么？",
        as_of_date="2026-07-18",
    )

    assert spec.intent_type == "policy"
    requirement = spec.requirements[0]
    assert requirement["kind"] == "claim_set"
    kinds = {item["claim_kind"]: item for item in requirement["claim_kinds"]}
    assert kinds["policy_fact"]["minimum_claims"] == 4
    assert kinds["policy_fact"]["support_rule"] == "admitted_binding"
    assert kinds["impact_inference"]["support_rule"] == (
        "admitted_binding_or_registered_inference"
    )
    assert [item["facet_id"] for item in requirement["topic_facets"]] == [
        "issuer",
        "document",
        "date",
        "commitment",
        "impact",
    ]
    assert requirement["time_scope"]["as_of"] == "2026-07-18"
    assert len(spec.work_dimensions) == 5


def test_compile_research_spec_preserves_exact_fact_behavior() -> None:
    question = "深入调研：2024年中国总人口和出生人口分别是多少？优先国家统计局。"
    assert compile_research_spec(question).to_json() == compile_official_exact_fact(
        question
    ).to_json()


def test_typed_intent_llm_candidates_cannot_delete_or_downgrade_base() -> None:
    question = "当前最值得关注的 10 个 AI 产品及其优缺点"
    baseline = compile_research_spec(question, as_of_date="2026-07-18")
    requirement = baseline.requirements[0]
    candidate = dict(requirement)
    candidate["importance"] = "optional"

    compiled = compile_research_spec(
        question,
        as_of_date="2026-07-18",
        llm_candidates=[candidate],
    )

    assert len(compiled.requirements) == 1
    assert compiled.requirements[0]["importance"] == "required"
    assert compiled.requirements[0]["selection"] == requirement["selection"]


def test_official_exact_fact_requires_one_year_and_china() -> None:
    with pytest.raises(ResearchSpecValidationError):
        compile_official_exact_fact("中国总人口是多少")
    with pytest.raises(ResearchSpecValidationError):
        compile_official_exact_fact("2024年美国总人口是多少")
