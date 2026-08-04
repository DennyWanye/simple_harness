from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from deskpet.workflows.definitions.deep_research_v5_contracts import (
    ContractValidationError,
    ResearchBrief,
)
from deskpet.workflows.definitions.deep_research_v5_policy import (
    EDUCATION_DIMENSION_IDS,
    GENERIC_DIMENSION_IDS,
    TECHNOLOGY_DIMENSION_IDS,
    build_research_brief,
    detect_research_intent,
    initial_dimension_coverages,
    merge_modeling_output,
    profile_definition,
    validate_modeling_output,
)


FIXTURE = Path(__file__).parent / "fixtures" / "deep_research_v5_brief_profiles.json"
AS_OF = date(2026, 7, 16)


def _model_dimension(
    dimension_id: str,
    question: str,
    query_target: str,
    *,
    importance: str = "core",
) -> dict:
    return {
        "dimension_id": dimension_id,
        "question": question,
        "importance": importance,
        "expected_source_types": ["official", "independent_research"],
        "query_targets": [query_target],
        "first_party_required": False,
        "not_applicable_when": ["user_explicitly_excludes_dimension"],
    }


def test_profile_fixture_language_geography_subjects_and_comparison_intent() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for case in payload["cases"]:
        detected = detect_research_intent(case["question"], as_of_date=AS_OF)
        assert detected.profile == case["profile"], case["id"]
        assert detected.locale == case["locale"], case["id"]
        assert detected.geography == case["geography"], case["id"]
        if "subjects" in case:
            assert detected.subjects == tuple(case["subjects"]), case["id"]
        assert detected.comparison_intents == tuple(case["comparison_intents"]), case["id"]


def test_sc_edu_01_has_exact_six_core_dimensions_and_source_requirements() -> None:
    brief = build_research_brief(
        "帮我调研一下，现在中国小学现在的教育现状和国家下一步计划",
        as_of_date=AS_OF,
    )

    assert brief.profile == "policy_education"
    assert tuple(item.dimension_id for item in brief.dimensions) == EDUCATION_DIMENSION_IDS
    assert all(item.importance == "core" for item in brief.dimensions)
    assert all(item.expected_source_types for item in brief.dimensions)
    assert all(item.query_targets for item in brief.dimensions)
    assert all(item.first_party_required for item in brief.dimensions)
    assert brief.not_applicable_conditions == ()
    assert ResearchBrief.from_json(brief.to_json()) == brief

    fields = {item.dimension_id: item for item in brief.dimensions}
    assert "double reduction implementation" in fields[
        "edu_double_reduction_after_school_burden"
    ].query_targets
    assert "national_plan" in fields["edu_national_next_plan"].expected_source_types
    assert "finance_budget" in fields["edu_teacher_finance"].expected_source_types


def test_generic_fallback_is_who_what_state_drivers_outlook_not_one_dimension() -> None:
    brief = build_research_brief(
        "Research the causes and outlook for global coffee prices",
        as_of_date=AS_OF,
    )
    assert brief.profile == "generic_research"
    assert tuple(item.dimension_id for item in brief.dimensions) == GENERIC_DIMENSION_IDS
    assert len(brief.dimensions) == 5
    assert all(item.importance == "core" for item in brief.dimensions)


def test_technology_profile_is_complete_and_deterministic() -> None:
    brief = build_research_brief(
        "Compare the latest GPT and Claude model releases and benchmarks",
        as_of_date=AS_OF,
    )
    assert brief.profile == "technology_intelligence"
    assert tuple(item.dimension_id for item in brief.dimensions) == TECHNOLOGY_DIMENSION_IDS
    assert brief.subjects == ("GPT", "Claude")
    assert "entity_comparison" in brief.expected_decision


def test_explicit_date_region_and_comparison_axes_are_normalized() -> None:
    intent = detect_research_intent(
        "截至2025年6月30日，比较中国城乡小学教育的历年变化与区域差异",
        as_of_date=AS_OF,
    )
    assert intent.as_of_date == "2025-06-30"
    assert intent.geography == "China"
    assert intent.locale == "zh-CN"
    assert intent.comparison_intents == (
        "urban_rural",
        "regional",
        "temporal",
        "entity_comparison",
    )


def test_only_explicit_user_exclusion_can_create_not_applicable_coverage() -> None:
    brief = build_research_brief(
        "调研中国小学教育现状，但不考虑双减与课后服务",
        as_of_date=AS_OF,
    )
    coverage = {item.dimension_id: item for item in initial_dimension_coverages(brief)}
    excluded = coverage["edu_double_reduction_after_school_burden"]
    assert excluded.status == "not_applicable"
    assert excluded.gap_reasons[0].startswith("user_explicit_exclusion:")
    assert all(
        value.status == "uncovered"
        for key, value in coverage.items()
        if key != "edu_double_reduction_after_school_burden"
    )

    ordinary = build_research_brief("调研中国小学教育现状", as_of_date=AS_OF)
    assert all(item.status == "uncovered" for item in initial_dimension_coverages(ordinary))

    forged = replace(
        ordinary,
        not_applicable_conditions=(
            "edu_teacher_finance|user_explicit:fabricated exclusion",
        ),
    )
    with pytest.raises(ContractValidationError, match="explicit user exclusion"):
        initial_dimension_coverages(forged)


def test_modeling_output_is_strict_and_ignores_deterministic_identity_echoes() -> None:
    brief = build_research_brief("Research global coffee prices", as_of_date=AS_OF)
    with pytest.raises(ContractValidationError, match="unknown modeling"):
        merge_modeling_output(brief, {"prompt": "leak"})
    echoed = merge_modeling_output(
        brief,
        {"profile": "deep_research_modeling", "locale": "xx", "geography": 42},
    )
    assert echoed.profile == "generic_research"
    assert echoed.locale == "en-US"
    assert echoed.geography == "Global"
    with pytest.raises(ContractValidationError, match="no usable 3-to-8"):
        validate_modeling_output(
            {
                "dimensions": [
                    _model_dimension("one", "Question one?", "target one"),
                    _model_dimension("two", "Question two?", "target two"),
                ]
            }
        )


def test_real_provider_replay_keeps_valid_dimensions_and_normalizes_bounded_enums() -> None:
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "deep_research_v5_modeling_provider_response.json")
        .read_text(encoding="utf-8")
    )["modeling_output"]
    validated = validate_modeling_output(payload)
    assert tuple(item.dimension_id for item in validated.dimensions or ()) == (
        "ai_ranking_method",
        "ai_candidate_models",
        "ai_capabilities",
        "ai_pros_cons",
    )
    assert tuple(item.importance for item in validated.dimensions or ()) == (
        "core",
        "core",
        "supporting",
        "supporting",
    )
    assert validated.rejected_dimensions == (
        (4, "importance must be one of ['core', 'supporting']"),
    )

    brief = build_research_brief(
        "请调研现在最强的 AI 大模型前 10，并比较优缺点",
        as_of_date=AS_OF,
        modeling_output=payload,
    )
    assert brief.profile == "technology_intelligence"
    assert brief.locale == "zh-CN"
    assert tuple(item.dimension_id for item in brief.dimensions) == (
        "tech_ranking_method_candidates",
        "tech_pricing_access_pros_cons",
        "tech_capabilities_benchmarks",
        "tech_current_state",
        "tech_recent_changes",
    )
    assert tuple(item.importance for item in brief.dimensions) == (
        "core",
        "core",
        "core",
        "supporting",
        "supporting",
    )


def test_gate_f_three_natural_questions_build_topic_specific_faceted_dimensions() -> None:
    ai_question = (
        "请你帮我调研一下，现在最强的ai 大模型前10是哪几个？"
        "每一个的优缺点都要调研出来"
    )
    statistics_question = (
        "帮我深入调研一下：2024年中国总人口、出生人口、65岁以上人口占比和"
        "城镇化率分别是多少？请优先使用国家统计局原始资料，并把每一个数字的来源说清楚。"
    )
    product_question = (
        "我准备在 iPhone 17 Pro 和小米 15 Ultra 之间选一台，帮我深入调研一下两者的"
        "相机、电池、价格、系统生态和主要缺点，最后告诉我分别适合什么人。"
    )

    ai_intent = detect_research_intent(ai_question, as_of_date=AS_OF)
    assert ai_intent.profile == "technology_intelligence"
    assert {"ranking_top_n", "pros_cons"} <= set(ai_intent.intent_facets)
    ai = build_research_brief(ai_question, as_of_date=AS_OF)
    assert tuple(item.dimension_id for item in ai.dimensions[:2]) == (
        "tech_ranking_method_candidates",
        "tech_pricing_access_pros_cons",
    )
    assert sum(item.importance == "core" for item in ai.dimensions) == 3

    statistics_intent = detect_research_intent(statistics_question, as_of_date=AS_OF)
    assert {
        "official_statistics",
        "exact_fact_lookup",
        "first_party_required",
    } <= set(statistics_intent.intent_facets)
    statistics = build_research_brief(statistics_question, as_of_date=AS_OF)
    assert tuple(item.dimension_id for item in statistics.dimensions) == (
        "stat_total_population",
        "stat_birth_population",
        "stat_age_65_share",
        "stat_urbanization_rate",
    )
    assert all(item.first_party_required for item in statistics.dimensions)

    product_intent = detect_research_intent(product_question, as_of_date=AS_OF)
    assert {"product_comparison", "purchase_recommendation"} <= set(
        product_intent.intent_facets
    )
    product = build_research_brief(product_question, as_of_date=AS_OF)
    assert product.subjects == ("iPhone 17 Pro", "小米 15 Ultra")
    assert tuple(item.dimension_id for item in product.dimensions) == (
        "product_camera",
        "product_battery",
        "product_price",
        "product_ecosystem",
        "product_drawbacks",
        "product_recommendation",
    )
    assert not any(item.dimension_id.startswith("generic_") for item in product.dimensions)


def test_valid_generic_modeling_dimensions_replace_fallback_but_remain_traceable() -> None:
    brief = build_research_brief("Research global coffee prices", as_of_date=AS_OF)
    dimensions = [
        _model_dimension("coffee_supply", "How is supply changing?", "coffee supply"),
        _model_dimension("coffee_demand", "How is demand changing?", "coffee demand"),
        _model_dimension("coffee_outlook", "What is the outlook?", "coffee outlook"),
    ]
    merged = merge_modeling_output(
        brief,
        {
            "profile": "generic_research",
            "locale": "en-US",
            "subjects": ["coffee prices"],
            "expected_decision": "assess price outlook and uncertainty",
            "dimensions": dimensions,
        },
    )
    assert tuple(item.dimension_id for item in merged.dimensions) == (
        "coffee_supply",
        "coffee_demand",
        "coffee_outlook",
    )
    assert merged.subjects == ("coffee prices",)
    coverage = initial_dimension_coverages(merged)
    assert tuple(item.dimension_id for item in coverage) == tuple(
        item.dimension_id for item in merged.dimensions
    )


def test_fixed_profile_modeling_cannot_remove_required_core_dimensions() -> None:
    brief = build_research_brief("调研中国小学教育现状", as_of_date=AS_OF)
    modeled = [
        _model_dimension("edu_current_state", "Current state?", "edu state"),
        _model_dimension("edu_scale_trend", "Scale trend?", "edu scale"),
        _model_dimension("custom_context", "What context helps?", "edu context", importance="supporting"),
    ]
    merged = merge_modeling_output(brief, {"dimensions": modeled})
    assert tuple(item.dimension_id for item in merged.dimensions[:6]) == EDUCATION_DIMENSION_IDS
    assert merged.dimensions[-1].dimension_id == "custom_context"
    assert merged.dimensions[-1].importance == "supporting"


def test_profile_definitions_have_unique_dimension_ids_and_no_shared_query_targets() -> None:
    for profile in ("generic_research", "policy_education", "technology_intelligence"):
        definition = profile_definition(profile)
        ids = [item.dimension_id for item in definition.dimensions]
        targets = [target for item in definition.dimensions for target in item.query_targets]
        assert len(ids) == len(set(ids))
        assert len(targets) == len(set(targets))
        assert all(item.expected_source_types for item in definition.dimensions)
