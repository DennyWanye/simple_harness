"""Versioned evidence admission and source-family policy for DeepResearch v5."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from itertools import combinations
from typing import Literal, Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .deep_research_v5_contracts import (
    ContractValidationError,
    DimensionCoverage,
    EvidenceSourceFamily,
    ResearchDimension,
)


FamilyMatchLevel = Literal["exact", "strong", "weak", "none"]
CoverageStatus = Literal[
    "covered", "partially_covered", "uncovered", "not_applicable"
]

POLICY_ID = "EvidenceAdmissionPolicyV1"
POLICY_VERSION = "1"

MIN_BODY_CHARS = 300
MIN_RELEVANCE = 0.55
MIN_WINNING_RELEVANCE = 0.65
MAX_INVALID_RATE = 0.40
MAX_DUPLICATE_RATE = 0.50
MIN_CORE_PASSAGES = 2
MIN_CORE_FAMILIES = 2

_INVALID_FLAG_ORDER = (
    "invalid",
    "captcha",
    "security_verification",
    "login",
    "login_wall",
    "app_shell",
    "navigation_only",
    "search_redirect",
    "blank",
    "garbled",
    "title_mismatch",
)
_INVALID_CONTENT_PATTERNS = (
    ("captcha", re.compile(r"captcha|人机验证", re.I)),
    ("security_verification", re.compile(r"安全验证|security verification", re.I)),
    ("login_wall", re.compile(r"登录后(?:继续|查看)|sign in to continue|login required", re.I)),
    ("app_shell", re.compile(r"enable javascript|请启用javascript|javascript is required", re.I)),
)
_TRACKING_QUERY_KEYS = frozenset(
    {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "spm", "from"}
)


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _normalized_identity(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", unicodedata.normalize("NFKC", value).casefold())


def _canonical_url(value: str) -> str:
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").casefold()
    port = parts.port
    netloc = host if port is None else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if key.casefold() not in _TRACKING_QUERY_KEYS
        )
    )
    return urlunsplit((parts.scheme.casefold() or "https", netloc, path, query, ""))


def _body_hash(body: str) -> str:
    return hashlib.sha256(_normalized_text(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    candidate_id: str
    dimension_id: str
    url: str
    title: str
    body_text: str
    span_text: str | None
    relevance: float
    source_type: str
    source_tier: Literal["first_party", "secondary", "aggregator"]
    canonical_url: str | None = None
    canonical_target_url: str | None = None
    content_hash: str | None = None
    issuing_body: str | None = None
    document_number: str | None = None
    published_date: str | None = None
    invalid_reason: str | None = None
    page_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("candidate_id", "dimension_id", "url", "title", "source_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ContractValidationError(f"{name} is required")
        if self.source_tier not in {"first_party", "secondary", "aggregator"}:
            raise ContractValidationError("invalid source_tier")
        if (
            isinstance(self.relevance, bool)
            or not isinstance(self.relevance, (int, float))
            or not 0 <= float(self.relevance) <= 1
        ):
            raise ContractValidationError("relevance must be between 0 and 1")
        if not isinstance(self.body_text, str):
            raise ContractValidationError("body_text must be a string")
        if self.span_text is not None and not isinstance(self.span_text, str):
            raise ContractValidationError("span_text must be a string or None")
        if len(set(self.page_flags)) != len(self.page_flags):
            raise ContractValidationError("page_flags must be unique")

    @property
    def effective_canonical_url(self) -> str:
        return _canonical_url(self.canonical_url or self.url)

    @property
    def effective_content_hash(self) -> str:
        return self.content_hash or _body_hash(self.body_text)


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    candidate_id: str
    dimension_id: str
    accepted: bool
    stage: Literal["page_invalid", "passage_admission", "admitted"]
    reason: str


@dataclass(frozen=True, slots=True)
class SourceFamilyMatch:
    left_candidate_id: str
    right_candidate_id: str
    level: FamilyMatchLevel
    matched_fields: tuple[str, ...]
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdmittedPassage:
    candidate: EvidenceCandidate
    family_id: str


@dataclass(frozen=True, slots=True)
class CoverageMetrics:
    dimension_id: str
    importance: Literal["core", "supporting"]
    admitted_passages: int
    strong_distinct_families: int
    winning_relevance: float
    duplicate_rate: float
    invalid_rate: float
    first_party_required: bool
    first_party_satisfied: bool

    def __post_init__(self) -> None:
        for name in ("admitted_passages", "strong_distinct_families"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractValidationError(f"{name} must be a non-negative integer")
        for name in ("winning_relevance", "duplicate_rate", "invalid_rate"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= float(value) <= 1
            ):
                raise ContractValidationError(f"{name} must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class EvidenceEvaluation:
    policy_hash: str
    decisions: tuple[AdmissionDecision, ...]
    admitted_passages: tuple[AdmittedPassage, ...]
    source_families: tuple[EvidenceSourceFamily, ...]
    family_matches: tuple[SourceFamilyMatch, ...]
    coverage_metrics: tuple[CoverageMetrics, ...]
    dimension_coverages: tuple[DimensionCoverage, ...]


def policy_manifest() -> dict[str, object]:
    return {
        "policy_id": POLICY_ID,
        "version": POLICY_VERSION,
        "order": [
            "invalid_page",
            "passage_admission",
            "source_family_folding",
            "per_dimension_coverage",
        ],
        "page_invalid_flags": list(_INVALID_FLAG_ORDER),
        "passage": {
            "min_body_chars": MIN_BODY_CHARS,
            "min_relevance": MIN_RELEVANCE,
            "requires_body_span": True,
        },
        "source_family": {
            "fold_levels": ["exact", "strong"],
            "audit_only_levels": ["weak"],
            "official_original_priority": True,
        },
        "core_covered": {
            "min_admitted_passages": MIN_CORE_PASSAGES,
            "min_strong_distinct_families": MIN_CORE_FAMILIES,
            "min_winning_relevance": MIN_WINNING_RELEVANCE,
            "max_duplicate_rate": MAX_DUPLICATE_RATE,
            "max_invalid_rate": MAX_INVALID_RATE,
            "first_party_if_required": True,
            "duplicate_rate_population": "fetched_candidates_exact_or_strong_family",
            "invalid_rate_population": "fetched_candidates_page_invalid_only",
        },
        "supporting_covered": {"min_strong_distinct_families": 1},
        "education_equity_first_party_statistics": True,
    }


def _policy_hash() -> str:
    canonical = json.dumps(
        policy_manifest(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


POLICY_HASH = _policy_hash()


def page_invalid_reason(candidate: EvidenceCandidate) -> str | None:
    if candidate.invalid_reason:
        return f"invalid_reason:{candidate.invalid_reason}"
    flags = {value.casefold() for value in candidate.page_flags}
    for flag in _INVALID_FLAG_ORDER:
        if flag in flags:
            return f"invalid_page:{flag}"
    sample = f"{candidate.title}\n{candidate.body_text[:1000]}"
    for reason, pattern in _INVALID_CONTENT_PATTERNS:
        if pattern.search(sample):
            return f"invalid_page:{reason}"
    return None


def admit_passage(candidate: EvidenceCandidate) -> AdmissionDecision:
    invalid = page_invalid_reason(candidate)
    if invalid is not None:
        return AdmissionDecision(
            candidate.candidate_id,
            candidate.dimension_id,
            False,
            "page_invalid",
            invalid,
        )
    body = _normalized_text(candidate.body_text)
    if len(body) < MIN_BODY_CHARS:
        return AdmissionDecision(
            candidate.candidate_id,
            candidate.dimension_id,
            False,
            "passage_admission",
            "body_too_short",
        )
    span = _normalized_text(candidate.span_text or "")
    if not span or span not in body:
        return AdmissionDecision(
            candidate.candidate_id,
            candidate.dimension_id,
            False,
            "passage_admission",
            "body_span_missing",
        )
    if candidate.relevance < MIN_RELEVANCE:
        return AdmissionDecision(
            candidate.candidate_id,
            candidate.dimension_id,
            False,
            "passage_admission",
            "dimension_relevance_below_threshold",
        )
    return AdmissionDecision(
        candidate.candidate_id,
        candidate.dimension_id,
        True,
        "admitted",
        "admitted",
    )


def match_source_family(
    left: EvidenceCandidate,
    right: EvidenceCandidate,
) -> SourceFamilyMatch:
    matched: list[str] = []
    reasons: list[str] = []
    if left.effective_canonical_url == right.effective_canonical_url:
        matched.append("canonical_url")
        reasons.append("exact_canonical_url")
    if left.effective_content_hash == right.effective_content_hash:
        matched.append("content_hash")
        reasons.append("exact_content_hash")
    left_target = _canonical_url(left.canonical_target_url) if left.canonical_target_url else ""
    right_target = _canonical_url(right.canonical_target_url) if right.canonical_target_url else ""
    if left_target and left_target == right_target:
        matched.append("canonical_target_url")
        reasons.append("exact_canonical_target")
    if reasons:
        return SourceFamilyMatch(
            left.candidate_id,
            right.candidate_id,
            "exact",
            tuple(matched),
            tuple(reasons),
        )

    comparisons = {
        "normalized_title": (
            _normalized_identity(left.title),
            _normalized_identity(right.title),
        ),
        "issuing_body": (
            _normalized_identity(left.issuing_body),
            _normalized_identity(right.issuing_body),
        ),
        "document_number": (
            _normalized_identity(left.document_number),
            _normalized_identity(right.document_number),
        ),
        "published_date": (left.published_date or "", right.published_date or ""),
    }
    matched = [
        name for name, (a, b) in comparisons.items() if a and b and a == b
    ]
    matched_set = set(matched)
    if {"document_number", "issuing_body"} <= matched_set:
        return SourceFamilyMatch(
            left.candidate_id,
            right.candidate_id,
            "strong",
            tuple(matched),
            ("strong_document_number_and_issuer",),
        )
    if {"normalized_title", "published_date", "issuing_body"} <= matched_set:
        return SourceFamilyMatch(
            left.candidate_id,
            right.candidate_id,
            "strong",
            tuple(matched),
            ("strong_title_date_and_issuer",),
        )
    if len(matched) >= 2:
        return SourceFamilyMatch(
            left.candidate_id,
            right.candidate_id,
            "weak",
            tuple(matched),
            ("weak_partial_document_identity",),
        )
    return SourceFamilyMatch(
        left.candidate_id,
        right.candidate_id,
        "none",
        tuple(matched),
        ("no_family_match",),
    )


class _DisjointSet:
    def __init__(self, values: Sequence[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        low, high = sorted((left_root, right_root))
        self.parent[high] = low


def _canonical_candidate(members: Sequence[EvidenceCandidate]) -> EvidenceCandidate:
    tier = {"first_party": 0, "secondary": 1, "aggregator": 2}
    return min(
        members,
        key=lambda item: (tier[item.source_tier], -float(item.relevance), item.candidate_id),
    )


def fold_source_families(
    candidates: Sequence[EvidenceCandidate],
) -> tuple[
    tuple[AdmittedPassage, ...],
    tuple[EvidenceSourceFamily, ...],
    tuple[SourceFamilyMatch, ...],
]:
    ids = [item.candidate_id for item in candidates]
    if len(ids) != len(set(ids)):
        raise ContractValidationError("candidate ids must be unique")
    ordered = tuple(sorted(candidates, key=lambda item: item.candidate_id))
    disjoint = _DisjointSet(ids)
    matches: list[SourceFamilyMatch] = []
    for left, right in combinations(ordered, 2):
        match = match_source_family(left, right)
        matches.append(match)
        if match.level in {"exact", "strong"}:
            disjoint.union(left.candidate_id, right.candidate_id)
    groups: dict[str, list[EvidenceCandidate]] = {}
    for candidate in ordered:
        groups.setdefault(disjoint.find(candidate.candidate_id), []).append(candidate)

    families: list[EvidenceSourceFamily] = []
    passages: list[AdmittedPassage] = []
    for members in groups.values():
        member_ids = tuple(sorted(item.candidate_id for item in members))
        family_id = "family-" + hashlib.sha256("|".join(member_ids).encode("utf-8")).hexdigest()[:20]
        canonical = _canonical_candidate(members)
        member_urls = tuple(sorted({item.url for item in members}))
        family = EvidenceSourceFamily(
            family_id=family_id,
            canonical_url=canonical.url,
            canonical_source_id=canonical.candidate_id,
            source_type=canonical.source_type,
            source_tier=canonical.source_tier,
            member_urls=member_urls,
            original_document_url=(canonical.url if canonical.source_tier == "first_party" else None),
            page_quality="valid",
            content_hashes=tuple(sorted({item.effective_content_hash for item in members})),
        )
        families.append(family)
        passages.extend(AdmittedPassage(item, family_id) for item in members)
    return (
        tuple(sorted(passages, key=lambda item: item.candidate.candidate_id)),
        tuple(sorted(families, key=lambda item: item.family_id)),
        tuple(matches),
    )


def _fetched_duplicate_rate(candidates: Sequence[EvidenceCandidate]) -> float:
    """Measure exact/strong family duplication across fetched candidates.

    Admission failures remain visible in this retrieval-quality metric; weak
    matches deliberately stay distinct and therefore do not count as
    duplicates.
    """

    if not candidates:
        return 0.0
    ids = [item.candidate_id for item in candidates]
    disjoint = _DisjointSet(ids)
    for left, right in combinations(candidates, 2):
        if match_source_family(left, right).level in {"exact", "strong"}:
            disjoint.union(left.candidate_id, right.candidate_id)
    family_count = len({disjoint.find(candidate_id) for candidate_id in ids})
    return (len(candidates) - family_count) / len(candidates)


def classify_coverage(metrics: CoverageMetrics) -> CoverageStatus:
    quality_rates_ok = (
        metrics.duplicate_rate <= MAX_DUPLICATE_RATE
        and metrics.invalid_rate <= MAX_INVALID_RATE
    )
    first_party_ok = not metrics.first_party_required or metrics.first_party_satisfied
    if metrics.importance == "supporting":
        if (
            metrics.admitted_passages >= 1
            and metrics.strong_distinct_families >= 1
            and quality_rates_ok
            and first_party_ok
        ):
            return "covered"
    elif (
        metrics.admitted_passages >= MIN_CORE_PASSAGES
        and metrics.strong_distinct_families >= MIN_CORE_FAMILIES
        and metrics.winning_relevance >= MIN_WINNING_RELEVANCE
        and quality_rates_ok
        and first_party_ok
    ):
        return "covered"
    if metrics.admitted_passages > 0 and metrics.strong_distinct_families > 0:
        return "partially_covered"
    return "uncovered"


def _first_party_satisfied(
    dimension: ResearchDimension,
    passages: Sequence[AdmittedPassage],
) -> bool:
    first_party = [
        item.candidate for item in passages if item.candidate.source_tier == "first_party"
    ]
    if not dimension.first_party_required:
        return True
    if dimension.dimension_id == "edu_equity_urban_rural_region":
        return any("statistic" in item.source_type.casefold() for item in first_party)
    return bool(first_party)


def _coverage_gap_reasons(metrics: CoverageMetrics) -> tuple[str, ...]:
    reasons: list[str] = []
    if metrics.admitted_passages < (MIN_CORE_PASSAGES if metrics.importance == "core" else 1):
        reasons.append("insufficient_admitted_passages")
    if metrics.strong_distinct_families < (MIN_CORE_FAMILIES if metrics.importance == "core" else 1):
        reasons.append("insufficient_strong_distinct_families")
    if metrics.importance == "core" and metrics.winning_relevance < MIN_WINNING_RELEVANCE:
        reasons.append("winning_relevance_below_threshold")
    if metrics.duplicate_rate > MAX_DUPLICATE_RATE:
        reasons.append("duplicate_rate_above_threshold")
    if metrics.invalid_rate > MAX_INVALID_RATE:
        reasons.append("invalid_rate_above_threshold")
    if metrics.first_party_required and not metrics.first_party_satisfied:
        reasons.append("first_party_requirement_unsatisfied")
    return tuple(reasons)


def evaluate_evidence(
    dimensions: Sequence[ResearchDimension],
    candidates: Sequence[EvidenceCandidate],
) -> EvidenceEvaluation:
    dimension_ids = [item.dimension_id for item in dimensions]
    if len(dimension_ids) != len(set(dimension_ids)):
        raise ContractValidationError("dimension ids must be unique")
    known = set(dimension_ids)
    if any(item.dimension_id not in known for item in candidates):
        raise ContractValidationError("candidate references unknown dimension")
    candidate_ids = [item.candidate_id for item in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ContractValidationError("candidate ids must be unique")

    decisions = tuple(admit_passage(item) for item in candidates)
    accepted_ids = {item.candidate_id for item in decisions if item.accepted}
    accepted = tuple(item for item in candidates if item.candidate_id in accepted_ids)
    admitted, families, matches = fold_source_families(accepted)

    metrics_values: list[CoverageMetrics] = []
    coverage_values: list[DimensionCoverage] = []
    for dimension in dimensions:
        dimension_candidates = [
            item for item in candidates if item.dimension_id == dimension.dimension_id
        ]
        dimension_passages = [
            item
            for item in admitted
            if item.candidate.dimension_id == dimension.dimension_id
        ]
        family_ids = tuple(sorted({item.family_id for item in dimension_passages}))
        invalid_count = sum(
            1
            for decision in decisions
            if decision.dimension_id == dimension.dimension_id
            and decision.stage == "page_invalid"
        )
        invalid_rate = (
            invalid_count / len(dimension_candidates) if dimension_candidates else 0.0
        )
        duplicate_rate = _fetched_duplicate_rate(dimension_candidates)
        first_party = _first_party_satisfied(dimension, dimension_passages)
        winning_relevance = max(
            (float(item.candidate.relevance) for item in dimension_passages),
            default=0.0,
        )
        metrics = CoverageMetrics(
            dimension_id=dimension.dimension_id,
            importance=dimension.importance,
            admitted_passages=len(dimension_passages),
            strong_distinct_families=len(family_ids),
            winning_relevance=winning_relevance,
            duplicate_rate=duplicate_rate,
            invalid_rate=invalid_rate,
            first_party_required=dimension.first_party_required,
            first_party_satisfied=first_party,
        )
        status = classify_coverage(metrics)
        winners = tuple(
            max(
                (item for item in dimension_passages if item.family_id == family_id),
                key=lambda item: (float(item.candidate.relevance), item.candidate.candidate_id),
            ).candidate.candidate_id
            for family_id in family_ids
        )
        coverage_values.append(
            DimensionCoverage(
                dimension_id=dimension.dimension_id,
                status=status,
                evidence_passage_ids=tuple(
                    sorted(item.candidate.candidate_id for item in dimension_passages)
                ),
                winning_evidence_ids=winners,
                source_family_ids=family_ids,
                first_party_satisfied=first_party,
                relevance_score=winning_relevance,
                gap_reasons=() if status == "covered" else _coverage_gap_reasons(metrics),
            )
        )
        metrics_values.append(metrics)

    return EvidenceEvaluation(
        policy_hash=POLICY_HASH,
        decisions=decisions,
        admitted_passages=admitted,
        source_families=families,
        family_matches=matches,
        coverage_metrics=tuple(metrics_values),
        dimension_coverages=tuple(coverage_values),
    )


class EvidenceAdmissionPolicyV1:
    policy_id = POLICY_ID
    version = POLICY_VERSION
    policy_hash = POLICY_HASH

    @staticmethod
    def manifest() -> Mapping[str, object]:
        return policy_manifest()

    @staticmethod
    def evaluate(
        dimensions: Sequence[ResearchDimension],
        candidates: Sequence[EvidenceCandidate],
    ) -> EvidenceEvaluation:
        return evaluate_evidence(dimensions, candidates)


__all__ = [
    "AdmissionDecision",
    "AdmittedPassage",
    "CoverageMetrics",
    "EvidenceAdmissionPolicyV1",
    "EvidenceCandidate",
    "EvidenceEvaluation",
    "FamilyMatchLevel",
    "MAX_DUPLICATE_RATE",
    "MAX_INVALID_RATE",
    "MIN_BODY_CHARS",
    "MIN_RELEVANCE",
    "MIN_WINNING_RELEVANCE",
    "POLICY_HASH",
    "POLICY_ID",
    "POLICY_VERSION",
    "SourceFamilyMatch",
    "admit_passage",
    "classify_coverage",
    "evaluate_evidence",
    "fold_source_families",
    "match_source_family",
    "page_invalid_reason",
    "policy_manifest",
]
