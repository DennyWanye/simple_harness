from __future__ import annotations

import pytest

from deskpet.workflows.definitions.deep_research_v2_quality import apply_repair, evaluate_support, parse_claims
from deskpet.workflows.definitions.deep_research_v2_report import render_report


def test_sentence_and_paragraph_citations_bind_to_existing_evidence():
    claims = parse_claims("Revenue reached $12 million in 2026 [1].\n\nThis may indicate growth. [1]")
    decisions = evaluate_support(claims, {1: "Revenue reached $12 million in 2026. This may indicate growth."})
    assert [value.status for value in decisions] == ["supported", "inference"]


def test_numeric_or_date_conflict_fails_closed_even_with_semantic_score():
    claims = parse_claims("Revenue reached $12 million in 2026 [1].")
    decisions = evaluate_support(
        claims, {1: "Revenue reached $10 million in 2025."},
        semantic_scorer=lambda claim, evidence: 0.99,
    )
    assert decisions[0].status == "unsupported"
    assert decisions[0].reason_code == "missing_exact_token"


def test_lexical_threshold_is_inclusive_and_below_threshold_fails():
    supported = parse_claims("alpha beta gamma [1].")
    assert evaluate_support(supported, {1: "alpha delta epsilon zeta"})[0].status == "supported"
    unsupported = parse_claims("alpha beta gamma [1].")
    assert evaluate_support(unsupported, {1: "alpha delta epsilon zeta eta"})[0].status == "unsupported"


def test_repair_cannot_add_claim_ids_or_citation_ids():
    claims = parse_claims("A documented fact [1].")
    repaired = apply_repair(
        "A documented fact [1].", claims,
        {"replacements": [{"claim_id": claims[0].claim_id, "replacement": "A supported fact", "citation_ids": [1]}], "removals": []},
        valid_citation_ids={1},
    )
    assert repaired.count("[1]") == 1
    assert repaired == "A supported fact [1]."
    with pytest.raises(ValueError, match="invalid_repair_citation"):
        apply_repair(
            "A documented fact [1].", claims,
            {"replacements": [{"claim_id": claims[0].claim_id, "replacement": "A fact", "citation_ids": [2]}], "removals": []},
            valid_citation_ids={1},
        )
    with pytest.raises(ValueError, match="invalid_repair_claim"):
        apply_repair(
            "A documented fact [1].", claims,
            {"replacements": [], "removals": ["new-claim"]}, valid_citation_ids={1},
        )
    with pytest.raises(ValueError, match="repair_already_attempted"):
        apply_repair(
            "A documented fact [1].", claims,
            {"replacements": [], "removals": []}, valid_citation_ids={1}, already_repaired=True,
        )


def test_report_hash_and_citation_numbering_are_stable_across_ten_runs():
    reports = [
        render_report(
            topic="stable", supported_findings=("Finding [1]",), inferences=(),
            limitations=("bounded",),
            citations=({"url": "https://a.example/source", "title": "A"},),
            coverage={"support_rate": 1.0}, errors=(),
        )
        for _ in range(10)
    ]
    assert len({value["report_hash"] for value in reports}) == 1
    assert all("[1] A" in value["report_md"] for value in reports)


def test_report_with_citations_but_no_supported_findings_is_not_completed():
    report = render_report(
        topic="unsupported", supported_findings=(), inferences=(), limitations=(),
        citations=({"url": "https://a.example", "title": "A"},),
        coverage={"support_rate": 0.0}, errors=(),
    )
    assert report["status"] == "no_results"
    assert "不足以支持" in report["report_md"]
