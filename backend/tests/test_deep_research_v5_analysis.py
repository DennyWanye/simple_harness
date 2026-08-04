from __future__ import annotations

import inspect
import json
import random
from dataclasses import replace
from pathlib import Path

import pytest

from deskpet.workflows.definitions.deep_research_v3_contracts import (
    AtomicClaim,
    EvidencePassage,
)
from deskpet.workflows.definitions.deep_research_v3_quality import evaluate_support
from deskpet.workflows.definitions.deep_research_v5_analysis import (
    AnalysisStatement,
    PerDimensionAnalysisPayload,
    make_gap_analysis_payload,
    rerank_analysis_passages,
    validate_dimension_analysis_payload,
)
from deskpet.workflows.definitions.deep_research_v5_contracts import (
    ContractValidationError,
    ResearchDimension,
)
from deskpet.workflows.definitions.deep_research_v5_evidence import (
    EvidenceCandidate,
    evaluate_evidence,
)
from deskpet.workflows.definitions.deep_research_v5_policy import profile_definition


FIXTURE = (
    Path(__file__).parent / "fixtures" / "deep_research_v5_education_analysis.json"
)


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
        source_type="official_statistics" if source_tier == "first_party" else "independent_research",
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


def test_education_fixture_balances_all_six_core_dimensions_before_volume() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    dimensions = profile_definition(fixture["profile"]).dimensions
    prolific = dimensions[0].dimension_id
    candidates = [
        _candidate(f"prolific-{index}", prolific, relevance=0.99 - index * 0.01)
        for index in range(8)
    ]
    candidates.extend(
        _candidate(f"only-{dimension.dimension_id}", dimension.dimension_id)
        for dimension in dimensions[1:]
    )
    evaluation = evaluate_evidence(dimensions, candidates)

    selected = rerank_analysis_passages(dimensions, evaluation, maximum=6)

    assert fixture["core_dimension_ids"] == [item.dimension_id for item in dimensions]
    assert {item.candidate.dimension_id for item in selected} == set(
        fixture["core_dimension_ids"]
    )
    assert sum(item.candidate.dimension_id == prolific for item in selected) == 1

    with pytest.raises(ContractValidationError, match="every core dimension"):
        rerank_analysis_passages(dimensions, evaluation, maximum=5)


def test_family_winners_are_selected_before_nonwinning_passages() -> None:
    dimension = _dimension("core")
    candidates = [
        _candidate("family-a-winner", "core", relevance=0.95, canonical_target_url="https://origin.example/a"),
        _candidate("family-a-copy", "core", relevance=0.80, canonical_target_url="https://origin.example/a"),
        _candidate("family-b-winner", "core", relevance=0.90, canonical_target_url="https://origin.example/b"),
        _candidate("family-b-copy", "core", relevance=0.70, canonical_target_url="https://origin.example/b"),
    ]
    evaluation = evaluate_evidence((dimension,), candidates)

    selected = rerank_analysis_passages((dimension,), evaluation, maximum=2)

    assert {item.candidate.candidate_id for item in selected} == {
        "family-a-winner",
        "family-b-winner",
    }


def test_family_winner_prefers_official_original_over_more_relevant_reprint() -> None:
    dimension = _dimension("core")
    target = "https://origin.example/policy"
    candidates = [
        _candidate(
            "official-original",
            "core",
            relevance=0.70,
            source_tier="first_party",
            canonical_target_url=target,
        ),
        _candidate(
            "news-reprint",
            "core",
            relevance=0.99,
            source_tier="secondary",
            canonical_target_url=target,
        ),
    ]
    evaluation = evaluate_evidence((dimension,), candidates)

    selected = rerank_analysis_passages((dimension,), evaluation, maximum=1)

    assert selected[0].candidate.candidate_id == "official-original"


def test_fill_priority_is_authority_then_recency_relevance_and_depth() -> None:
    dimension = _dimension("core")
    target = "https://origin.example/shared"
    candidates = [
        _candidate("winner", "core", relevance=0.99, canonical_target_url=target),
        _candidate("official-old", "core", relevance=0.60, source_tier="first_party", published_date="2020-01-01", canonical_target_url=target),
        _candidate("secondary-new", "core", relevance=0.98, source_tier="secondary", published_date="2026-01-01", canonical_target_url=target),
    ]
    evaluation = evaluate_evidence((dimension,), candidates)

    selected = rerank_analysis_passages((dimension,), evaluation, maximum=2)

    assert [item.candidate.candidate_id for item in selected] == [
        "official-old",
        "secondary-new",
    ]


@pytest.mark.parametrize(
    ("left", "right", "expected_index", "expected"),
    [
        (
            {"published_date": "2026-01-01", "relevance": 0.60},
            {"published_date": "2025-12-31", "relevance": 0.98},
            0,
            "left",
        ),
        (
            {"published_date": "2025-01-01", "relevance": 0.80},
            {"published_date": "2025-01-01", "relevance": 0.79},
            1,
            "left",
        ),
        (
            {
                "published_date": "2025-01-01",
                "relevance": 0.80,
                "span_text": "A longer independently traceable evidence passage with more detail.",
            },
            {
                "published_date": "2025-01-01",
                "relevance": 0.80,
                "span_text": "Short evidence.",
            },
            1,
            "left",
        ),
    ],
)
def test_fill_priority_boundary_order(
    left: dict[str, object],
    right: dict[str, object],
    expected_index: int,
    expected: str,
) -> None:
    dimension = _dimension("core")
    target = "https://origin.example/shared"
    candidates = [
        _candidate("winner", "core", relevance=0.99, canonical_target_url=target),
        _candidate("left", "core", canonical_target_url=target, **left),
        _candidate("right", "core", canonical_target_url=target, **right),
    ]
    evaluation = evaluate_evidence((dimension,), candidates)

    selected = rerank_analysis_passages((dimension,), evaluation, maximum=2)

    assert selected[expected_index].candidate.candidate_id == expected


@pytest.mark.parametrize("seed", range(20))
def test_core_balance_property_never_loses_a_low_volume_dimension(seed: int) -> None:
    rng = random.Random(seed)
    dimensions = tuple(_dimension(f"core-{index}") for index in range(1, 7))
    candidates = []
    for dimension in dimensions:
        for index in range(rng.randint(1, 12)):
            candidates.append(
                _candidate(
                    f"{dimension.dimension_id}-{index}",
                    dimension.dimension_id,
                    relevance=round(rng.uniform(0.56, 0.99), 4),
                )
            )
    rng.shuffle(candidates)
    evaluation = evaluate_evidence(dimensions, candidates)

    selected = rerank_analysis_passages(dimensions, evaluation, maximum=6)

    assert len(selected) == 6
    assert len({item.candidate.candidate_id for item in selected}) == 6
    assert {item.candidate.dimension_id for item in selected} == {
        item.dimension_id for item in dimensions
    }


def _analysis_fixture():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    analysis_case = fixture["analysis_case"]
    dimension = next(
        item
        for item in profile_definition(fixture["profile"]).dimensions
        if item.dimension_id == analysis_case["dimension_id"]
    )
    candidate = _candidate(
        analysis_case["passage_id"],
        dimension.dimension_id,
        relevance=0.90,
        source_tier="first_party",
        span_text=analysis_case["span_text"],
    )
    evaluation = evaluate_evidence((dimension,), (candidate,))
    selected = rerank_analysis_passages((dimension,), evaluation, maximum=1)
    payload = PerDimensionAnalysisPayload.from_json(analysis_case["payload"])
    return dimension, evaluation, selected, payload


def test_strict_education_analysis_converts_to_existing_dimension_contract() -> None:
    dimension, evaluation, selected, payload = _analysis_fixture()

    analysis = validate_dimension_analysis_payload(
        payload,
        dimension=dimension,
        evaluation=evaluation,
        selected_passages=selected,
    )

    assert analysis.dimension_id == dimension.dimension_id
    assert analysis.direct_answer == payload.finding.text
    assert analysis.fact_claim_ids == (
        "fact-current-enrollment",
        "fact-current-state",
    )
    assert analysis.inference_claim_ids == (
        "inference-demography",
        "inference-capacity",
        "uncertainty-local",
    )
    assert analysis.limitation_claim_ids == ("uncertainty-local",)
    assert analysis.winning_evidence_ids == ("edu-current-official",)
    assert analysis.confidence == "low"  # one family is only partial coverage


def test_unsupported_numeric_fact_fails_closed() -> None:
    dimension, evaluation, selected, payload = _analysis_fixture()
    unsupported = replace(
        payload,
        finding=replace(payload.finding, text=payload.finding.text.replace("100", "999")),
    )

    with pytest.raises(ContractValidationError, match="unsupported fact"):
        validate_dimension_analysis_payload(
            unsupported,
            dimension=dimension,
            evaluation=evaluation,
            selected_passages=selected,
        )


def test_cross_dimension_citation_fails_closed_even_if_selected() -> None:
    dimension, evaluation, selected, payload = _analysis_fixture()
    other = _dimension("other")
    other_candidate = _candidate("other-passage", "other")
    combined_evaluation = evaluate_evidence(
        (dimension, other),
        (selected[0].candidate, other_candidate),
    )
    combined_selected = rerank_analysis_passages(
        (dimension, other), combined_evaluation, maximum=2
    )
    crossed = replace(
        payload,
        finding=replace(payload.finding, citation_ids=("other-passage",)),
        current_state=None,
        citation_ids=("other-passage",),
    )

    with pytest.raises(ContractValidationError, match="selected admitted passages"):
        validate_dimension_analysis_payload(
            crossed,
            dimension=dimension,
            evaluation=combined_evaluation,
            selected_passages=combined_selected,
        )


def test_inference_is_independent_and_not_subject_to_fact_support_gate() -> None:
    dimension, evaluation, selected, payload = _analysis_fixture()
    speculative = replace(
        payload,
        driver_or_change=replace(
            payload.driver_or_change,
            text="A future enrollment reversal is plausible but not established.",
        ),
    )

    analysis = validate_dimension_analysis_payload(
        speculative,
        dimension=dimension,
        evaluation=evaluation,
        selected_passages=selected,
    )

    assert "inference-demography" in analysis.inference_claim_ids
    assert "inference-demography" not in analysis.fact_claim_ids


def test_direct_finding_cannot_be_an_unmarked_inference() -> None:
    dimension, evaluation, selected, payload = _analysis_fixture()
    inferred_finding = replace(
        payload,
        finding=replace(payload.finding, kind="inference"),
    )
    with pytest.raises(ContractValidationError, match="supported factual finding"):
        validate_dimension_analysis_payload(
            inferred_finding,
            dimension=dimension,
            evaluation=evaluation,
            selected_passages=selected,
        )


def test_uncovered_dimension_requires_explicit_gap_without_pseudo_conclusion() -> None:
    dimension = _dimension("missing")
    evaluation = evaluate_evidence((dimension,), ())
    gap = make_gap_analysis_payload(dimension.dimension_id)

    analysis = validate_dimension_analysis_payload(
        gap,
        dimension=dimension,
        evaluation=evaluation,
        selected_passages=(),
    )

    assert analysis.direct_answer is None
    assert analysis.confidence == "insufficient"
    assert analysis.limitation_claim_ids == ("gap:missing",)

    with pytest.raises(ContractValidationError, match="publication metadata"):
        PerDimensionAnalysisPayload(
            dimension_id=dimension.dimension_id,
            finding=AnalysisStatement(
                "policy-slogan",
                "Evidence gap: a national policy was published on 2025-01-01.",
                "gap",
            ),
            current_state=None,
            driver_or_change=None,
            impact=None,
            uncertainty=None,
            counterevidence=None,
            citation_ids=(),
        )

    fake_policy = replace(
        gap,
        current_state=AnalysisStatement(
            "fake-current", "Strengthen education quality nationwide.", "inference"
        ),
    )
    with pytest.raises(ContractValidationError, match="one explicit gap"):
        validate_dimension_analysis_payload(
            fake_policy,
            dimension=dimension,
            evaluation=evaluation,
            selected_passages=(),
        )


def test_payload_schema_rejects_unknown_fields_and_citation_union_drift() -> None:
    _, _, _, payload = _analysis_fixture()
    raw = payload.to_json()
    raw["extra"] = "not allowed"
    with pytest.raises(ContractValidationError, match="keys differ"):
        PerDimensionAnalysisPayload.from_json(raw)

    raw = payload.to_json()
    raw["citation_ids"] = []
    with pytest.raises(ContractValidationError, match="exactly match"):
        PerDimensionAnalysisPayload.from_json(raw)


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
