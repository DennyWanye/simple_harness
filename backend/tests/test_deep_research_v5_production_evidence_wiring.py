from __future__ import annotations

from pathlib import Path
from decimal import Decimal
from datetime import date

import pytest

from deskpet.workflows.adapters.deep_research_v5_evidence_runtime import (
    EvidenceReadinessV1,
    candidate_from_document,
    classify_source,
    deterministic_relevance,
    evaluate_runtime_evidence,
    select_dimension_fair_rows,
)
from deskpet.workflows.definitions.deep_research_v5_contracts import (
    ResearchBrief,
    ResearchDimension,
)
from deskpet.workflows.definitions.deep_research_v5_policy import build_research_brief
from deskpet.workflows.definitions.deep_research_v5_queries import build_dimension_queries
from deskpet.workflows.definitions.deep_research_v5_evidence import _normalized_text, admit_passage


def _brief() -> ResearchBrief:
    return ResearchBrief(
        brief_id="brief-production-evidence",
        user_question="How are teacher allocation and education finance changing?",
        profile="policy_education",
        as_of_date="2026-07-16",
        locale="en",
        geography="China",
        subjects=("teacher allocation", "education finance"),
        expected_decision="Compare current allocation and finance evidence",
        dimensions=(
            ResearchDimension(
                dimension_id="edu_teacher_finance",
                question="teacher workforce allocation and education finance expenditure",
                importance="core",
                expected_source_types=("official_statistic", "government_document"),
                query_targets=("teacher workforce", "education finance", "expenditure"),
                first_party_required=False,
            ),
        ),
    )


def _document(
    *,
    url: str,
    body: str,
    flags: tuple[str, ...] = (),
    content_hash: str = "a" * 64,
    title: str = "Teacher workforce allocation and education finance expenditure statistics",
) -> dict:
    return {
        "stable_id": content_hash,
        "url": url,
        "canonical_url": url,
        "title": title,
        "text": body,
        "published_at": "2026-05-01",
        "source_kind": "web",
        "content_hash": content_hash,
        "quality_flags": list(flags),
    }


def _candidate(*, url: str, body: str, flags: tuple[str, ...] = (), content_hash: str):
    dimension = _brief().dimensions[0]
    return candidate_from_document(
        dimension_id=dimension.dimension_id,
        dimension_text=" ".join((dimension.question, *dimension.query_targets)),
        query="teacher workforce allocation education finance expenditure",
        document=_document(url=url, body=body, flags=flags, content_hash=content_hash),
    )


def _relevant_body(marker: str) -> str:
    sentence = (
        "Teacher workforce allocation and education finance expenditure statistics "
        f"show regional differences in {marker}. "
    )
    return sentence * 8


def test_captcha_and_unrelated_pages_cannot_mark_core_covered() -> None:
    captcha = _candidate(
        url="https://stats.gov.cn/captcha",
        body=_relevant_body("the official series"),
        flags=("captcha",),
        content_hash="a" * 64,
    )
    unrelated = _candidate(
        url="https://example.com/cooking",
        body=("A recipe discusses onions, soup, and kitchen equipment. " * 10),
        content_hash="b" * 64,
    )

    result = evaluate_runtime_evidence(brief=_brief(), candidates=(captcha, unrelated))

    assert result["coverages"][0]["status"] == "uncovered"
    assert result["synthesis_route"] == "insufficient_summary"
    assert all(not item["admitted"] for item in result["evidence_candidates"])
    assert result["rejection_reason_counts"]["invalid_reason:captcha"] == 1
    assert sum(result["rejection_reason_counts"].values()) == 2
    assert {
        "body_span_missing",
        "dimension_relevance_below_threshold",
    } & set(result["rejection_reason_counts"])
    assert all(item["rejection_reason"] for item in result["evidence_candidates"])
    assert len(result["admission_decisions"]) == 2


def test_exact_duplicate_pages_do_not_fake_two_family_core_coverage() -> None:
    body = _relevant_body("the annual bulletin")
    first = _candidate(
        url="https://example.com/report?utm_source=a",
        body=body,
        content_hash="c" * 64,
    )
    duplicate = _candidate(
        url="https://mirror.example.net/copied-report",
        body=body,
        content_hash="c" * 64,
    )

    result = evaluate_runtime_evidence(brief=_brief(), candidates=(first, duplicate))

    assert result["coverages"][0]["status"] == "partially_covered"
    assert len(result["source_families"]) == 1
    assert result["synthesis_route"] != "full_synthesis"


def test_gap_rescue_merges_new_admitted_family_and_raises_coverage() -> None:
    first = _candidate(
        url="https://source-a.example/report",
        body=_relevant_body("source A"),
        content_hash="d" * 64,
    )
    before = evaluate_runtime_evidence(brief=_brief(), candidates=(first,))
    rescue = _candidate(
        url="https://source-b.example/independent-report",
        body=_relevant_body("independent source B"),
        content_hash="e" * 64,
    )
    merged = evaluate_runtime_evidence(brief=_brief(), candidates=(first, rescue))

    assert before["coverages"][0]["status"] == "partially_covered"
    assert merged["coverages"][0]["status"] == "covered"
    assert merged["readiness"] > before["readiness"]
    assert merged["synthesis_route"] == "full_synthesis"


def test_readiness_v1_route_boundaries_are_inclusive_and_locked() -> None:
    full = EvidenceReadinessV1.evaluate(
        coverage_ratio=Decimal("1"),
        first_party_ratio=Decimal("0"),
        diversity_ratio=Decimal("1"),
        admission_ratio=Decimal("1"),
        supported_core_ratio=Decimal("1"),
        all_core_covered=True,
        supported_have_span=True,
    )
    partial = EvidenceReadinessV1.evaluate(
        coverage_ratio=Decimal("0.5"),
        first_party_ratio=Decimal("0"),
        diversity_ratio=Decimal("0"),
        admission_ratio=Decimal("2") / Decimal("3"),
        supported_core_ratio=Decimal("0.50"),
        all_core_covered=False,
        supported_have_span=True,
    )

    assert EvidenceReadinessV1.policy_id == "EvidenceReadinessV1"
    assert len(EvidenceReadinessV1.policy_hash) == 64
    assert full == {
        "readiness": 80.0,
        "synthesis_route": "full_synthesis",
        "readiness_components": {
            "weighted_coverage_ratio": 1.0,
            "first_party_core_ratio": 0.0,
            "core_source_diversity_ratio": 1.0,
            "admission_ratio": 1.0,
            "supported_core_ratio": 1.0,
        },
    }
    assert partial["readiness"] == 35.0
    assert partial["synthesis_route"] == "partial_synthesis"


def test_fetch_pool_round_robins_dimensions_before_filling_shared_budget() -> None:
    rows = tuple(
        {"dimension_id": dimension_id, "rank": rank}
        for dimension_id in ("scale", "teachers", "double_reduction", "next_plan")
        for rank in range(10)
    )

    selected = select_dimension_fair_rows(
        rows,
        ("scale", "teachers", "double_reduction", "next_plan"),
        limit=10,
    )

    assert [(row["dimension_id"], row["rank"]) for row in selected] == [
        ("scale", 0),
        ("teachers", 0),
        ("double_reduction", 0),
        ("next_plan", 0),
        ("scale", 1),
        ("teachers", 1),
        ("double_reduction", 1),
        ("next_plan", 1),
        ("scale", 2),
        ("teachers", 2),
    ]
    assert {row["dimension_id"] for row in selected} == {
        "scale",
        "teachers",
        "double_reduction",
        "next_plan",
    }


def test_exact_fact_relevance_preserves_year_anchor_and_vendor_domains_are_first_party() -> None:
    kwargs = {
        "dimension_text": "2024年中国总人口是多少 国家统计局官方统计",
        "query": "2024 中国 总人口 site:stats.gov.cn",
        "title": "中国人口统计公报",
        "subjects": ("中国人口",),
        "required_anchors": ("2024",),
    }
    current = deterministic_relevance(
        **kwargs,
        body=("2024年中国总人口及人口结构由国家统计局发布。" * 8),
    )
    stale = deterministic_relevance(
        **kwargs,
        body=("2023年中国总人口及人口结构由国家统计局发布。" * 8),
    )
    assert current > stale
    assert classify_source(url="https://www.apple.com/iphone-17-pro/specs/", source_kind="web")[1] == "first_party"
    assert classify_source(url="https://www.mi.com/prod/xiaomi-15-ultra", source_kind="web")[1] == "first_party"
    assert classify_source(url="https://openai.com/index/model-release/", source_kind="web")[1] == "first_party"


@pytest.mark.parametrize(
    ("question", "url"),
    [
        (
            "请你帮我调研一下，现在最强的ai 大模型前10是哪几个？每一个的优缺点都要调研出来",
            "https://openai.com/index/model-release/",
        ),
        (
            "帮我深入调研一下：2024年中国总人口、出生人口、65岁以上人口占比和城镇化率分别是多少？请优先使用国家统计局原始资料。",
            "https://www.stats.gov.cn/sj/zxfb/202502/t20250228_1958817.html",
        ),
        (
            "我准备在 iPhone 17 Pro 和小米 15 Ultra 之间选一台，帮我深入调研一下两者的相机、电池、价格、系统生态和主要缺点，最后告诉我分别适合什么人。",
            "https://www.apple.com/iphone-17-pro/specs/",
        ),
    ],
)
def test_gate_f_layered_fixture_admits_relevant_first_party_passage(
    question: str,
    url: str,
) -> None:
    brief = build_research_brief(question, as_of_date=date(2026, 7, 17))
    dimension = brief.dimensions[0]
    body = "。".join(
        [
            question,
            dimension.question,
            *brief.subjects,
            *dimension.query_targets,
        ]
        * 8
    )
    candidate = candidate_from_document(
        dimension_id=dimension.dimension_id,
        dimension_text=" ".join(
            (brief.user_question, dimension.question, *brief.subjects, *dimension.query_targets)
        ),
        query=" ".join((question, dimension.question, *dimension.query_targets)),
        document=_document(
            url=url,
            body=body,
            content_hash=(dimension.dimension_id.encode().hex() + "0" * 64)[:64],
        ),
        subjects=brief.subjects,
        required_anchors=("2024",) if "2024" in question else (),
    )
    result = evaluate_runtime_evidence(brief=brief, candidates=(candidate,))
    assert len([item for item in result["evidence_candidates"] if item["admitted"]]) == 1
    if "国家统计局" in question:
        assert result["evidence_candidates"][0]["source_tier"] == "first_party"


@pytest.mark.parametrize(
    ("question", "dimension_id", "url", "title", "body"),
    [
        (
            "可以帮我调研一下，现在 AI 相关的最新最有价值的技术相关的信息吗？",
            "tech_capabilities_benchmarks",
            "https://openai.com/index/model-release/",
            "最新人工智能推理模型发布与能力评测",
            "新模型在代码、数学和智能体基准测试中能力提升，支持企业采用，同时仍存在成本、延迟和安全限制。",
        ),
        (
            "帮我深入调研一下：2024年中国总人口、出生人口、65岁以上人口占比和城镇化率分别是多少？请优先使用国家统计局原始资料。",
            "stat_total_population",
            "https://www.stats.gov.cn/sj/zxfb/202502/t20250228_1958817.html",
            "中华人民共和国2024年国民经济和社会发展统计公报",
            "2024年末全国人口140828万人，全年出生人口954万人，65周岁及以上人口占比15.6%，常住人口城镇化率67.00%。",
        ),
        (
            "我准备在 iPhone 17 Pro 和小米 15 Ultra 之间选一台，帮我深入调研一下两者的相机、电池、价格、系统生态和主要缺点，最后告诉我分别适合什么人。",
            "product_camera",
            "https://www.apple.com/iphone-17-pro/specs/",
            "iPhone 17 Pro 技术规格与专业相机系统",
            "iPhone 17 Pro 配备专业相机系统，列出主摄、长焦、超广角、视频录制、显示屏、电池续航和连接规格。",
        ),
    ],
)
def test_gate_f_realistic_first_party_excerpt_can_cross_locked_admission_gate(
    question: str,
    dimension_id: str,
    url: str,
    title: str,
    body: str,
) -> None:
    brief = build_research_brief(question, as_of_date=date(2026, 7, 17))
    dimension = next(item for item in brief.dimensions if item.dimension_id == dimension_id)
    query = build_dimension_queries(brief, dimension)[0].query
    candidate = candidate_from_document(
        dimension_id=dimension.dimension_id,
        dimension_text=" ".join((dimension.question, *dimension.query_targets)),
        query=query,
        document=_document(url=url, title=title, body=(body + " ") * 8),
        subjects=brief.subjects,
        required_anchors=("2024",) if "2024" in question else (),
    )

    result = evaluate_runtime_evidence(brief=brief, candidates=(candidate,))

    assert candidate.relevance >= 0.55
    assert candidate.span_text
    assert _normalized_text(candidate.span_text or "") in _normalized_text(candidate.body_text), (
        repr(candidate.span_text),
        repr(candidate.body_text[:160]),
    )
    assert admit_passage(candidate).reason == "admitted"
    assert result["evidence_candidates"][0]["rejection_reason"] is None
    assert result["evidence_candidates"][0]["admitted"] is True


def test_cross_language_concepts_do_not_admit_unrelated_first_party_page() -> None:
    question = (
        "帮我深入调研一下：2024年中国总人口、出生人口、65岁以上人口占比和"
        "城镇化率分别是多少？请优先使用国家统计局原始资料。"
    )
    brief = build_research_brief(question, as_of_date=date(2026, 7, 17))
    dimension = next(
        item for item in brief.dimensions if item.dimension_id == "stat_total_population"
    )
    candidate = candidate_from_document(
        dimension_id=dimension.dimension_id,
        dimension_text=" ".join((dimension.question, *dimension.query_targets)),
        query=build_dimension_queries(brief, dimension)[0].query,
        document=_document(
            url="https://www.stats.gov.cn/weather-bulletin.html",
            title="2024年海洋气象观测统计公报",
            body=("2024年海洋气象观测记录包括台风路径、海温、降水和远洋航运预报。" + " ") * 12,
        ),
        subjects=brief.subjects,
        required_anchors=("2024",),
    )

    assert candidate.relevance < 0.55
    decision = admit_passage(candidate)
    assert decision.accepted is False
    assert decision.reason in {"body_span_missing", "dimension_relevance_below_threshold"}


def test_main_v5_search_uses_one_dimension_batch_and_gap_fetch_admission() -> None:
    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    start = source.index("async def _deep_v5_context_factory")
    end = source.index("return WorkflowContext(", start)
    factory = source[start:end]

    assert "gateway.search_dimension_batch(" in factory
    assert "DimensionSearchRequest(" in factory
    assert "from deskpet.retrieval.query_terms import extract_query_terms" in factory
    assert "semantic_terms = extract_query_terms(semantic_context)" in factory
    assert "min_results_per_core=1" in factory
    assert "gateway.search(SearchRequest(" not in factory
    assert '"dimension_id": dimension_result.dimension_id' in factory
    assert '"query_fingerprint": fingerprint' in factory
    assert 'payload.get("_stage") == "gap_work"' in factory
    assert '"budget": batch.to_dict()["budget"]' in factory
    assert '"budget": batch.budget' not in factory
    assert "candidate_from_document(" in factory
    assert "evaluate_runtime_evidence(" in factory
    assert "select_dimension_fair_rows(" in factory
    assert "rows[:24]" not in factory
