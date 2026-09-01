"""Production-only bridge from retrieval documents to the locked v5 evidence policy.

The workflow definition owns the policy.  This module only derives deterministic
inputs from Search Gateway output and projects the policy result back to JSON.
Keeping that bridge out of ``main.py`` makes the production wiring directly
testable without importing the monolithic application module.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import urlsplit

from ...retrieval.query_terms import extract_query_terms, query_term_relevance
from ..definitions.deep_research_v5_contracts import ResearchBrief
from ..definitions.deep_research_v5_evidence import (
    EvidenceAdmissionPolicyV1,
    EvidenceCandidate,
    match_source_family,
)

_WORD = re.compile(r"[a-z0-9][a-z0-9_-]+", re.IGNORECASE)
_CJK = re.compile(r"[\u3400-\u9fff]")
_SENTENCE = re.compile(r"[^\n。！？!?]+[。！？!?]?", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "about", "after", "and", "are", "current", "data", "for", "from",
        "into", "latest", "official", "overview", "research", "site", "the",
        "this", "trend", "with", "以及", "关于", "分析", "当前", "数据", "研究",
        "相关", "综合", "现状", "趋势",
    }
)
_INVALID_FETCH_FLAGS = frozenset(
    {
        "invalid", "captcha", "security_verification", "login", "login_wall",
        "app_shell", "navigation_only", "search_redirect", "blank", "garbled",
        "title_mismatch",
    }
)
_FIRST_PARTY_HOSTS = frozenset(
    {
        "ai.google.dev", "anthropic.com", "apple.com", "deepmind.google",
        "mi.com", "oecd.org", "openai.com", "un.org", "unesco.org",
        "who.int", "worldbank.org", "xiaomi.com",
    }
)
READINESS_POLICY_ID = "EvidenceReadinessV1"
READINESS_POLICY_VERSION = "1"
_READINESS_MANIFEST = {
    "policy_id": READINESS_POLICY_ID,
    "version": READINESS_POLICY_VERSION,
    "weights": {
        "weighted_coverage_ratio": "50",
        "first_party_core_ratio": "20",
        "core_source_diversity_ratio": "15",
        "admission_ratio": "15",
    },
    "coverage_values": {"covered": "1", "partially_covered": "0.5", "uncovered": "0"},
    "dimension_weights": {"core": "2", "supporting": "1"},
    "rounding": "Decimal.ROUND_HALF_UP:0.1",
    "routes": {
        "full_synthesis": {"all_core_covered": True, "min_readiness": "80.0"},
        "partial_synthesis": {
            "min_supported_core_ratio": "0.50",
            "min_readiness": "35.0",
            "supported_dimensions_require_body_span": True,
        },
        "fallback": "insufficient_summary",
    },
}
READINESS_POLICY_HASH = hashlib.sha256(
    json.dumps(_READINESS_MANIFEST, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def _terms(value: str) -> frozenset[str]:
    normalized = _normalize(value).casefold()
    latin = {
        item for item in _WORD.findall(normalized)
        if len(item) >= 2 and item not in _STOPWORDS and not item.isdigit()
    }
    chars = "".join(_CJK.findall(normalized))
    # CJK bigrams are language-agnostic enough for deterministic zh/ja matching;
    # unigrams are included only for very short labels.
    cjk = {chars[index:index + 2] for index in range(max(0, len(chars) - 1))}
    if len(chars) <= 2:
        cjk.update(chars)
    return frozenset(latin | cjk)


_CONCEPT_ALIASES = (
    ("artificial_intelligence", ("artificial intelligence", "ai", "人工智能", "智能体")),
    ("latest", ("latest", "recent", "最新", "近期", "发布")),
    ("capability", ("capability", "capabilities", "能力", "性能")),
    ("benchmark", ("benchmark", "evaluation", "基准", "评测", "测试")),
    ("adoption", ("adoption", "采用", "落地", "应用")),
    ("risk", ("risk", "limitation", "风险", "限制", "缺点")),
    ("camera", ("camera", "相机", "主摄", "长焦", "超广角", "影像")),
    ("specification", ("specification", "specifications", "specs", "规格", "参数")),
    ("image_quality", ("image quality", "画质", "图像质量", "影像质量")),
    ("video", ("video", "视频")),
    ("battery", ("battery", "电池", "续航", "充电")),
    ("price", ("price", "pricing", "价格", "售价")),
    ("ecosystem", ("ecosystem", "operating system", "生态", "操作系统", "系统")),
    ("total_population", ("total population", "总人口", "全国人口", "人口总量")),
    ("birth_population", ("birth population", "births", "出生人口")),
    ("age_65", ("aged 65", "age 65", "65岁以上", "65周岁及以上")),
    ("urbanization", ("urbanization", "城镇化率", "城市化率")),
    ("official_statistics", ("official statistics", "statistics bulletin", "国家统计局", "统计公报")),
    ("education", ("education", "school", "教育", "学校")),
    ("enrollment", ("enrollment", "student population", "招生", "在校生")),
    ("urban_rural", ("urban rural", "urban-rural", "城乡", "区域均衡")),
    ("teacher", ("teacher", "教师", "师资")),
    ("finance", ("finance", "budget", "财政", "经费", "预算")),
    ("double_reduction", ("double reduction", "双减")),
    ("after_school", ("after school", "after-school", "课后服务")),
    ("burden", ("burden", "负担")),
    ("national_plan", ("national plan", "next plan", "国家计划", "下一步计划")),
)


def _contains_alias(text: str, alias: str) -> bool:
    normalized_alias = _normalize(alias).casefold()
    if normalized_alias.isascii() and re.fullmatch(r"[a-z0-9_ -]+", normalized_alias):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])",
                text,
            )
        )
    return normalized_alias in text


def _concept_relevance(query: str, candidate: str) -> float:
    normalized_query = _normalize(query).casefold()
    normalized_candidate = _normalize(candidate).casefold()
    requested = [
        aliases
        for _, aliases in _CONCEPT_ALIASES
        if any(_contains_alias(normalized_query, alias) for alias in aliases)
    ]
    if not requested:
        return 0.0
    matched = sum(
        1
        for aliases in requested
        if any(_contains_alias(normalized_candidate, alias) for alias in aliases)
    )
    return matched / len(requested)


def deterministic_relevance(
    *,
    dimension_text: str,
    query: str,
    title: str,
    body: str,
    subjects: Sequence[str] = (),
    required_anchors: Sequence[str] = (),
) -> float:
    """Return a stable multilingual lexical coverage score in ``[0, 1]``.

    Retrieval rank is intentionally not trusted as semantic relevance.  The
    score is query/dimension term recall with a small title bonus, so an
    unrelated high-ranked page cannot pass the locked 0.55 admission gate.
    """

    candidate_text = f"{title} {body}"
    dimension_score = Decimal(str(max(
        query_term_relevance(dimension_text, candidate_text),
        _concept_relevance(dimension_text, candidate_text),
    )))
    query_score = Decimal(str(max(
        query_term_relevance(query, candidate_text),
        _concept_relevance(query, candidate_text),
    )))
    subject_text = " ".join(subjects)
    subject_score = (
        Decimal(str(query_term_relevance(subject_text, candidate_text)))
        if subject_text.strip()
        else Decimal(0)
    )
    if not extract_query_terms(f"{dimension_text} {query} {subject_text}"):
        return 0.0
    score = min(
        Decimal(1),
        dimension_score * Decimal("0.65")
        + subject_score * Decimal("0.20")
        + query_score * Decimal("0.15"),
    )
    normalized_candidate = _normalize(candidate_text).casefold()
    normalized_anchors = tuple(
        dict.fromkeys(_normalize(str(item)).casefold() for item in required_anchors if str(item).strip())
    )
    if normalized_anchors and not all(anchor in normalized_candidate for anchor in normalized_anchors):
        score *= Decimal("0.60")
    return float(score.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def select_dimension_fair_rows(
    rows: Sequence[Mapping[str, Any]],
    dimension_ids: Sequence[str],
    *,
    limit: int,
) -> tuple[Mapping[str, Any], ...]:
    """Select a bounded fetch pool without letting early dimensions starve later ones.

    Search results are grouped by the locked brief order, then consumed one row
    per dimension per round. Sparse dimensions do not waste the shared budget;
    remaining dimensions continue filling it deterministically.
    """

    if limit <= 0:
        return ()
    ordered_ids = tuple(dict.fromkeys(str(item) for item in dimension_ids if str(item)))
    buckets: dict[str, dict[str, list[Mapping[str, Any]]]] = {
        item: {} for item in ordered_ids
    }
    for row in rows:
        dimension_id = str(row.get("dimension_id") or "")
        if dimension_id in buckets:
            source_target = str(row.get("source_target") or "broad_web")
            buckets[dimension_id].setdefault(source_target, []).append(row)

    target_orders = {
        dimension_id: tuple(
            sorted(
                groups,
                key=lambda target: (
                    0 if "official" in target or "primary" in target else 1,
                    target,
                ),
            )
        )
        for dimension_id, groups in buckets.items()
    }
    cursors = {dimension_id: 0 for dimension_id in ordered_ids}

    selected: list[Mapping[str, Any]] = []
    while len(selected) < limit:
        advanced = False
        for dimension_id in ordered_ids:
            targets = target_orders[dimension_id]
            if not targets:
                continue
            for offset in range(len(targets)):
                index = (cursors[dimension_id] + offset) % len(targets)
                bucket = buckets[dimension_id][targets[index]]
                if not bucket:
                    continue
                selected.append(bucket.pop(0))
                cursors[dimension_id] = (index + 1) % len(targets)
                advanced = True
                break
            if len(selected) == limit:
                break
        if not advanced:
            break
    return tuple(selected)


def _best_span(body: str, *, dimension_text: str, query: str) -> str | None:
    normalized = _normalize(body)
    if not normalized:
        return None
    reference_text = f"{dimension_text} {query}"
    reference = _terms(reference_text)
    candidates = [_normalize(item.group(0)) for item in _SENTENCE.finditer(normalized)]
    candidates = [item for item in candidates if item]
    if not candidates:
        return normalized[:1200] or None
    best = max(
        enumerate(candidates),
        key=lambda pair: (
            _concept_relevance(reference_text, pair[1]),
            len(reference & _terms(pair[1])),
            min(len(pair[1]), 1200),
            -pair[0],
        ),
    )[1]
    if not (reference & _terms(best)) and _concept_relevance(reference_text, best) <= 0:
        return None
    return best[:1200]


def _is_host(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def classify_source(*, url: str, source_kind: str) -> tuple[str, str]:
    """Derive a conservative source type/tier from the actual final URL."""

    host = (urlsplit(url).hostname or "").casefold().strip(".")
    government = host.endswith(".gov") or ".gov." in host
    education = host.endswith(".edu") or ".edu." in host
    international = any(_is_host(host, item) for item in _FIRST_PARTY_HOSTS)
    statistics = (
        "stats." in host or "statistics." in host or "statistic" in source_kind.casefold()
    )
    first_party = government or education or international
    if statistics and first_party:
        source_type = "official_statistic"
    elif government:
        source_type = "government_document"
    elif education:
        source_type = "institutional_document"
    elif international:
        source_type = "international_organization_document"
    else:
        source_type = source_kind.strip() or "web"
    return source_type, "first_party" if first_party else "secondary"


def candidate_from_document(
    *,
    dimension_id: str,
    dimension_text: str,
    query: str,
    document: Mapping[str, Any],
    subjects: Sequence[str] = (),
    required_anchors: Sequence[str] = (),
) -> EvidenceCandidate:
    url = str(document.get("final_url") or document.get("canonical_url") or document.get("url") or "")
    canonical_url = str(document.get("canonical_url") or url)
    body = str(document.get("text") or "")
    title = str(document.get("title") or url)
    flags = tuple(
        sorted({str(item).casefold() for item in (document.get("quality_flags") or ()) if str(item).strip()})
    )
    source_type, source_tier = classify_source(
        url=url,
        source_kind=str(document.get("source_kind") or "web"),
    )
    stable = str(document.get("stable_id") or document.get("content_hash") or canonical_url)
    candidate_id = "passage-" + hashlib.sha256(
        f"{dimension_id}\0{stable}\0{canonical_url}".encode()
    ).hexdigest()[:24]
    invalid_reason = next((flag for flag in flags if flag in _INVALID_FETCH_FLAGS), None)
    return EvidenceCandidate(
        candidate_id=candidate_id,
        dimension_id=dimension_id,
        url=url,
        canonical_url=canonical_url,
        canonical_target_url=(str(document["canonical_target_url"]) if document.get("canonical_target_url") else None),
        title=title,
        body_text=body,
        span_text=_best_span(body, dimension_text=dimension_text, query=query),
        relevance=deterministic_relevance(
            dimension_text=dimension_text,
            query=query,
            title=title,
            body=body,
            subjects=subjects,
            required_anchors=required_anchors,
        ),
        source_type=source_type,
        source_tier=source_tier,  # type: ignore[arg-type]
        content_hash=(str(document["content_hash"]) if document.get("content_hash") else None),
        issuing_body=(str(document["issuing_body"]) if document.get("issuing_body") else None),
        document_number=(str(document["document_number"]) if document.get("document_number") else None),
        published_date=(str(document.get("published_at") or document.get("published_date")) if (document.get("published_at") or document.get("published_date")) else None),
        invalid_reason=invalid_reason,
        page_flags=flags,
    )


def _candidate_json(candidate: EvidenceCandidate, *, admitted: bool, family_id: str | None) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "dimension_id": candidate.dimension_id,
        "url": candidate.url,
        "canonical_url": candidate.canonical_url,
        "canonical_target_url": candidate.canonical_target_url,
        "title": candidate.title,
        "body_text": candidate.body_text,
        "span_text": candidate.span_text,
        "relevance": candidate.relevance,
        "source_type": candidate.source_type,
        "source_tier": candidate.source_tier,
        "content_hash": candidate.content_hash,
        "issuing_body": candidate.issuing_body,
        "document_number": candidate.document_number,
        "published_date": candidate.published_date,
        "invalid_reason": candidate.invalid_reason,
        "page_flags": list(candidate.page_flags),
        "family_id": family_id,
        "admitted": admitted,
    }


def candidate_from_json(value: Mapping[str, Any]) -> EvidenceCandidate:
    return EvidenceCandidate(
        candidate_id=str(value["candidate_id"]),
        dimension_id=str(value["dimension_id"]),
        url=str(value["url"]),
        canonical_url=(str(value["canonical_url"]) if value.get("canonical_url") else None),
        canonical_target_url=(str(value["canonical_target_url"]) if value.get("canonical_target_url") else None),
        title=str(value["title"]),
        body_text=str(value.get("body_text") or ""),
        span_text=(str(value["span_text"]) if value.get("span_text") else None),
        relevance=float(value.get("relevance") or 0.0),
        source_type=str(value.get("source_type") or "web"),
        source_tier=str(value.get("source_tier") or "secondary"),  # type: ignore[arg-type]
        content_hash=(str(value["content_hash"]) if value.get("content_hash") else None),
        issuing_body=(str(value["issuing_body"]) if value.get("issuing_body") else None),
        document_number=(str(value["document_number"]) if value.get("document_number") else None),
        published_date=(str(value["published_date"]) if value.get("published_date") else None),
        invalid_reason=(str(value["invalid_reason"]) if value.get("invalid_reason") else None),
        page_flags=tuple(str(item) for item in (value.get("page_flags") or ())),
    )


def _q(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _nonduplicate_fetched_count(candidates: Sequence[EvidenceCandidate]) -> int:
    parent = {item.candidate_id: item.candidate_id for item in candidates}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            low, high = sorted((a, b))
            parent[high] = low

    for index, left in enumerate(candidates):
        for right in candidates[index + 1:]:
            if match_source_family(left, right).level in {"exact", "strong"}:
                union(left.candidate_id, right.candidate_id)
    return len({find(item.candidate_id) for item in candidates})


class EvidenceReadinessV1:
    """Locked arithmetic and route boundaries for pre-synthesis evidence."""

    policy_id = READINESS_POLICY_ID
    version = READINESS_POLICY_VERSION
    policy_hash = READINESS_POLICY_HASH

    @staticmethod
    def manifest() -> Mapping[str, Any]:
        return json.loads(json.dumps(_READINESS_MANIFEST))

    @staticmethod
    def evaluate(
        *,
        coverage_ratio: Decimal,
        first_party_ratio: Decimal,
        diversity_ratio: Decimal,
        admission_ratio: Decimal,
        supported_core_ratio: Decimal,
        all_core_covered: bool,
        supported_have_span: bool,
    ) -> dict[str, Any]:
        readiness = _q(
            Decimal(50) * coverage_ratio
            + Decimal(20) * first_party_ratio
            + Decimal(15) * diversity_ratio
            + Decimal(15) * admission_ratio
        )
        if all_core_covered and readiness >= 80.0:
            route = "full_synthesis"
        elif (
            supported_core_ratio >= Decimal("0.50")
            and readiness >= 35.0
            and supported_have_span
        ):
            route = "partial_synthesis"
        else:
            route = "insufficient_summary"
        return {
            "readiness": readiness,
            "synthesis_route": route,
            "readiness_components": {
                "weighted_coverage_ratio": _q(coverage_ratio),
                "first_party_core_ratio": _q(first_party_ratio),
                "core_source_diversity_ratio": _q(diversity_ratio),
                "admission_ratio": _q(admission_ratio),
                "supported_core_ratio": _q(supported_core_ratio),
            },
        }


def evaluate_runtime_evidence(
    *,
    brief: ResearchBrief,
    candidates: Sequence[EvidenceCandidate],
) -> dict[str, Any]:
    """Evaluate, compute locked readiness, and choose the sole synth route."""

    # Rescue can rediscover the same passage.  Deduplicate by stable candidate
    # id before invoking the strict policy; last-write semantics are forbidden.
    unique = {item.candidate_id: item for item in candidates}
    ordered = tuple(unique[key] for key in sorted(unique))
    evaluation = EvidenceAdmissionPolicyV1.evaluate(brief.dimensions, ordered)
    coverage_by_id = {item.dimension_id: item for item in evaluation.dimension_coverages}
    metrics_by_id = {item.dimension_id: item for item in evaluation.coverage_metrics}

    def weight(dimension: Any) -> Decimal:
        return Decimal(2 if dimension.importance == "core" else 1)

    applicable = [item for item in brief.dimensions if coverage_by_id[item.dimension_id].status != "not_applicable"]
    coverage_den = sum((weight(item) for item in applicable), Decimal(0))
    coverage_num = sum(
        (
            weight(item)
            * ({"covered": Decimal(1), "partially_covered": Decimal("0.5")}.get(
                coverage_by_id[item.dimension_id].status, Decimal(0)
            ))
            for item in applicable
        ),
        Decimal(0),
    )
    coverage_ratio = coverage_num / coverage_den if coverage_den else Decimal(0)

    required_core = [
        item for item in applicable
        if item.importance == "core" and item.first_party_required
    ]
    first_den = sum((weight(item) for item in required_core), Decimal(0))
    first_num = sum(
        (weight(item) for item in required_core if coverage_by_id[item.dimension_id].first_party_satisfied),
        Decimal(0),
    )
    first_ratio = first_num / first_den if first_den else Decimal(0)

    supported_core = [
        item for item in applicable
        if item.importance == "core"
        and coverage_by_id[item.dimension_id].status in {"covered", "partially_covered"}
    ]
    core = [item for item in applicable if item.importance == "core"]
    diversity_den = sum((weight(item) for item in core), Decimal(0))
    diversity_num = sum(
        (weight(item) for item in core if metrics_by_id[item.dimension_id].strong_distinct_families >= 2),
        Decimal(0),
    )
    diversity_ratio = diversity_num / diversity_den if diversity_den else Decimal(0)

    admitted_count = len(evaluation.admitted_passages)
    # Exact/strong folded families are the policy's authoritative non-duplicate
    # passage units.  Invalid/rejected unique pages remain in the denominator.
    nonduplicate_fetched = _nonduplicate_fetched_count(ordered)
    admission_ratio = (
        min(Decimal(1), Decimal(admitted_count) / Decimal(nonduplicate_fetched))
        if nonduplicate_fetched else Decimal(0)
    )
    core_den = sum((weight(item) for item in core), Decimal(0))
    supported_core_ratio = (
        sum((weight(item) for item in supported_core), Decimal(0)) / core_den
        if core_den else Decimal(0)
    )
    admitted_by_id = {item.candidate.candidate_id: item for item in evaluation.admitted_passages}
    all_core_covered = bool(core) and all(
        coverage_by_id[item.dimension_id].status == "covered" for item in core
    )
    supported_have_span = bool(supported_core) and all(
        any(
            candidate_id in admitted_by_id
            and bool(admitted_by_id[candidate_id].candidate.span_text)
            for candidate_id in coverage_by_id[item.dimension_id].evidence_passage_ids
        )
        for item in supported_core
    )
    readiness_result = EvidenceReadinessV1.evaluate(
        coverage_ratio=coverage_ratio,
        first_party_ratio=first_ratio,
        diversity_ratio=diversity_ratio,
        admission_ratio=admission_ratio,
        supported_core_ratio=supported_core_ratio,
        all_core_covered=all_core_covered,
        supported_have_span=supported_have_span,
    )

    family_by_candidate = {
        item.candidate.candidate_id: item.family_id for item in evaluation.admitted_passages
    }
    admitted_ids = set(family_by_candidate)
    decision_by_candidate = {item.candidate_id: item for item in evaluation.decisions}
    rejection_reason_counts: dict[str, int] = {}
    rejection_by_dimension: dict[str, dict[str, int]] = {}
    for decision in evaluation.decisions:
        if decision.accepted:
            continue
        rejection_reason_counts[decision.reason] = rejection_reason_counts.get(decision.reason, 0) + 1
        dimension_counts = rejection_by_dimension.setdefault(decision.dimension_id, {})
        dimension_counts[decision.reason] = dimension_counts.get(decision.reason, 0) + 1
    return {
        "policy_hash": evaluation.policy_hash,
        "readiness_policy_hash": EvidenceReadinessV1.policy_hash,
        "evidence_candidates": [
            {
                **_candidate_json(
                    item,
                    admitted=item.candidate_id in admitted_ids,
                    family_id=family_by_candidate.get(item.candidate_id),
                ),
                "admission_stage": decision_by_candidate[item.candidate_id].stage,
                "rejection_reason": (
                    None
                    if decision_by_candidate[item.candidate_id].accepted
                    else decision_by_candidate[item.candidate_id].reason
                ),
            }
            for item in ordered
        ],
        "admission_decisions": [
            {
                "candidate_id": item.candidate_id,
                "dimension_id": item.dimension_id,
                "accepted": item.accepted,
                "stage": item.stage,
                "reason": item.reason,
            }
            for item in evaluation.decisions
        ],
        "rejection_reason_counts": dict(sorted(rejection_reason_counts.items())),
        "rejection_summary_by_dimension": [
            {
                "dimension_id": dimension_id,
                "reason_counts": dict(sorted(reason_counts.items())),
            }
            for dimension_id, reason_counts in sorted(rejection_by_dimension.items())
        ],
        "coverages": [item.to_json() for item in evaluation.dimension_coverages],
        "source_families": [item.to_json() for item in evaluation.source_families],
        **readiness_result,
        "quality_score": readiness_result["readiness"],
    }


__all__ = [
    "READINESS_POLICY_HASH",
    "EvidenceReadinessV1",
    "candidate_from_document",
    "candidate_from_json",
    "classify_source",
    "deterministic_relevance",
    "evaluate_runtime_evidence",
    "select_dimension_fair_rows",
]
