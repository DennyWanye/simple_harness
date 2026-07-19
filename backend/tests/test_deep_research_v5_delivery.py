from __future__ import annotations

import json
from pathlib import Path

import pytest

from deskpet.workflows.definitions.deep_research_v5_contracts import (
    DimensionCoverage,
    ResearchBrief,
)
from deskpet.workflows.definitions.deep_research_v5_delivery import (
    ReportDraftError,
    apply_atomic_repair,
    build_extractively_grounded_draft,
    evaluate_structured_report,
)


FIXTURE = Path(__file__).parent / "fixtures" / "deep_research_v5_report_golden.json"


def _case():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    case = next(item for item in payload["cases"] if item["id"] == "SC-EDU")
    family_by_source = {item["source_id"]: item for item in case["sources"]}
    support_text_by_dimension: dict[str, str] = {}
    for claim in case["claims"]:
        support_text_by_dimension.setdefault(claim["dimension_id"], "")
        support_text_by_dimension[claim["dimension_id"]] += " " + claim["text"]
    for analysis in case["analyses"]:
        support_text_by_dimension.setdefault(analysis["dimension_id"], "")
        support_text_by_dimension[analysis["dimension_id"]] += " " + str(
            analysis.get("direct_answer") or ""
        )
    candidates = []
    for source in case["sources"]:
        candidates.append(
            {
                "candidate_id": source["source_id"],
                "dimension_id": source["dimension_id"],
                "url": source["url"],
                "canonical_url": source["url"],
                "title": source["title"],
                "family_id": source["family_id"],
                "source_tier": "first_party" if source["first_party"] else "secondary",
                "source_type": "official_document" if source["first_party"] else "web",
                "relevance": 0.9,
                "body_text": support_text_by_dimension[source["dimension_id"]],
                "span_text": support_text_by_dimension[source["dimension_id"]],
                "content_hash": "a" * 64,
                "page_flags": [],
                "invalid_reason": None,
                "published_date": "",
                "within_time_window": source.get("within_time_window", False),
                "admitted": True,
            }
        )
    assert family_by_source
    claims = []
    for raw in case["claims"]:
        claim = dict(raw)
        claim["supported_fact_refs"] = list(claim["source_ids"])
        claims.append(claim)
    return (
        ResearchBrief.from_json(case["brief"]),
        [DimensionCoverage.from_json(item) for item in case["coverages"]],
        {"analyses": case["analyses"], "claims": claims},
        candidates,
    )


def test_production_adapter_server_renders_and_owns_delivery_decision() -> None:
    brief, coverages, draft, candidates = _case()
    draft["report_md"] = "# forged"
    draft["delivery_status"] = "completed"
    result = evaluate_structured_report(
        brief=brief,
        coverages=coverages,
        draft=draft,
        evidence_candidates=candidates,
        audited_at="2026-07-16T00:00:00+00:00",
    )
    assert result["report_md"] != "# forged"
    assert result["delivery_status"] == "completed"
    assert result["quality_audit"]["passed"] is True


def test_production_adapter_rejects_cross_dimension_or_new_evidence() -> None:
    brief, coverages, draft, candidates = _case()
    draft["claims"][0]["source_ids"] = ["missing-source"]
    with pytest.raises(ReportDraftError, match="widened"):
        evaluate_structured_report(
            brief=brief,
            coverages=coverages,
            draft=draft,
            evidence_candidates=candidates,
        )


def test_model_owned_analysis_ids_cannot_authorize_fabricated_fact_refs() -> None:
    brief, coverages, draft, candidates = _case()
    draft["analyses"][0]["fact_claim_ids"] = ["model-invented-fact"]
    draft["claims"][0]["supported_fact_refs"] = ["model-invented-fact"]
    draft["claims"][0]["text"] = "FABRICATED"
    with pytest.raises(ReportDraftError, match="fact refs widened"):
        evaluate_structured_report(
            brief=brief,
            coverages=coverages,
            draft=draft,
            evidence_candidates=candidates,
        )


def test_valid_passage_ids_cannot_authorize_text_the_passages_do_not_support() -> None:
    brief, coverages, draft, candidates = _case()
    draft["analyses"][0]["direct_answer"] = "The Moon is made of cheese"
    draft["claims"][0]["text"] = "The Moon is made of cheese"
    for candidate in candidates:
        candidate["body_text"] = "This passage says nothing about lunar cheese."
        candidate["span_text"] = "This passage says nothing about lunar cheese."
    with pytest.raises(ReportDraftError, match="does not support"):
        evaluate_structured_report(
            brief=brief,
            coverages=coverages,
            draft=draft,
            evidence_candidates=candidates,
        )


def test_inference_label_cannot_bypass_passage_support_for_key_judgment() -> None:
    brief, coverages, draft, candidates = _case()
    draft["claims"][0]["text"] = "The Moon is made of cheese"
    draft["claims"][0]["is_inference"] = True
    with pytest.raises(ReportDraftError, match="does not support"):
        evaluate_structured_report(
            brief=brief,
            coverages=coverages,
            draft=draft,
            evidence_candidates=candidates,
        )


def test_extractive_recovery_narrows_paraphrases_to_admitted_passages() -> None:
    brief, coverages, draft, candidates = _case()
    for analysis in draft["analyses"]:
        analysis["direct_answer"] = "Unsupported paraphrase about lunar cheese"
    for claim in draft["claims"]:
        claim["text"] = "Unsupported paraphrase about lunar cheese"
    recovered = build_extractively_grounded_draft(
        brief=brief,
        coverages=coverages,
        draft=draft,
        evidence_candidates=candidates,
    )
    result = evaluate_structured_report(
        brief=brief,
        coverages=coverages,
        draft=recovered,
        evidence_candidates=candidates,
        audited_at="2026-07-17T00:00:00+00:00",
    )
    passages = {item["candidate_id"]: item["span_text"] for item in candidates}
    assert result["delivery_status"] == "completed"
    assert all(
        claim["text"] in passages[claim["source_ids"][0]]
        for claim in recovered["claims"]
    )
    assert all(
        analysis["direct_answer"] in passages[analysis["winning_evidence_ids"][0]]
        for analysis in recovered["analyses"]
    )


def test_extractive_recovery_fails_closed_without_admitted_evidence() -> None:
    brief, coverages, draft, candidates = _case()
    for candidate in candidates:
        candidate["admitted"] = False
    with pytest.raises(ReportDraftError, match="evidence-backed key judgments"):
        build_extractively_grounded_draft(
            brief=brief,
            coverages=coverages,
            draft=draft,
            evidence_candidates=candidates,
        )


def test_atomic_repair_changes_only_one_dimension_and_never_sources() -> None:
    _, _, draft, _ = _case()
    dimension_id = draft["claims"][0]["dimension_id"]
    replacement = dict(draft["claims"][0])
    replacement["claim_id"] = "replacement-key"
    repaired = apply_atomic_repair(
        draft,
        {
            "repair_action": "narrow_unsupported_claim",
            "dimension_id": dimension_id,
            "replacement_claims": [replacement],
        },
        allowed_action="narrow_unsupported_claim",
        expected_dimension_id=dimension_id,
    )
    assert repaired["claims"][-1]["claim_id"] == "replacement-key"
    assert all(
        item["dimension_id"] != dimension_id
        for item in repaired["claims"][:-1]
    )
    with pytest.raises(ReportDraftError, match="one dimension"):
        apply_atomic_repair(
            draft,
            {
                "repair_action": "narrow_unsupported_claim",
                "dimension_id": dimension_id,
                "replacement_claims": [
                    {**replacement, "dimension_id": "different-dimension"}
                ],
            },
            allowed_action="narrow_unsupported_claim",
            expected_dimension_id=dimension_id,
        )
    with pytest.raises(ReportDraftError, match="deterministic target"):
        apply_atomic_repair(
            draft,
            {
                "repair_action": "narrow_unsupported_claim",
                "dimension_id": dimension_id,
                "replacement_claims": [replacement],
            },
            allowed_action="narrow_unsupported_claim",
            expected_dimension_id="different-dimension",
        )
