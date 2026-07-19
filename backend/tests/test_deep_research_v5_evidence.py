from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from deskpet.workflows.definitions.deep_research_v5_evidence import (
    POLICY_HASH,
    AdmissionDecision,
    CoverageMetrics,
    EvidenceAdmissionPolicyV1,
    EvidenceCandidate,
    admit_passage,
    classify_coverage,
    evaluate_evidence,
    fold_source_families,
    match_source_family,
    policy_manifest,
)
from deskpet.workflows.definitions.deep_research_v5_policy import profile_definition


FIXTURE = Path(__file__).parent / "fixtures" / "deep_research_v5_evidence_policy.json"


def _long_body(seed: str) -> str:
    return ((seed.strip() + " ") * 16).strip()


def _candidate(
    candidate_id: str,
    *,
    dimension_id: str = "dimension-core",
    relevance: float = 0.8,
    source_tier: str = "secondary",
    source_type: str = "independent_research",
    url: str | None = None,
    title: str | None = None,
    body_text: str | None = None,
    span_text: str | None = None,
    **kwargs,
) -> EvidenceCandidate:
    seed = f"{candidate_id} provides supported evidence for the assigned research dimension."
    body = body_text if body_text is not None else _long_body(seed)
    span = span_text if span_text is not None else seed
    return EvidenceCandidate(
        candidate_id=candidate_id,
        dimension_id=dimension_id,
        url=url or f"https://{candidate_id}.example/document",
        title=title or f"Evidence document {candidate_id}",
        body_text=body,
        span_text=span,
        relevance=relevance,
        source_type=source_type,
        source_tier=source_tier,  # type: ignore[arg-type]
        **kwargs,
    )


def _metrics(**changes) -> CoverageMetrics:
    values = {
        "dimension_id": "dimension-core",
        "importance": "core",
        "admitted_passages": 2,
        "strong_distinct_families": 2,
        "winning_relevance": 0.65,
        "duplicate_rate": 0.50,
        "invalid_rate": 0.40,
        "first_party_required": True,
        "first_party_satisfied": True,
    }
    values.update(changes)
    return CoverageMetrics(**values)


def test_policy_manifest_hash_is_fixture_locked_and_ordered() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert EvidenceAdmissionPolicyV1.policy_id == fixture["policy_id"]
    assert POLICY_HASH == fixture["policy_hash"]
    assert policy_manifest()["order"] == [
        "invalid_page",
        "passage_admission",
        "source_family_folding",
        "per_dimension_coverage",
    ]


@pytest.mark.parametrize(
    ("relevance", "accepted"),
    [(0.54, False), (0.55, True)],
)
def test_passage_relevance_boundary_is_inclusive(
    relevance: float, accepted: bool
) -> None:
    body = "证" * 300
    decision = admit_passage(
        _candidate(
            f"relevance-{relevance}",
            relevance=relevance,
            body_text=body,
            span_text="证" * 20,
        )
    )
    assert decision.accepted is accepted


@pytest.mark.parametrize("flag", ["captcha", "login_wall", "app_shell"])
def test_invalid_page_gate_precedes_body_span_and_relevance(flag: str) -> None:
    decision = admit_passage(
        _candidate(
            f"invalid-{flag}",
            relevance=0.1,
            body_text="short",
            span_text=None,
            page_flags=(flag,),
        )
    )
    assert decision == AdmissionDecision(
        f"invalid-{flag}",
        "dimension-core",
        False,
        "page_invalid",
        f"invalid_page:{flag}",
    )


def test_passage_rejects_short_body_and_missing_or_untraceable_span() -> None:
    short = admit_passage(
        _candidate("short", body_text="证" * 299, span_text="证" * 20)
    )
    missing = admit_passage(_candidate("missing-span", span_text="not in body"))
    assert short.reason == "body_too_short"
    assert missing.reason == "body_span_missing"


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"winning_relevance": 0.64}, "partially_covered"),
        ({"winning_relevance": 0.65}, "covered"),
        ({"invalid_rate": 0.40}, "covered"),
        ({"invalid_rate": 0.41}, "partially_covered"),
        ({"duplicate_rate": 0.50}, "covered"),
        ({"duplicate_rate": 0.51}, "partially_covered"),
        (
            {"admitted_passages": 0, "strong_distinct_families": 0},
            "uncovered",
        ),
        (
            {"admitted_passages": 1, "strong_distinct_families": 1},
            "partially_covered",
        ),
        (
            {"admitted_passages": 2, "strong_distinct_families": 1},
            "partially_covered",
        ),
        (
            {"admitted_passages": 2, "strong_distinct_families": 2},
            "covered",
        ),
        ({"first_party_satisfied": False}, "partially_covered"),
    ],
)
def test_core_coverage_threshold_boundaries(
    changes: dict[str, object], expected: str
) -> None:
    assert classify_coverage(_metrics(**changes)) == expected


def test_supporting_dimension_is_covered_with_one_distinct_family() -> None:
    assert classify_coverage(
        _metrics(
            importance="supporting",
            admitted_passages=1,
            strong_distinct_families=1,
            winning_relevance=0.55,
            first_party_required=False,
            first_party_satisfied=True,
        )
    ) == "covered"


def _fixture_candidates() -> tuple[EvidenceCandidate, ...]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = []
    for item in payload["source_family_cases"]:
        values = dict(item)
        seed = values.pop("body_seed")
        values["body_text"] = _long_body(seed)
        result.append(EvidenceCandidate(**values))
    return tuple(result)


def test_source_family_exact_strong_weak_none_and_weak_does_not_fold() -> None:
    moe, baidu, news, weak = _fixture_candidates()
    assert match_source_family(moe, baidu).level == "strong"
    assert match_source_family(moe, news).level == "strong"
    weak_match = match_source_family(moe, weak)
    assert weak_match.level == "weak"
    assert set(weak_match.matched_fields) == {"normalized_title", "published_date"}
    assert match_source_family(news, weak).level == "none"

    exact = replace(
        weak,
        candidate_id="exact-copy",
        url="https://mirror.example/exact-copy",
        canonical_target_url=moe.url,
    )
    original_with_target = replace(moe, canonical_target_url=moe.url)
    assert match_source_family(original_with_target, exact).level == "exact"

    passages, families, _ = fold_source_families((moe, baidu, news, weak))
    assert len(passages) == 4
    assert len(families) == 2
    assert len({item.family_id for item in passages[:3]}) == 1
    assert passages[-1].family_id != passages[0].family_id


def test_official_original_beats_baidu_and_reprint_as_family_canonical() -> None:
    candidates = _fixture_candidates()
    _, families, matches = fold_source_families(candidates)
    official_family = next(
        family for family in families if candidates[0].url in family.member_urls
    )
    assert official_family.canonical_source_id == "moe-original"
    assert official_family.canonical_url == candidates[0].url
    assert official_family.source_tier == "first_party"
    assert official_family.original_document_url == candidates[0].url
    assert any(match.level == "weak" for match in matches)


def test_all_education_core_require_first_party_and_equity_requires_statistics() -> None:
    dimensions = profile_definition("policy_education").dimensions
    candidates: list[EvidenceCandidate] = []
    for dimension in dimensions:
        official_type = (
            "official_report"
            if dimension.dimension_id == "edu_equity_urban_rural_region"
            else "official_policy"
        )
        candidates.extend(
            (
                _candidate(
                    f"{dimension.dimension_id}-official",
                    dimension_id=dimension.dimension_id,
                    source_tier="first_party",
                    source_type=official_type,
                    relevance=0.82,
                ),
                _candidate(
                    f"{dimension.dimension_id}-independent",
                    dimension_id=dimension.dimension_id,
                    source_tier="secondary",
                    source_type="independent_research",
                    relevance=0.76,
                ),
            )
        )
    first = evaluate_evidence(dimensions, candidates)
    statuses = {
        item.dimension_id: item.status for item in first.dimension_coverages
    }
    assert statuses["edu_equity_urban_rural_region"] == "partially_covered"
    assert all(
        status == "covered"
        for dimension_id, status in statuses.items()
        if dimension_id != "edu_equity_urban_rural_region"
    )

    fixed = [
        replace(item, source_type="official_statistics")
        if item.candidate_id == "edu_equity_urban_rural_region-official"
        else item
        for item in candidates
    ]
    second = EvidenceAdmissionPolicyV1.evaluate(dimensions, fixed)
    assert all(item.status == "covered" for item in second.dimension_coverages)
    assert all(item.first_party_satisfied for item in second.dimension_coverages)


def test_cross_dimension_passages_and_winners_never_mix() -> None:
    dimensions = profile_definition("generic_research").dimensions[:2]
    candidates = tuple(
        _candidate(
            f"{dimension.dimension_id}-{index}",
            dimension_id=dimension.dimension_id,
            source_tier="secondary",
            relevance=0.75 + index * 0.05,
        )
        for dimension in dimensions
        for index in (1, 2)
    )
    result = evaluate_evidence(dimensions, candidates)
    for coverage in result.dimension_coverages:
        assert all(
            candidate_id.startswith(coverage.dimension_id)
            for candidate_id in coverage.evidence_passage_ids
        )
        assert all(
            candidate_id.startswith(coverage.dimension_id)
            for candidate_id in coverage.winning_evidence_ids
        )


def test_invalid_rate_counts_page_invalid_only_not_passage_rejections() -> None:
    dimension = replace(
        profile_definition("generic_research").dimensions[0],
        first_party_required=False,
    )
    candidates = (
        _candidate("accepted-a", dimension_id=dimension.dimension_id),
        _candidate("accepted-b", dimension_id=dimension.dimension_id),
        _candidate(
            "low-relevance",
            dimension_id=dimension.dimension_id,
            relevance=0.54,
        ),
        _candidate(
            "short-body",
            dimension_id=dimension.dimension_id,
            body_text="too short",
            span_text="too short",
        ),
    )
    result = evaluate_evidence((dimension,), candidates)
    metrics = result.coverage_metrics[0]
    assert metrics.invalid_rate == 0.0
    assert metrics.admitted_passages == 2
    assert result.dimension_coverages[0].status == "covered"


def test_duplicate_rate_uses_all_fetched_exact_or_strong_families() -> None:
    dimension = replace(
        profile_definition("generic_research").dimensions[0],
        first_party_required=False,
    )
    original = _candidate(
        "original",
        dimension_id=dimension.dimension_id,
        url="https://official.example/policy",
        canonical_target_url="https://official.example/policy",
    )
    duplicate_rejected = _candidate(
        "duplicate-rejected",
        dimension_id=dimension.dimension_id,
        url="https://mirror.example/policy",
        canonical_target_url="https://official.example/policy",
        relevance=0.54,
    )
    independent = _candidate("independent", dimension_id=dimension.dimension_id)
    result = evaluate_evidence(
        (dimension,),
        (original, duplicate_rejected, independent),
    )
    metrics = result.coverage_metrics[0]
    assert metrics.admitted_passages == 2
    assert metrics.strong_distinct_families == 2
    assert metrics.duplicate_rate == pytest.approx(1 / 3)
    assert result.dimension_coverages[0].status == "covered"
