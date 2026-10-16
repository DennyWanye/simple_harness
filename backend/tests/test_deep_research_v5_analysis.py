from __future__ import annotations

import inspect
from pathlib import Path

from deskpet.workflows.definitions.deep_research_v3_contracts import (
    AtomicClaim,
    EvidencePassage,
)
from deskpet.workflows.definitions.deep_research_v3_quality import evaluate_support
from deskpet.workflows.definitions.deep_research_v5_contracts import ResearchDimension
from deskpet.workflows.definitions.deep_research_v5_evidence import EvidenceCandidate

FIXTURE = Path(__file__).parent / "fixtures" / "deep_research_v5_education_analysis.json"


def _body(span: str, seed: str) -> str:
    return f"{span} " + ((f"{seed} provides additional source context. ") * 20)


def _candidate(
    candidate_id: str,
    dimension_id: str,
    *,
    relevance: float = 0.75,
    source_tier: str = "secondary",
    published_date: str | None = "2025-01-01",
    span_text: str | None = None,
    canonical_target_url: str | None = None,
) -> EvidenceCandidate:
    span = span_text or f"{candidate_id} documents the condition for {dimension_id}."
    return EvidenceCandidate(
        candidate_id=candidate_id,
        dimension_id=dimension_id,
        url=f"https://{candidate_id}.example/report",
        title=f"Report for {candidate_id}",
        body_text=_body(span, candidate_id),
        span_text=span,
        relevance=relevance,
        source_type="official_statistics"
        if source_tier == "first_party"
        else "independent_research",
        source_tier=source_tier,  # type: ignore[arg-type]
        published_date=published_date,
        canonical_target_url=canonical_target_url,
    )


def _dimension(dimension_id: str, importance: str = "core") -> ResearchDimension:
    return ResearchDimension(
        dimension_id=dimension_id,
        question=f"What is the state of {dimension_id}?",
        importance=importance,  # type: ignore[arg-type]
        expected_source_types=("official_statistics", "independent_research"),
        query_targets=(dimension_id,),
    )


def test_v3_support_interface_and_behavior_remain_stable() -> None:
    assert tuple(AtomicClaim.__dataclass_fields__) == (
        "claim_id",
        "text",
        "kind",
        "citation_ids",
        "repaired_from",
    )
    assert tuple(inspect.signature(evaluate_support).parameters) == (
        "claims",
        "passages",
        "lexical_threshold",
    )
    passage = EvidencePassage(
        "passage-1",
        1,
        "hash-1",
        "https://example.com/source",
        "question",
        "The measured value was 42 percent in 2025.",
        0,
        44,
        0.9,
    )
    supported = AtomicClaim("claim-1", passage.text, "factual", (1,))
    unsupported = AtomicClaim(
        "claim-2", "The measured value was 99 percent in 2025.", "factual", (1,)
    )
    assert evaluate_support((supported,), (passage,))[0].supported is True
    assert evaluate_support((unsupported,), (passage,))[0].supported is False
