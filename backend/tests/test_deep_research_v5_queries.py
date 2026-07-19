from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from deskpet.retrieval.query_terms import (
    extract_query_terms,
    query_fingerprint,
    query_language,
    query_term_relevance,
)
from deskpet.workflows.definitions.deep_research_v5_contracts import (
    ContractValidationError,
    DimensionCoverage,
)
from deskpet.workflows.definitions.deep_research_v5_policy import (
    build_research_brief,
    initial_dimension_coverages,
)
from deskpet.workflows.definitions.deep_research_v5_queries import (
    QUERY_FAMILIES,
    DimensionQuery,
    build_dimension_queries,
    build_gap_query_plan,
    build_initial_query_plan,
)


FIXTURE = Path(__file__).parent / "fixtures" / "deep_research_v5_education_queries.json"
AS_OF = date(2026, 7, 16)


def _education_brief():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return build_research_brief(payload["question"], as_of_date=AS_OF)


def test_chinese_terms_use_domain_dictionary_ngrams_and_numeric_entities() -> None:
    terms = extract_query_terms("2024年小学教育现状：课后服务覆盖率达到12.5%")
    assert "教育现状" in terms
    assert "课后服务" in terms
    assert "2024年" in terms
    assert "12.5%" in terms
    assert "小学" in terms
    assert query_language("中国小学教育") == "zh"


def test_english_terms_are_tokenized_without_stopword_only_noise() -> None:
    terms = extract_query_terms(
        "Compare the latest teacher workforce and education finance statistics"
    )
    assert "compare" in terms
    assert "teacher" in terms
    assert "workforce" in terms
    assert "education" in terms
    assert "the" not in terms
    assert query_language("education finance") == "en"


def test_chinese_relevance_is_nonzero_and_beats_unrelated_candidate() -> None:
    query = "中国 小学教育 双减 课后服务 学生负担 2024 统计"
    relevant = "2024年教育部评估双减政策、课后服务和小学生负担变化。"
    unrelated = "海洋气象部门发布远洋航运台风路径预报。"
    relevant_score = query_term_relevance(query, relevant)
    assert relevant_score > 0.0
    assert relevant_score > query_term_relevance(query, unrelated)


def test_education_initial_plan_is_bounded_first_party_probe_and_full_family_is_available() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected_axis = {
        item["dimension_id"]: item["comparison_axis"]
        for item in fixture["dimensions"]
    }
    queries = build_initial_query_plan(_education_brief())

    assert 3 <= len(queries) <= 5
    assert len({item.fingerprint for item in queries}) == len(queries)
    assert all(item.query_family == "official" for item in queries)
    assert all(item.source_target == "official_government_education" for item in queries)
    expected_fields = {
        "dimension_id",
        "query_family",
        "comparison_axis",
        "source_target",
        "query",
        "freshness_window",
    }
    brief = _education_brief()
    for dimension in brief.dimensions:
        dimension_id = dimension.dimension_id
        dimension_queries = list(build_dimension_queries(brief, dimension))
        assert tuple(item.query_family for item in dimension_queries) == QUERY_FAMILIES
        assert {item.query_family for item in dimension_queries} == set(QUERY_FAMILIES)
        comparison = next(
            item for item in dimension_queries if item.query_family == "comparison"
        )
        assert comparison.comparison_axis == expected_axis[dimension_id]
        assert all(set(item.to_json()) == expected_fields for item in dimension_queries)
        assert all(item.comparison_axis == "none" for item in dimension_queries[:3])

        official = next(item for item in dimension_queries if item.query_family == "official")
        assert "site:moe.gov.cn" in official.query
        assert "site:gov.cn" in official.query
        temporal = next(
            item for item in dimension_queries if item.query_family == "temporal_statistics"
        )
        assert "统计" in temporal.query
        assert "2026" in temporal.query


def test_each_education_dimension_query_has_nonzero_relevance_to_fixture() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    brief = _education_brief()
    queries = tuple(
        query
        for dimension in brief.dimensions
        for query in build_dimension_queries(brief, dimension)
    )
    general_by_dimension = {
        item.dimension_id: item
        for item in queries
        if item.query_family == "general"
    }
    for expected in fixture["dimensions"]:
        query = general_by_dimension[expected["dimension_id"]]
        assert query_term_relevance(query.query, expected["relevant_text"]) > 0.0


def test_gate_f_statistics_probe_preserves_requested_year_and_first_party_budget() -> None:
    question = (
        "帮我深入调研一下：2024年中国总人口、出生人口、65岁以上人口占比和"
        "城镇化率分别是多少？请优先使用国家统计局原始资料。"
    )
    brief = build_research_brief(question, as_of_date=AS_OF)
    queries = build_initial_query_plan(brief)
    assert len(queries) == 4
    assert all(item.query_family == "official" for item in queries)
    assert all("site:stats.gov.cn" in item.query for item in queries)
    assert all("2024" in item.query for item in queries)
    assert all("2026" not in item.query for item in queries)
    for query in queries:
        dimension = next(
            item for item in brief.dimensions if item.dimension_id == query.dimension_id
        )
        assert len(query.query) <= 240
        assert any(subject in query.query for subject in brief.subjects[:2])
        assert dimension.query_targets[0] in query.query


def test_modeled_chinese_statistics_sources_still_lock_to_stats_gov_cn() -> None:
    question = (
        "帮我深入调研一下：2024年中国总人口、出生人口、65岁以上人口占比和"
        "城镇化率分别是多少？请优先使用国家统计局原始资料。"
    )
    brief = build_research_brief(question, as_of_date=AS_OF)
    dimension = replace(
        brief.dimensions[0],
        expected_source_types=("国家统计局年度统计公报", "国家统计局统计数据表"),
    )

    official = next(
        item
        for item in build_dimension_queries(brief, dimension)
        if item.query_family == "official"
    )

    assert "site:stats.gov.cn" in official.query
    assert "2024" in official.query
    assert len(official.query) <= 240


def test_gate_f_top_n_probe_prioritizes_requested_ranking_and_tradeoff_facets() -> None:
    question = "请你帮我调研一下，现在最强的ai大模型前10是哪几个？每一个的优缺点都要调研出来。"
    brief = build_research_brief(question, as_of_date=AS_OF)

    queries = build_initial_query_plan(brief)

    assert 3 <= len(queries) <= 5
    assert [item.dimension_id for item in queries[:2]] == [
        "tech_ranking_method_candidates",
        "tech_pricing_access_pros_cons",
    ]


def test_gate_f_product_probe_uses_pairwise_discovery_before_official_rescue() -> None:
    question = (
        "我准备在 iPhone 17 Pro 和小米 15 Ultra 之间选一台，帮我深入调研一下"
        "两者的相机、电池、价格、系统生态和主要缺点，最后告诉我分别适合什么人。"
    )
    brief = build_research_brief(question, as_of_date=AS_OF)
    product_ids = (
        "camera_capability",
        "battery_and_charging",
        "price_and_value",
        "system_ecosystem",
        "major_drawbacks_and_risks",
    )
    brief = replace(
        brief,
        subjects=("iPhone 17 Pro", "小米 15 Ultra"),
        dimensions=tuple(
            replace(
                dimension,
                dimension_id=product_ids[index],
                first_party_required=True,
            )
            for index, dimension in enumerate(brief.dimensions[:5])
        ),
    )

    queries = build_initial_query_plan(brief)

    assert len(queries) == 5
    assert all(item.query_family == "general" for item in queries)
    assert all("iPhone 17 Pro" in item.query for item in queries)
    assert all("小米 15 Ultra" in item.query for item in queries)
    assert all("site:" not in item.query for item in queries)
    assert all(len(item.query) <= 240 for item in queries)


def test_query_contract_rejects_unknown_fields_and_invalid_axis() -> None:
    query = build_initial_query_plan(_education_brief())[0]
    assert DimensionQuery.from_json(query.to_json()) == query
    payload = query.to_json()
    payload["raw_prompt"] = "must not persist"
    with pytest.raises(ContractValidationError, match="fields mismatch"):
        DimensionQuery.from_json(payload)
    with pytest.raises(ContractValidationError, match="requires an axis"):
        DimensionQuery(
            dimension_id="dimension-1",
            query_family="comparison",
            comparison_axis="none",
            source_target="cross_source_comparison",
            query="compare objects",
            freshness_window="2025-01-01..2026-01-01",
        )


def test_query_fingerprint_normalizes_case_and_whitespace() -> None:
    fields = {
        "dimension_id": "EDU_CURRENT_STATE",
        "query_family": "GENERAL",
        "comparison_axis": "NONE",
        "source_target": "BROAD_WEB",
        "query": "  China   Primary Education  ",
        "freshness_window": "2021-01-01..2026-01-01",
    }
    normalized = {key: value.casefold().strip() for key, value in fields.items()}
    assert query_fingerprint(**fields) == query_fingerprint(**normalized)


def test_gap_rescue_only_targets_uncovered_or_partial_and_skips_executed_work() -> None:
    brief = _education_brief()
    coverage = list(initial_dimension_coverages(brief))
    coverage[0] = DimensionCoverage(
        dimension_id=coverage[0].dimension_id,
        status="covered",
        evidence_passage_ids=("passage-1",),
        winning_evidence_ids=("passage-1",),
        source_family_ids=("family-1",),
        first_party_satisfied=True,
        relevance_score=0.9,
    )
    coverage[1] = replace(coverage[1], status="partially_covered")
    coverage[2] = replace(
        coverage[2],
        status="not_applicable",
        gap_reasons=("user_explicit_exclusion:test",),
    )

    initial_gap = build_gap_query_plan(brief, coverage)
    assert coverage[0].dimension_id not in {item.dimension_id for item in initial_gap}
    assert coverage[2].dimension_id not in {item.dimension_id for item in initial_gap}
    assert coverage[1].dimension_id in {item.dimension_id for item in initial_gap}

    executed = next(
        item
        for item in initial_gap
        if item.dimension_id == coverage[1].dimension_id
        and item.query_family == "general"
    )
    filtered = build_gap_query_plan(
        brief,
        coverage,
        executed_query_fingerprints=(executed.fingerprint,),
        executed_source_families={
            coverage[1].dimension_id: ("official_government_education",),
        },
    )
    remaining_for_partial = [
        item for item in filtered if item.dimension_id == coverage[1].dimension_id
    ]
    assert all(item.fingerprint != executed.fingerprint for item in filtered)
    assert {item.query_family for item in remaining_for_partial} == {
        "temporal_statistics",
        "comparison",
    }


def test_gap_rescue_does_not_infer_missing_coverage_as_a_gap() -> None:
    brief = _education_brief()
    only_one = initial_dimension_coverages(brief)[:1]
    queries = build_gap_query_plan(brief, only_one)
    assert {item.dimension_id for item in queries} == {only_one[0].dimension_id}
