from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from deskpet.workflows.definitions.deep_research_v5_contracts import (
    DimensionAnalysis,
    DimensionCoverage,
    ResearchBrief,
)
from deskpet.workflows.definitions.deep_research_v5_report import (
    REPORT_QUALITY_RUBRIC_V1_HASH,
    REPORT_QUALITY_RUBRIC_V1_MANIFEST,
    ReportClaim,
    ReportRenderLint,
    ReportSource,
    _round_tenth,
    audit_report_quality,
    lint_rendered_report,
    render_report,
)


FIXTURES = Path(__file__).parent / "fixtures"
AUDITED_AT = "2026-07-16T00:00:00+00:00"


def _cases() -> dict[str, dict[str, object]]:
    payload = json.loads(
        (FIXTURES / "deep_research_v5_report_golden.json").read_text(encoding="utf-8")
    )
    return {item["id"]: item for item in payload["cases"]}


def _load(case_id: str):  # type: ignore[no-untyped-def]
    case = _cases()[case_id]
    return (
        case,
        ResearchBrief.from_json(case["brief"]),
        [DimensionCoverage.from_json(item) for item in case["coverages"]],
        [DimensionAnalysis.from_json(item) for item in case["analyses"]],
        [ReportClaim(**item) for item in case["claims"]],
        [ReportSource(**item) for item in case["sources"]],
    )


def _run(case_id: str):  # type: ignore[no-untyped-def]
    case, brief, coverages, analyses, claims, sources = _load(case_id)
    markdown = render_report(
        brief=brief,
        coverages=coverages,
        analyses=analyses,
        claims=claims,
        sources=sources,
    )
    lint = lint_rendered_report(
        markdown,
        metadata_pseudo_judgment_count=sum(
            item.metadata_pseudo_judgment for item in claims if item.kind == "key_judgment"
        ),
    )
    result = audit_report_quality(
        brief=brief,
        coverages=coverages,
        analyses=analyses,
        claims=claims,
        sources=sources,
        render_lint=lint,
        audited_at=AUDITED_AT,
    )
    return case, markdown, lint, result


def _audit(
    brief: ResearchBrief,
    coverages: list[DimensionCoverage],
    analyses: list[DimensionAnalysis],
    claims: list[ReportClaim],
    sources: list[ReportSource],
    lint: ReportRenderLint,
):
    return audit_report_quality(
        brief=brief,
        coverages=coverages,
        analyses=analyses,
        claims=claims,
        sources=sources,
        render_lint=lint,
        audited_at=AUDITED_AT,
    )


def test_rubric_manifest_hash_and_maxima_are_fixture_locked() -> None:
    fixture = json.loads(
        (FIXTURES / "deep_research_v5_report_rubric.json").read_text(encoding="utf-8")
    )
    assert REPORT_QUALITY_RUBRIC_V1_HASH == fixture["rubric_hash"]
    assert {
        name: definition["max"]
        for name, definition in REPORT_QUALITY_RUBRIC_V1_MANIFEST["components"].items()
    } == fixture["component_maxima"]
    assert fixture["rounding"] == "ROUND_HALF_UP_0.1"


@pytest.mark.parametrize("case_id", ["SC-EDU", "SC-AI"])
def test_golden_reports_lock_markdown_score_and_decision(case_id: str) -> None:
    case, markdown, lint, result = _run(case_id)
    expected = case["expected"]
    components = [
        result.audit.relevance,
        result.audit.coverage,
        result.audit.source_quality,
        result.audit.synthesis_reasoning,
        result.audit.timeliness_uncertainty,
        result.audit.readability,
    ]
    assert hashlib.sha256(markdown.encode("utf-8")).hexdigest() == expected["markdown_sha256"]
    assert components == expected["components"]
    assert result.audit.total_score == expected["total_score"]
    assert result.delivery_status == expected["delivery_status"]
    assert lint.section_order_correct and lint.first_screen_ok and lint.clean_copy_ok


def test_renderer_is_conclusion_first_and_never_leaks_machine_diagnostics() -> None:
    _, markdown, _, _ = _run("SC-EDU")
    positions = [
        markdown.index("## 关键判断"),
        markdown.index("## 分维度分析"),
        markdown.index("## 风险与不确定性"),
        markdown.index("## 来源与方法"),
    ]
    assert positions == sorted(positions)
    assert markdown.count("\n- ", 0, positions[1]) == 3
    for forbidden in (
        "## Coverage",
        "raw diagnostics",
        "evidence_passage_ids",
        "winning_evidence_ids",
        "gap_reasons",
    ):
        assert forbidden not in markdown
    for label in ("**现状：**", "**原因与变化：**", "**影响：**", "**下一步：**"):
        assert label in markdown


def test_adversarial_bad_report_fails_every_render_lint_axis() -> None:
    markdown = """# 深度研究报告

## 关键判断

- 采集了 12 条证据。[S9]

## 风险与不确定性

错误。。

## 分维度分析

## Coverage

raw diagnostics: evidence_passage_ids=[1]

## 来源与方法

- [S1] source — https://example.test

## 关键判断
"""
    lint = lint_rendered_report(markdown, metadata_pseudo_judgment_count=1)
    assert not lint.section_order_correct
    assert not lint.first_screen_ok
    assert not lint.no_internal_diagnostics
    assert not lint.no_duplicate_headings
    assert not lint.no_dangling_citations
    assert not lint.no_obvious_errors


def test_universal_hard_failures_override_a_high_numeric_score() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    _, _, good_lint, baseline = _run("SC-EDU")
    claims[0] = replace(claims[0], supported_fact_refs=())
    sources[0] = replace(sources[0], invalid_page=True, captcha=True)
    bad_lint = replace(good_lint, no_internal_diagnostics=False)
    result = _audit(brief, coverages, analyses, claims, sources, bad_lint)
    assert baseline.audit.total_score >= 80
    assert result.delivery_status == "insufficient_evidence"
    assert {
        "unsupported_key_claim",
        "invalid_or_captcha_citation",
        "internal_diagnostics_leak",
    } <= set(result.audit.hard_failures)
    assert not result.audit.passed


def test_uncovered_core_blocks_completed_but_can_honestly_be_partial() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-AI")
    coverages[2] = replace(
        coverages[2],
        status="uncovered",
        evidence_passage_ids=(),
        winning_evidence_ids=(),
        source_family_ids=(),
        relevance_score=0,
    )
    analyses = analyses[:2]
    claims = [item for item in claims if item.dimension_id != "ai-risk"]
    sources = [item for item in sources if item.dimension_id != "ai-risk"]
    lint = replace(_run("SC-AI")[2], key_judgment_count=3)
    result = _audit(brief, coverages, analyses, claims, sources, lint)
    assert "completed_core_uncovered" in result.audit.hard_failures
    assert result.delivery_status == "partial"
    assert not result.audit.passed


def test_partial_counts_evidence_backed_core_dimensions() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-AI")
    coverages[1] = replace(coverages[1], status="partially_covered")
    coverages[2] = replace(
        coverages[2],
        status="uncovered",
        evidence_passage_ids=(),
        winning_evidence_ids=(),
        source_family_ids=(),
        relevance_score=0,
    )
    analyses = analyses[:2]
    claims = [item for item in claims if item.dimension_id != "ai-risk"]
    sources = [item for item in sources if item.dimension_id != "ai-risk"]
    lint = replace(_run("SC-AI")[2], key_judgment_count=3)
    result = _audit(brief, coverages, analyses, claims, sources, lint)
    assert result.audit.total_score >= 60
    assert result.delivery_status == "partial"


def test_not_applicable_core_is_excluded_from_every_denominator() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    coverages[1] = replace(
        coverages[1],
        status="not_applicable",
        evidence_passage_ids=(),
        winning_evidence_ids=(),
        source_family_ids=(),
        first_party_satisfied=False,
        relevance_score=0,
    )
    analyses = [item for item in analyses if item.dimension_id == "edu-current"]
    claims = [item for item in claims if item.dimension_id == "edu-current"]
    sources = [item for item in sources if item.dimension_id == "edu-current"]
    lint = replace(_run("SC-EDU")[2], key_judgment_count=3)
    result = _audit(brief, coverages, analyses, claims, sources, lint)
    assert result.audit.relevance == 25.0
    assert result.audit.coverage == 25.0
    assert result.audit.source_quality == 20.0
    assert result.delivery_status == "completed"


def test_empty_denominators_are_zero_except_uncertainty_special_case() -> None:
    _, brief, coverages, _, _, _ = _load("SC-EDU")
    coverages[0] = replace(
        coverages[0],
        status="uncovered",
        evidence_passage_ids=(),
        winning_evidence_ids=(),
        source_family_ids=(),
        first_party_satisfied=False,
        relevance_score=0,
    )
    coverages[1] = replace(
        coverages[1],
        status="not_applicable",
        evidence_passage_ids=(),
        winning_evidence_ids=(),
        source_family_ids=(),
        first_party_satisfied=False,
        relevance_score=0,
    )
    lint = ReportRenderLint(True, 0, True, True, True, True, True, True)
    result = _audit(brief, coverages, [], [], [], lint)
    assert result.audit.relevance == 0
    assert result.audit.coverage == 0
    assert result.audit.source_quality == 0
    assert result.audit.synthesis_reasoning == 0
    assert result.audit.timeliness_uncertainty == 5.0


def test_decimal_round_half_up_is_used_at_point_zero_five() -> None:
    assert _round_tenth(Decimal("1.25"), 5) == Decimal("1.3")
    assert _round_tenth(Decimal("1.24"), 5) == Decimal("1.2")
    assert _round_tenth(Decimal("9.95"), 5) == Decimal("5.0")


def test_exact_score_80_is_completed_when_all_core_dimensions_are_covered() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    coverages = [
        replace(item, first_party_satisfied=False)
        if item.status != "not_applicable"
        else item
        for item in coverages
    ]
    sources = [
        replace(
            item,
            first_party=False,
            time_sensitive=True,
            within_time_window=False,
            explicit_no_new_evidence=False,
        )
        for item in sources
    ]
    claims.extend(
        [
            ReportClaim("edu-fc1", "edu-current", "未来执行仍可能变化。", "forecast", ("edu-e1",), ("edu-p1",)),
            ReportClaim("edu-fc2", "edu-next", "未来成效仍可能变化。", "forecast", ("edu-e3",), ("edu-p3",)),
        ]
    )
    result = _audit(brief, coverages, analyses, claims, sources, _run("SC-EDU")[2])
    assert result.audit.total_score == 80.0
    assert result.delivery_status == "completed"
    assert result.audit.passed


def test_duplicate_citations_and_same_family_sources_cannot_raise_score() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    lint = _run("SC-EDU")[2]
    baseline = _audit(brief, coverages, analyses, claims, sources, lint)
    duplicates = [
        replace(sources[0], source_id="edu-e1-copy"),
        replace(sources[2], source_id="edu-e3-copy"),
    ]
    coverages[0] = replace(
        coverages[0], winning_evidence_ids=coverages[0].winning_evidence_ids + ("edu-e1-copy",)
    )
    coverages[1] = replace(
        coverages[1], winning_evidence_ids=coverages[1].winning_evidence_ids + ("edu-e3-copy",)
    )
    claims[0] = replace(claims[0], source_ids=claims[0].source_ids + ("edu-e1-copy",))
    stacked = _audit(brief, coverages, analyses, claims, sources + duplicates, lint)
    assert stacked.audit.total_score == baseline.audit.total_score
    assert stacked.audit.source_quality == baseline.audit.source_quality


def test_stacked_body_claims_cannot_substitute_for_a_missing_reasoning_field() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    lint = _run("SC-EDU")[2]
    without_impact = [item for item in claims if item.claim_id != "edu-c9"]
    baseline = _audit(brief, coverages, analyses, without_impact, sources, lint)
    filler = [
        ReportClaim(
            f"filler-{index}",
            "edu-next",
            "重复背景材料不会替代影响分析。",
            "driver_change",
            ("edu-e3",),
            ("edu-p3",),
        )
        for index in range(20)
    ]
    stacked = _audit(brief, coverages, analyses, without_impact + filler, sources, lint)
    assert stacked.audit.synthesis_reasoning == baseline.audit.synthesis_reasoning == 12.5
    assert stacked.audit.total_score == baseline.audit.total_score


def test_diagnostic_leak_alone_forces_insufficient_delivery() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    lint = replace(_run("SC-EDU")[2], no_internal_diagnostics=False)
    result = _audit(brief, coverages, analyses, claims, sources, lint)
    assert result.audit.total_score > 80
    assert "internal_diagnostics_leak" in result.audit.hard_failures
    assert result.delivery_status == "insufficient_evidence"


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        ("official-replacement", "secondary_replaces_available_official"),
        ("dimension-mismatch", "citation_dimension_mismatch"),
    ],
)
def test_source_integrity_hard_failures_precede_high_score(
    mutation: str, failure: str
) -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    if mutation == "official-replacement":
        sources[0] = replace(sources[0], secondary_replaces_available_official=True)
    else:
        claims[0] = replace(claims[0], source_ids=("edu-e3",))
    result = _audit(brief, coverages, analyses, claims, sources, _run("SC-EDU")[2])
    assert result.audit.total_score >= 80
    assert failure in result.audit.hard_failures
    assert result.delivery_status == "insufficient_evidence"


def test_forged_intent_token_metadata_cannot_raise_relevance() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    claims = [
        replace(item, intent_tokens=("not-in-user-intent",))
        if item.kind == "key_judgment"
        else item
        for item in claims
    ]
    result = _audit(
        brief,
        coverages,
        analyses,
        claims,
        sources,
        _run("SC-EDU")[2],
    )
    assert result.audit.relevance == 15.0


def test_renderer_does_not_claim_no_uncertainty_when_evidence_is_partial() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-AI")
    claims = [
        item
        for item in claims
        if item.kind not in {"uncertainty", "counterevidence", "forecast"}
    ]
    markdown = render_report(
        brief=brief,
        coverages=coverages,
        analyses=analyses,
        claims=claims,
        sources=sources,
    )
    assert "部分维度证据仍不完整" in markdown
    assert "当前未识别到需要单列" not in markdown


def test_analysis_inference_must_be_explicitly_marked_to_earn_reasoning_score() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-AI")
    analyses[2] = replace(
        analyses[2], inference_claim_ids=analyses[2].inference_claim_ids + ("ai-c8",)
    )
    lint = _run("SC-AI")[2]
    unmarked = _audit(brief, coverages, analyses, claims, sources, lint)
    claims = [
        replace(item, is_inference=True) if item.claim_id == "ai-c8" else item
        for item in claims
    ]
    marked = _audit(brief, coverages, analyses, claims, sources, lint)
    assert unmarked.audit.synthesis_reasoning == 13.3
    assert marked.audit.synthesis_reasoning == 15.0


def test_unknown_key_claim_source_is_unsupported_and_renderer_fails_closed() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    claims[0] = replace(claims[0], source_ids=("missing-source",))
    with pytest.raises(Exception, match="unknown sources"):
        render_report(
            brief=brief,
            coverages=coverages,
            analyses=analyses,
            claims=claims,
            sources=sources,
        )
    result = _audit(brief, coverages, analyses, claims, sources, _run("SC-EDU")[2])
    assert "unsupported_key_claim" in result.audit.hard_failures
    assert result.delivery_status == "insufficient_evidence"


@pytest.mark.parametrize("key_count", [2, 8])
def test_renderer_enforces_three_to_seven_key_judgments(key_count: int) -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    keys = [item for item in claims if item.kind == "key_judgment"]
    non_keys = [item for item in claims if item.kind != "key_judgment"]
    if key_count == 2:
        keys = keys[:2]
    else:
        keys.extend(
            replace(keys[0], claim_id=f"extra-key-{index}") for index in range(5)
        )
    with pytest.raises(Exception, match="3 to 7 key judgments"):
        render_report(
            brief=brief,
            coverages=coverages,
            analyses=analyses,
            claims=keys + non_keys,
            sources=sources,
        )


def test_renderer_refuses_metadata_pseudo_judgment() -> None:
    _, brief, coverages, analyses, claims, sources = _load("SC-EDU")
    claims[0] = replace(claims[0], metadata_pseudo_judgment=True)
    with pytest.raises(Exception, match="metadata cannot be rendered"):
        render_report(
            brief=brief,
            coverages=coverages,
            analyses=analyses,
            claims=claims,
            sources=sources,
        )
