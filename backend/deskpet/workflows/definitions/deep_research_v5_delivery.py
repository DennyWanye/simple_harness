"""Fail-closed production adapter for the DeepResearch v5 report contract.

The model proposes structured analysis and claims.  Evidence identity, source
metadata, Markdown rendering, linting, scoring, and the delivery decision stay
deterministic and server-owned.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from .deep_research_v5_contracts import (
    DimensionAnalysis,
    DimensionCoverage,
    JsonValue,
    ResearchBrief,
)
from .deep_research_v3_contracts import AtomicClaim, EvidencePassage
from .deep_research_v3_quality import evaluate_support
from .deep_research_v5_report import (
    REPORT_QUALITY_RUBRIC_V1_HASH,
    ReportClaim,
    ReportSource,
    audit_report_quality,
    lint_rendered_report,
    render_report,
)


class ReportDraftError(ValueError):
    """A model draft attempted to widen or violate the evidence contract."""


_EXTRACTIVE_SENTENCE = re.compile(r"[^。！？.!?\n]+[。！？.!?]?", re.UNICODE)
_EXTRACTIVE_TOKEN = re.compile(
    r"[a-zA-Z][a-zA-Z0-9_.+-]*|\d+(?:\.\d+)?|[\u4e00-\u9fff]",
    re.UNICODE,
)


def _extractive_tokens(text: object) -> set[str]:
    return {
        value.casefold()
        for value in _EXTRACTIVE_TOKEN.findall(str(text or ""))
        if value.strip()
    }


def _extractive_snippets(text: object, *, maximum: int = 180) -> tuple[str, ...]:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if not compact:
        return ()
    values: list[str] = []
    for match in _EXTRACTIVE_SENTENCE.finditer(compact):
        sentence = match.group(0).strip()
        if len(sentence) < 24:
            continue
        if len(sentence) > maximum:
            boundary = sentence.rfind(" ", 0, maximum + 1)
            sentence = sentence[: boundary if boundary >= 24 else maximum].strip()
        if sentence and sentence not in values:
            values.append(sentence)
    if values:
        return tuple(values)
    if len(compact) > maximum:
        boundary = compact.rfind(" ", 0, maximum + 1)
        compact = compact[: boundary if boundary >= 24 else maximum].strip()
    return (compact,) if compact else ()


def _records(value: object, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ReportDraftError(f"{field} must be an array of objects")
    return list(value)


def _claims(value: object) -> tuple[ReportClaim, ...]:
    result: list[ReportClaim] = []
    for raw in _records(value, "claims"):
        result.append(
            ReportClaim(
                claim_id=str(raw.get("claim_id") or ""),
                dimension_id=str(raw.get("dimension_id") or ""),
                text=str(raw.get("text") or ""),
                kind=str(raw.get("kind") or ""),  # type: ignore[arg-type]
                source_ids=tuple(str(item) for item in raw.get("source_ids", [])),
                supported_fact_refs=tuple(
                    str(item) for item in raw.get("supported_fact_refs", [])
                ),
                intent_tokens=tuple(str(item) for item in raw.get("intent_tokens", [])),
                is_inference=bool(raw.get("is_inference", False)),
                metadata_pseudo_judgment=bool(
                    raw.get("metadata_pseudo_judgment", False)
                ),
            )
        )
    return tuple(result)


def _analyses(value: object) -> tuple[DimensionAnalysis, ...]:
    return tuple(DimensionAnalysis.from_json(raw) for raw in _records(value, "analyses"))


def _server_sources(
    evidence_candidates: Sequence[Mapping[str, Any]],
) -> tuple[ReportSource, ...]:
    sources: list[ReportSource] = []
    seen: set[str] = set()
    official_families = {
        str(item.get("family_id") or "")
        for item in evidence_candidates
        if bool(item.get("admitted", False))
        and str(item.get("source_tier") or "") == "first_party"
    }
    for raw in evidence_candidates:
        if not bool(raw.get("admitted", False)):
            continue
        source_id = str(raw.get("candidate_id") or "")
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        flags = {str(item) for item in raw.get("page_flags", [])}
        published = str(raw.get("published_date") or "")
        sources.append(
            ReportSource(
                source_id=source_id,
                dimension_id=str(raw.get("dimension_id") or ""),
                family_id=str(raw.get("family_id") or ""),
                title=str(raw.get("title") or raw.get("url") or ""),
                url=str(raw.get("canonical_url") or raw.get("url") or ""),
                first_party=str(raw.get("source_tier") or "") == "first_party",
                strong_family=True,
                winning=True,
                invalid_page=bool(raw.get("invalid_reason")) or bool(
                    flags
                    & {
                        "invalid",
                        "login",
                        "login_wall",
                        "security_verification",
                        "app_shell",
                        "navigation_only",
                        "search_redirect",
                        "blank",
                        "garbled",
                        "title_mismatch",
                    }
                ),
                captcha="captcha" in flags,
                aggregator_replacement=(
                    str(raw.get("source_tier") or "") == "aggregator"
                ),
                secondary_replaces_available_official=(
                    bool(raw.get("secondary_replaces_available_official", False))
                    or (
                        str(raw.get("source_tier") or "") != "first_party"
                        and str(raw.get("family_id") or "") in official_families
                    )
                ),
                time_sensitive=bool(published),
                within_time_window=bool(raw.get("within_time_window", False)),
                explicit_no_new_evidence=bool(raw.get("explicit_no_new_evidence", False)),
            )
        )
    return tuple(sources)


def _validate_evidence_bounds(
    *,
    brief: ResearchBrief,
    coverages: Sequence[DimensionCoverage],
    analyses: Sequence[DimensionAnalysis],
    claims: Sequence[ReportClaim],
    sources: Sequence[ReportSource],
    evidence_candidates: Sequence[Mapping[str, Any]],
) -> None:
    dimensions = {item.dimension_id for item in brief.dimensions}
    coverage = {item.dimension_id: item for item in coverages}
    if set(coverage) != dimensions:
        raise ReportDraftError("coverage must contain every research dimension")
    source_by_id = {item.source_id: item for item in sources}
    candidate_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in evidence_candidates
        if bool(item.get("admitted", False))
    }
    for item in analyses:
        if item.dimension_id not in dimensions:
            raise ReportDraftError("analysis references an unknown dimension")
        allowed = set(coverage[item.dimension_id].evidence_passage_ids)
        if not set(item.winning_evidence_ids) <= allowed:
            raise ReportDraftError("analysis widened beyond admitted evidence")
    for claim in claims:
        if claim.dimension_id not in dimensions:
            raise ReportDraftError("claim references an unknown dimension")
        allowed = set(coverage[claim.dimension_id].evidence_passage_ids)
        if not set(claim.supported_fact_refs) <= allowed:
            raise ReportDraftError("claim fact refs widened beyond admitted evidence")
        if any(
            source_id not in source_by_id
            or source_by_id[source_id].dimension_id != claim.dimension_id
            or source_id not in allowed
            for source_id in claim.source_ids
        ):
            raise ReportDraftError("claim citation widened or crossed dimensions")
    ordered_ids = sorted(candidate_by_id)
    citation_ids = {candidate_id: index for index, candidate_id in enumerate(ordered_ids, 1)}
    passages: list[EvidencePassage] = []
    for candidate_id in ordered_ids:
        raw = candidate_by_id[candidate_id]
        text = str(raw.get("span_text") or raw.get("body_text") or "")
        if not text.strip():
            raise ReportDraftError("admitted evidence is missing its supporting passage span")
        passages.append(
            EvidencePassage(
                passage_id=candidate_id,
                source_citation_id=citation_ids[candidate_id],
                source_content_hash=str(raw.get("content_hash") or candidate_id),
                canonical_url=str(raw.get("canonical_url") or raw.get("url") or ""),
                question_id=str(raw.get("dimension_id") or ""),
                text=text,
                start=0,
                end=len(text),
                relevance=float(raw.get("relevance") or 0.0),
            )
        )
    support_claims: list[AtomicClaim] = []
    for claim in claims:
        refs = tuple(dict.fromkeys((*claim.source_ids, *claim.supported_fact_refs)))
        support_claims.append(
            AtomicClaim(
                claim_id=claim.claim_id,
                text=claim.text,
                kind="factual",
                citation_ids=tuple(citation_ids[item] for item in refs),
            )
        )
    for analysis in analyses:
        if analysis.direct_answer:
            support_claims.append(
                AtomicClaim(
                    claim_id=f"direct-answer:{analysis.dimension_id}",
                    text=analysis.direct_answer,
                    kind="factual",
                    citation_ids=tuple(
                        citation_ids[item] for item in analysis.winning_evidence_ids
                    ),
                )
            )
    unsupported = [
        item.claim_id
        for item in evaluate_support(support_claims, passages)
        if not item.supported
    ]
    if unsupported:
        raise ReportDraftError(
            f"passage text does not support report claims: {sorted(unsupported)}"
        )


def build_extractively_grounded_draft(
    *,
    brief: ResearchBrief,
    coverages: Sequence[DimensionCoverage],
    draft: Mapping[str, Any],
    evidence_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, JsonValue]:
    """Narrow a schema-valid model draft to verbatim admitted evidence spans.

    This is a fail-closed recovery path for a common synthesis defect: the
    model selected the right evidence but paraphrased it too aggressively for
    the deterministic lexical support gate.  The recovery never invents a
    source or a claim.  It keeps the model's claim kind and evidence binding,
    replaces prose with a short extractive sentence from that same admitted
    passage, and drops dimensions without admitted evidence.
    """

    coverage_by_id = {item.dimension_id: item for item in coverages}
    if set(coverage_by_id) != {item.dimension_id for item in brief.dimensions}:
        raise ReportDraftError("coverage must contain every research dimension")
    candidate_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in evidence_candidates
        if bool(item.get("admitted", False))
        and str(item.get("candidate_id") or "")
        and _extractive_snippets(item.get("span_text") or item.get("body_text"))
    }

    entries_by_dimension: dict[str, list[tuple[str, str]]] = {}
    for dimension in brief.dimensions:
        coverage = coverage_by_id[dimension.dimension_id]
        allowed_ids = tuple(
            dict.fromkeys(
                (*coverage.winning_evidence_ids, *coverage.evidence_passage_ids)
            )
        )
        entries: list[tuple[str, str]] = []
        for candidate_id in allowed_ids:
            raw = candidate_by_id.get(candidate_id)
            if raw is None or str(raw.get("dimension_id") or "") != dimension.dimension_id:
                continue
            for snippet in _extractive_snippets(
                raw.get("span_text") or raw.get("body_text")
            ):
                entries.append((candidate_id, snippet))
        if entries:
            entries_by_dimension[dimension.dimension_id] = entries

    def choose_entry(
        dimension_id: str,
        original_text: object,
        preferred_ids: Sequence[object] = (),
    ) -> tuple[str, str] | None:
        entries = entries_by_dimension.get(dimension_id, [])
        if not entries:
            return None
        preferred = {str(item) for item in preferred_ids}
        narrowed = [item for item in entries if item[0] in preferred] or entries
        original_tokens = _extractive_tokens(original_text)

        def rank(item: tuple[str, str]) -> tuple[float, int, str, str]:
            snippet_tokens = _extractive_tokens(item[1])
            overlap = (
                len(original_tokens & snippet_tokens) / len(original_tokens)
                if original_tokens
                else 0.0
            )
            return (-overlap, len(item[1]), item[0], item[1])

        return min(narrowed, key=rank)

    raw_claims = _records(draft.get("claims"), "claims")
    intent_vocabulary = _extractive_tokens(
        " ".join((brief.user_question, *brief.subjects))
    )
    transformed_claims: list[dict[str, JsonValue]] = []
    seen_claim_ids: set[str] = set()
    key_count = 0
    for index, raw in enumerate(raw_claims, 1):
        dimension_id = str(raw.get("dimension_id") or "")
        kind = str(raw.get("kind") or "")
        if dimension_id not in coverage_by_id or kind not in {
            "key_judgment",
            "current_state",
            "driver_change",
            "impact",
            "next_step",
            "uncertainty",
            "counterevidence",
            "forecast",
        }:
            continue
        if kind == "key_judgment" and key_count >= 7:
            continue
        preferred_ids = [
            *raw.get("source_ids", []),
            *raw.get("supported_fact_refs", []),
        ] if isinstance(raw.get("source_ids", []), list) and isinstance(
            raw.get("supported_fact_refs", []), list
        ) else []
        selected = choose_entry(dimension_id, raw.get("text"), preferred_ids)
        if selected is None:
            continue
        candidate_id, snippet = selected
        claim_id = str(raw.get("claim_id") or f"extractive-claim-{index}")
        if claim_id in seen_claim_ids:
            claim_id = f"{claim_id}-{index}"
        seen_claim_ids.add(claim_id)
        intent_tokens = raw.get("intent_tokens", [])
        truthful_intent_tokens = sorted(
            {
                token
                for token in (_extractive_tokens(snippet) & intent_vocabulary)
                if len(token) >= 2
            },
            key=lambda token: (-len(token), token),
        )[:6]
        original_intent_tokens = [
            str(item)
            for item in intent_tokens
            if isinstance(item, str) and item.strip()
        ] if isinstance(intent_tokens, list) else []
        transformed_claims.append(
            {
                "claim_id": claim_id,
                "dimension_id": dimension_id,
                "text": snippet,
                "kind": kind,
                "source_ids": [candidate_id],
                "supported_fact_refs": [candidate_id],
                "intent_tokens": list(
                    dict.fromkeys((*original_intent_tokens, *truthful_intent_tokens))
                ),
                "is_inference": bool(raw.get("is_inference", False)),
                "metadata_pseudo_judgment": False,
            }
        )
        if kind == "key_judgment":
            key_count += 1
    if not 3 <= key_count <= 7:
        raise ReportDraftError(
            "extractive recovery requires 3 to 7 evidence-backed key judgments"
        )

    raw_analysis_by_dimension = {
        str(item.get("dimension_id") or ""): item
        for item in _records(draft.get("analyses"), "analyses")
    }
    claims_by_dimension: dict[str, list[Mapping[str, JsonValue]]] = {}
    for claim in transformed_claims:
        claims_by_dimension.setdefault(str(claim["dimension_id"]), []).append(claim)
    transformed_analyses: list[dict[str, JsonValue]] = []
    for dimension in brief.dimensions:
        dimension_id = dimension.dimension_id
        original = raw_analysis_by_dimension.get(dimension_id, {})
        preferred = original.get("winning_evidence_ids", [])
        selected = choose_entry(
            dimension_id,
            original.get("direct_answer"),
            preferred if isinstance(preferred, list) else (),
        )
        if selected is None:
            continue
        candidate_id, snippet = selected
        dimension_claims = claims_by_dimension.get(dimension_id, [])
        fact_ids = [
            str(item["claim_id"])
            for item in dimension_claims
            if not bool(item["is_inference"])
        ]
        inference_ids = [
            str(item["claim_id"])
            for item in dimension_claims
            if bool(item["is_inference"])
        ]
        limitation_ids = [
            str(item["claim_id"])
            for item in dimension_claims
            if str(item["kind"]) in {"uncertainty", "counterevidence", "forecast"}
        ]
        coverage = coverage_by_id[dimension_id]
        transformed_analyses.append(
            {
                "schema_version": 1,
                "dimension_id": dimension_id,
                "direct_answer": snippet,
                "fact_claim_ids": fact_ids,
                "inference_claim_ids": inference_ids,
                "limitation_claim_ids": limitation_ids,
                "winning_evidence_ids": [candidate_id],
                "relevance_score": coverage.relevance_score,
                "confidence": "high" if coverage.status == "covered" else "medium",
            }
        )
    if not transformed_analyses:
        raise ReportDraftError("extractive recovery found no admitted evidence")
    return {
        "analyses": transformed_analyses,
        "claims": transformed_claims,
    }


def evaluate_structured_report(
    *,
    brief: ResearchBrief,
    coverages: Sequence[DimensionCoverage],
    draft: Mapping[str, Any],
    evidence_candidates: Sequence[Mapping[str, Any]],
    audited_at: str | None = None,
) -> dict[str, JsonValue]:
    """Render and score one structured draft, rejecting evidence widening."""

    analyses = _analyses(draft.get("analyses"))
    claims = _claims(draft.get("claims"))
    sources = _server_sources(evidence_candidates)
    _validate_evidence_bounds(
        brief=brief,
        coverages=coverages,
        analyses=analyses,
        claims=claims,
        sources=sources,
        evidence_candidates=evidence_candidates,
    )
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
        audited_at=audited_at or datetime.now(UTC).isoformat(),
    )
    return {
        "report_md": markdown,
        "analyses": [item.to_json() for item in analyses],
        "claims": [
            {
                "claim_id": item.claim_id,
                "dimension_id": item.dimension_id,
                "text": item.text,
                "kind": item.kind,
                "source_ids": list(item.source_ids),
                "supported_fact_refs": list(item.supported_fact_refs),
                "intent_tokens": list(item.intent_tokens),
                "is_inference": item.is_inference,
                "metadata_pseudo_judgment": item.metadata_pseudo_judgment,
            }
            for item in claims
        ],
        "sources": [
            {
                "source_id": item.source_id,
                "dimension_id": item.dimension_id,
                "family_id": item.family_id,
                "title": item.title,
                "url": item.url,
            }
            for item in sources
        ],
        "quality_audit": result.audit.to_json(),
        "delivery_status": result.delivery_status,
        "reason_codes": list(result.reason_codes),
        "rubric_hash": REPORT_QUALITY_RUBRIC_V1_HASH,
    }


def failed_report_evaluation(reason: str) -> dict[str, JsonValue]:
    """Return a deterministic insufficient result for an invalid model draft."""

    return {
        "report_md": "",
        "analyses": [],
        "claims": [],
        "sources": [],
        "quality_audit": {
            "schema_version": 1,
            "audit_id": "report-quality-v1-invalid-draft",
            "rubric_version": "ReportQualityRubricV1",
            "relevance": 0.0,
            "coverage": 0.0,
            "source_quality": 0.0,
            "synthesis_reasoning": 0.0,
            "timeliness_uncertainty": 0.0,
            "readability": 0.0,
            "total_score": 0.0,
            "hard_failures": ["unsupported_key_claim"],
            "defects": [str(reason)[:240]],
            "allowed_repairs": [],
            "passed": False,
            "audited_at": datetime.now(UTC).isoformat(),
        },
        "delivery_status": "insufficient_evidence",
        "reason_codes": ["invalid_structured_report"],
        "rubric_hash": REPORT_QUALITY_RUBRIC_V1_HASH,
    }


def apply_atomic_repair(
    previous: Mapping[str, Any],
    repair: Mapping[str, Any],
    *,
    allowed_action: str,
    expected_dimension_id: str,
) -> dict[str, JsonValue]:
    """Apply one dimension-scoped repair without accepting model-owned sources."""

    if str(repair.get("repair_action") or "") != allowed_action:
        raise ReportDraftError("repair action does not match the deterministic audit")
    dimension_id = str(repair.get("dimension_id") or "")
    if not dimension_id:
        raise ReportDraftError("repair must identify one affected dimension")
    if dimension_id != expected_dimension_id:
        raise ReportDraftError("repair dimension does not match the deterministic target")
    updated = copy.deepcopy(dict(previous))
    previous_claims = _records(updated.get("claims"), "claims")
    replacement_claims = _records(repair.get("replacement_claims"), "replacement_claims")
    if any(str(item.get("dimension_id") or "") != dimension_id for item in replacement_claims):
        raise ReportDraftError("repair claims must stay inside one dimension")
    updated["claims"] = [
        copy.deepcopy(dict(item))
        for item in previous_claims
        if str(item.get("dimension_id") or "") != dimension_id
    ] + [copy.deepcopy(dict(item)) for item in replacement_claims]
    if repair.get("replacement_analysis") is not None:
        replacement = repair["replacement_analysis"]
        if not isinstance(replacement, Mapping) or str(replacement.get("dimension_id") or "") != dimension_id:
            raise ReportDraftError("replacement analysis must stay inside one dimension")
        updated["analyses"] = [
            copy.deepcopy(dict(item))
            for item in _records(updated.get("analyses"), "analyses")
            if str(item.get("dimension_id") or "") != dimension_id
        ] + [copy.deepcopy(dict(replacement))]
    updated.pop("report_md", None)
    updated.pop("quality_audit", None)
    updated.pop("delivery_status", None)
    return updated  # type: ignore[return-value]


def select_repair_dimension(
    *,
    action: str,
    brief: ResearchBrief,
    coverages: Sequence[DimensionCoverage],
    evaluated: Mapping[str, Any],
    evidence_candidates: Sequence[Mapping[str, Any]],
) -> str | None:
    """Select one stable repair target from server-owned evidence and audit state."""

    coverage_by_id = {item.dimension_id: item for item in coverages}
    claims = [item for item in evaluated.get("claims", []) if isinstance(item, Mapping)]
    analyses = {
        str(item.get("dimension_id") or ""): item
        for item in evaluated.get("analyses", [])
        if isinstance(item, Mapping)
    }
    official_families = {
        str(item.get("family_id") or "")
        for item in evidence_candidates
        if bool(item.get("admitted", False))
        and str(item.get("source_tier") or "") == "first_party"
    }
    defective_secondary_ids = {
        str(item.get("candidate_id") or "")
        for item in evidence_candidates
        if bool(item.get("admitted", False))
        and str(item.get("source_tier") or "") != "first_party"
        and str(item.get("family_id") or "") in official_families
    }
    for dimension in brief.dimensions:
        dimension_id = dimension.dimension_id
        coverage = coverage_by_id[dimension_id]
        dimension_claims = [
            item for item in claims if str(item.get("dimension_id") or "") == dimension_id
        ]
        if action == "complete_dimension_analysis" and coverage.status in {
            "covered",
            "partially_covered",
        }:
            supported_kinds = {
                str(item.get("kind") or "")
                for item in dimension_claims
                if item.get("source_ids") and item.get("supported_fact_refs")
            }
            if dimension_id not in analyses or not {
                "current_state",
                "driver_change",
                "impact",
            } <= supported_kinds:
                return dimension_id
        elif action == "replace_secondary_citation" and any(
            str(item.get("dimension_id") or "") == dimension_id
            and any(
                str(source_id) in defective_secondary_ids
                for source_id in item.get("source_ids", [])
            )
            for item in dimension_claims
        ):
            return dimension_id
        elif action == "narrow_unsupported_claim" and any(
            str(item.get("kind") or "") == "key_judgment"
            and (
                not item.get("source_ids")
                or not item.get("supported_fact_refs")
            )
            for item in dimension_claims
        ):
            return dimension_id
        elif action == "add_uncertainty" and coverage.status == "partially_covered":
            return dimension_id
        elif action == "rewrite_readability" and dimension_claims:
            return dimension_id
    return None


__all__ = [
    "ReportDraftError",
    "apply_atomic_repair",
    "build_extractively_grounded_draft",
    "evaluate_structured_report",
    "failed_report_evaluation",
    "select_repair_dimension",
]
