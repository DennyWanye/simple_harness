from __future__ import annotations

from deskpet.workflows.definitions.deep_research_v6_exact_fact import (
    ScalarEvidenceRequest,
    extract_scalar_evidence,
)
from deskpet.workflows.definitions.deep_research_v6_exact_report import (
    CitationView,
    assess_and_render_exact_facts,
)


def _requests() -> tuple[ScalarEvidenceRequest, ...]:
    return (
        ScalarEvidenceRequest(
            requirement_id="req_population",
            definition="year_end_total_population",
            canonical_unit="person",
            time_label="2024年末",
        ),
        ScalarEvidenceRequest(
            requirement_id="req_births",
            definition="births_during_period",
            canonical_unit="person",
            time_label="2024年全年",
        ),
    )


def test_completed_exact_report_has_nearby_citations_and_two_claims() -> None:
    body = "2024年末全国人口140828万人。2024年全年出生人口954万人。"
    evidence = extract_scalar_evidence(
        body=body,
        body_ref="sha256:" + "d" * 64,
        page_id="page_nbs",
        requests=_requests(),
    )
    result = assess_and_render_exact_facts(
        requests=_requests(),
        evidence=evidence,
        citations={
            "page_nbs": CitationView(
                title="国家统计局：2024年国民经济和社会发展统计公报",
                url="https://www.stats.gov.cn/example.html",
            )
        },
    )

    assert result.answer_status == "completed"
    assert result.missing_requirement_ids == ()
    assert len(result.claims) == 2
    assert "2024年末全国人口：140828万人" in result.final_assistant
    assert "2024年全年出生人口：954万人" in result.final_assistant
    assert result.final_assistant.count("国家统计局") == 2
    assert "https://www.stats.gov.cn/example.html" in result.final_assistant


def test_missing_required_scalar_can_never_be_completed() -> None:
    evidence = extract_scalar_evidence(
        body="2024年末全国人口140828万人。",
        body_ref="sha256:" + "e" * 64,
        page_id="page_nbs",
        requests=_requests(),
    )
    result = assess_and_render_exact_facts(
        requests=_requests(),
        evidence=evidence,
        citations={"page_nbs": CitationView(title="国家统计局", url="https://stats.gov.cn/x")},
    )

    assert result.answer_status == "partial"
    assert result.missing_requirement_ids == ("req_births",)
    assert "未找到满足约束的权威证据" in result.final_assistant


def test_zero_evidence_is_insufficient_and_has_no_report() -> None:
    result = assess_and_render_exact_facts(
        requests=_requests(),
        evidence=(),
        citations={},
    )

    assert result.answer_status == "insufficient_evidence"
    assert result.report_markdown is None
    assert set(result.missing_requirement_ids) == {"req_population", "req_births"}
