"""Deterministic DeepResearch v5 report rendering and quality rubric.

This module is intentionally graph- and LLM-free.  It consumes only validated
v5 contracts plus the small report-facing records below, renders a stable
professional Markdown shape, and evaluates ReportQualityRubricV1.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from .deep_research_v5_contracts import (
    ContractValidationError,
    DimensionAnalysis,
    DimensionCoverage,
    ReportQualityAudit,
    ResearchBrief,
)

ClaimKind = Literal[
    "key_judgment",
    "current_state",
    "driver_change",
    "impact",
    "next_step",
    "uncertainty",
    "counterevidence",
    "forecast",
]
DeliveryStatus = Literal["completed", "partial", "insufficient_evidence"]

_CLAIM_KINDS = {
    "key_judgment",
    "current_state",
    "driver_change",
    "impact",
    "next_step",
    "uncertainty",
    "counterevidence",
    "forecast",
}
_REQUIRED_HEADINGS = (
    "## 关键判断",
    "## 分维度分析",
    "## 风险与不确定性",
    "## 来源与方法",
)
_DIAGNOSTIC_PATTERNS = (
    r"(?im)^#{1,6}\s*coverage\s*$",
    r"(?i)raw diagnostics?",
    r"(?i)coverage_json",
    r"(?i)evidence_passage_ids",
    r"(?i)winning_evidence_ids",
    r"(?i)gap_reasons",
    r"(?i)rubric_score",
    r"(?i)internal diagnostics?",
)


REPORT_QUALITY_RUBRIC_V1_MANIFEST: dict[str, object] = {
    "schema": "ReportQualityRubricV1",
    "rounding": "Decimal ROUND_HALF_UP 0.1 per clamped component then total",
    "weighting": "equal weight per applicable core dimension",
    "empty_denominator": "0 except no uncertainty-target dimensions gives full 5",
    "claim_support": "key and analysis claims require fact refs; citations resolve to the same dimension; inference ids are explicitly marked",
    "intent_mapping": "a key judgment token must occur in the user question or subjects and in the rendered claim text or its bound core-dimension question",
    "source_distinctness": "count distinct family ids per core; duplicate citations never increase score",
    "components": {
        "relevance": {
            "max": 25,
            "formula": "15*direct_answer_relevant + 10*intent_mapped_key_judgment",
        },
        "coverage": {
            "max": 25,
            "formula": "20*weighted_coverage + 5*conclusion_winning_evidence_binding",
        },
        "source_quality": {
            "max": 20,
            "formula": "10*first_party + 5*two_strong_families + 5*all_winners_clean",
        },
        "synthesis_reasoning": {
            "max": 15,
            "formula": "5*current_state + 5*driver_change + 5*impact",
        },
        "timeliness_uncertainty": {
            "max": 10,
            "formula": "5*temporal_window_or_no_new_evidence + 5*uncertainty_or_counterevidence",
        },
        "readability": {
            "max": 5,
            "formula": "2*section_order + 2*first_screen + 1*clean_copy",
        },
    },
    "hard_failures": (
        "completed_core_uncovered",
        "unsupported_key_claim",
        "invalid_or_captcha_citation",
        "secondary_replaces_available_official",
        "citation_dimension_mismatch",
        "internal_diagnostics_leak",
    ),
    "decision": {
        "completed": "score>=80 and all applicable core covered and no hard failure",
        "partial": "score>=60 and all key claims supported and evidence_backed_core>=max(1,ceil(core*0.5)) and no universal hard failure",
        "insufficient_evidence": "otherwise",
    },
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


REPORT_QUALITY_RUBRIC_V1_HASH = hashlib.sha256(
    _canonical_json(REPORT_QUALITY_RUBRIC_V1_MANIFEST).encode("utf-8")
).hexdigest()


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{field} must be non-empty text")


def _require_strings(values: Sequence[str], field: str) -> None:
    if not isinstance(values, (tuple, list)):
        raise ContractValidationError(f"{field} must be a string sequence")
    for value in values:
        _require_text(value, field)


@dataclass(frozen=True, slots=True)
class ReportClaim:
    claim_id: str
    dimension_id: str
    text: str
    kind: ClaimKind
    source_ids: tuple[str, ...]
    supported_fact_refs: tuple[str, ...]
    intent_tokens: tuple[str, ...] = ()
    is_inference: bool = False
    metadata_pseudo_judgment: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "supported_fact_refs", tuple(self.supported_fact_refs))
        object.__setattr__(self, "intent_tokens", tuple(self.intent_tokens))
        for field in ("claim_id", "dimension_id", "text"):
            _require_text(getattr(self, field), field)
        if self.kind not in _CLAIM_KINDS:
            raise ContractValidationError(f"kind must be one of {sorted(_CLAIM_KINDS)}")
        _require_strings(self.source_ids, "source_ids")
        _require_strings(self.supported_fact_refs, "supported_fact_refs")
        _require_strings(self.intent_tokens, "intent_tokens")
        if not isinstance(self.is_inference, bool):
            raise ContractValidationError("is_inference must be boolean")
        if not isinstance(self.metadata_pseudo_judgment, bool):
            raise ContractValidationError("metadata_pseudo_judgment must be boolean")


@dataclass(frozen=True, slots=True)
class ReportSource:
    source_id: str
    dimension_id: str
    family_id: str
    title: str
    url: str
    first_party: bool
    strong_family: bool
    winning: bool = True
    invalid_page: bool = False
    captcha: bool = False
    aggregator_replacement: bool = False
    secondary_replaces_available_official: bool = False
    time_sensitive: bool = False
    within_time_window: bool = False
    explicit_no_new_evidence: bool = False

    def __post_init__(self) -> None:
        for field in ("source_id", "dimension_id", "family_id", "title", "url"):
            _require_text(getattr(self, field), field)
        for field in (
            "first_party",
            "strong_family",
            "winning",
            "invalid_page",
            "captcha",
            "aggregator_replacement",
            "secondary_replaces_available_official",
            "time_sensitive",
            "within_time_window",
            "explicit_no_new_evidence",
        ):
            if not isinstance(getattr(self, field), bool):
                raise ContractValidationError(f"{field} must be boolean")


@dataclass(frozen=True, slots=True)
class ReportRenderLint:
    section_order_correct: bool
    key_judgment_count: int
    no_metadata_pseudo_judgments: bool
    paragraphs_within_limit: bool
    no_internal_diagnostics: bool
    no_duplicate_headings: bool
    no_dangling_citations: bool
    no_obvious_errors: bool

    def __post_init__(self) -> None:
        if not isinstance(self.key_judgment_count, int) or self.key_judgment_count < 0:
            raise ContractValidationError("key_judgment_count must be a non-negative integer")
        for field in (
            "section_order_correct",
            "no_metadata_pseudo_judgments",
            "paragraphs_within_limit",
            "no_internal_diagnostics",
            "no_duplicate_headings",
            "no_dangling_citations",
            "no_obvious_errors",
        ):
            if not isinstance(getattr(self, field), bool):
                raise ContractValidationError(f"{field} must be boolean")

    @property
    def first_screen_ok(self) -> bool:
        return (
            3 <= self.key_judgment_count <= 7
            and self.no_metadata_pseudo_judgments
            and self.paragraphs_within_limit
        )

    @property
    def clean_copy_ok(self) -> bool:
        return (
            self.no_internal_diagnostics
            and self.no_duplicate_headings
            and self.no_dangling_citations
            and self.no_obvious_errors
        )


@dataclass(frozen=True, slots=True)
class ReportQualityResult:
    audit: ReportQualityAudit
    delivery_status: DeliveryStatus
    reason_codes: tuple[str, ...]
    rubric_hash: str = REPORT_QUALITY_RUBRIC_V1_HASH

    def __post_init__(self) -> None:
        if self.delivery_status not in {
            "completed",
            "partial",
            "insufficient_evidence",
        }:
            raise ContractValidationError("invalid report delivery status")
        _require_strings(self.reason_codes, "reason_codes")
        if self.rubric_hash != REPORT_QUALITY_RUBRIC_V1_HASH:
            raise ContractValidationError("rubric hash mismatch")


def _index_inputs(
    brief: ResearchBrief,
    coverages: Sequence[DimensionCoverage],
    analyses: Sequence[DimensionAnalysis],
    claims: Sequence[ReportClaim],
    sources: Sequence[ReportSource],
) -> tuple[
    dict[str, DimensionCoverage],
    dict[str, DimensionAnalysis],
    dict[str, ReportSource],
]:
    dimension_ids = {item.dimension_id for item in brief.dimensions}
    coverage_by_id = {item.dimension_id: item for item in coverages}
    if len(coverage_by_id) != len(coverages) or set(coverage_by_id) != dimension_ids:
        raise ContractValidationError("coverage must contain every brief dimension exactly once")
    analysis_by_id = {item.dimension_id: item for item in analyses}
    if len(analysis_by_id) != len(analyses) or not set(analysis_by_id) <= dimension_ids:
        raise ContractValidationError("analysis dimensions must be unique and belong to the brief")
    source_by_id = {item.source_id: item for item in sources}
    if len(source_by_id) != len(sources):
        raise ContractValidationError("source ids must be unique")
    if any(item.dimension_id not in dimension_ids for item in sources):
        raise ContractValidationError("source dimension must belong to the brief")
    claim_ids = {item.claim_id for item in claims}
    if len(claim_ids) != len(claims):
        raise ContractValidationError("claim ids must be unique")
    if any(item.dimension_id not in dimension_ids for item in claims):
        raise ContractValidationError("claim dimension must belong to the brief")
    return coverage_by_id, analysis_by_id, source_by_id


def _claim_text(
    claims: Sequence[ReportClaim],
    *,
    dimension_id: str,
    kind: ClaimKind,
    labels: Mapping[str, str],
    fallback: str,
) -> str:
    selected = [item for item in claims if item.dimension_id == dimension_id and item.kind == kind]
    if not selected:
        return fallback
    chunks: list[str] = []
    for item in selected:
        citations = "".join(f" [{labels[source_id]}]" for source_id in item.source_ids if source_id in labels)
        prefix = "推断：" if item.is_inference else ""
        chunks.append(f"{prefix}{item.text}{citations}")
    return "；".join(chunks)


def _normalized_match_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def render_report(
    *,
    brief: ResearchBrief,
    coverages: Sequence[DimensionCoverage],
    analyses: Sequence[DimensionAnalysis],
    claims: Sequence[ReportClaim],
    sources: Sequence[ReportSource],
) -> str:
    """Render a fixed, user-facing Markdown report without audit internals."""

    coverage_by_id, analysis_by_id, source_by_id = _index_inputs(
        brief, coverages, analyses, claims, sources
    )
    unknown_source_ids = {
        source_id
        for item in claims
        for source_id in item.source_ids
        if source_id not in source_by_id
    }
    if unknown_source_ids:
        raise ContractValidationError(
            f"render claims reference unknown sources: {sorted(unknown_source_ids)}"
        )
    key_claims = [item for item in claims if item.kind == "key_judgment"]
    if not 3 <= len(key_claims) <= 7:
        raise ContractValidationError("render requires 3 to 7 key judgments")
    if any(item.metadata_pseudo_judgment for item in key_claims):
        raise ContractValidationError("metadata cannot be rendered as a key judgment")

    used_source_ids = {
        source_id
        for item in claims
        for source_id in item.source_ids
        if source_id in source_by_id and source_by_id[source_id].winning
    }
    ordered_sources = [item for item in sources if item.source_id in used_source_ids]
    labels = {item.source_id: f"S{index}" for index, item in enumerate(ordered_sources, 1)}

    lines = ["# 深度研究报告", "", "## 关键判断", ""]
    for item in key_claims:
        citations = "".join(f" [{labels[source_id]}]" for source_id in item.source_ids if source_id in labels)
        inference = "推断：" if item.is_inference else ""
        lines.append(f"- {inference}{item.text}{citations}")

    lines.extend(["", "## 分维度分析", ""])
    for dimension in brief.dimensions:
        coverage = coverage_by_id[dimension.dimension_id]
        if coverage.status == "not_applicable":
            continue
        analysis = analysis_by_id.get(dimension.dimension_id)
        if analysis is not None and analysis.direct_answer:
            direct_citations = "".join(
                f" [{labels[source_id]}]"
                for source_id in analysis.winning_evidence_ids
                if source_id in labels
            )
            direct_answer = f"{analysis.direct_answer}{direct_citations}"
        else:
            direct_answer = _claim_text(
                claims,
                dimension_id=dimension.dimension_id,
                kind="current_state",
                labels=labels,
                fallback="现有证据不足以形成可靠判断。",
            )
        lines.extend(
            [
                f"### {dimension.question}",
                "",
                f"**现状：** {direct_answer}",
                "",
                "**原因与变化：** "
                + _claim_text(
                    claims,
                    dimension_id=dimension.dimension_id,
                    kind="driver_change",
                    labels=labels,
                    fallback="尚无足够证据确认主要驱动因素。",
                ),
                "",
                "**影响：** "
                + _claim_text(
                    claims,
                    dimension_id=dimension.dimension_id,
                    kind="impact",
                    labels=labels,
                    fallback="影响范围仍需进一步验证。",
                ),
                "",
                "**下一步：** "
                + _claim_text(
                    claims,
                    dimension_id=dimension.dimension_id,
                    kind="next_step",
                    labels=labels,
                    fallback="优先补充该维度的一手证据后再行动。",
                ),
                "",
            ]
        )

    risk_claims = [
        item for item in claims if item.kind in {"uncertainty", "counterevidence", "forecast"}
    ]
    lines.extend(["## 风险与不确定性", ""])
    if risk_claims:
        for item in risk_claims:
            citations = "".join(f" [{labels[source_id]}]" for source_id in item.source_ids if source_id in labels)
            lines.append(f"- {item.text}{citations}")
    else:
        has_evidence_gap = any(
            coverage.status in {"uncovered", "partially_covered"}
            for coverage in coverage_by_id.values()
        )
        lines.append(
            "- 部分维度证据仍不完整，结论可能随新增一手资料而调整。"
            if has_evidence_gap
            else "- 当前未识别到需要单列的预测、冲突证据或重大不确定性。"
        )

    lines.extend(
        [
            "",
            "## 来源与方法",
            "",
            "本报告仅使用已核验且可回指的资料，并将事实、推断与不确定性分开表达。",
            "",
        ]
    )
    if ordered_sources:
        for item in ordered_sources:
            lines.append(f"- [{labels[item.source_id]}] {item.title} — {item.url}")
    else:
        lines.append("- 暂无可列出的核验来源。")
    return "\n".join(lines).rstrip() + "\n"


def lint_rendered_report(
    markdown: str,
    *,
    metadata_pseudo_judgment_count: int = 0,
    max_paragraph_chars: int = 800,
) -> ReportRenderLint:
    """Run the deterministic final-render lint required by the v1 rubric."""

    _require_text(markdown, "markdown")
    positions = [markdown.find(heading) for heading in _REQUIRED_HEADINGS]
    section_order_correct = all(position >= 0 for position in positions) and positions == sorted(positions)
    heading_lines = re.findall(r"(?m)^#{1,6}\s+.+$", markdown)
    no_duplicate_headings = len(heading_lines) == len(set(heading_lines))

    key_count = 0
    if positions[0] >= 0 and positions[1] > positions[0]:
        key_section = markdown[positions[0] + len(_REQUIRED_HEADINGS[0]) : positions[1]]
        key_count = len(re.findall(r"(?m)^-\s+\S", key_section))

    paragraphs = [
        item.strip()
        for item in re.split(r"\n\s*\n", markdown)
        if item.strip() and not item.lstrip().startswith("#")
    ]
    paragraphs_within_limit = all(len(item) <= max_paragraph_chars for item in paragraphs)
    no_internal_diagnostics = not any(
        re.search(pattern, markdown) for pattern in _DIAGNOSTIC_PATTERNS
    )
    citation_ids = set(re.findall(r"\[S(\d+)\]", markdown))
    defined_ids = set(re.findall(r"(?m)^- \[S(\d+)\]\s+", markdown))
    no_dangling_citations = citation_ids <= defined_ids
    no_obvious_errors = re.search(r"[。！？.!?]{2,}", markdown) is None
    return ReportRenderLint(
        section_order_correct=section_order_correct,
        key_judgment_count=key_count,
        no_metadata_pseudo_judgments=metadata_pseudo_judgment_count == 0,
        paragraphs_within_limit=paragraphs_within_limit,
        no_internal_diagnostics=no_internal_diagnostics,
        no_duplicate_headings=no_duplicate_headings,
        no_dangling_citations=no_dangling_citations,
        no_obvious_errors=no_obvious_errors,
    )


def _d(value: float | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _clamp(value: Decimal, maximum: int) -> Decimal:
    return max(Decimal(0), min(_d(maximum), value))


def _round_tenth(value: Decimal, maximum: int) -> Decimal:
    return _clamp(value, maximum).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _ratio(items: Sequence[object], predicate) -> Decimal:  # type: ignore[no-untyped-def]
    if not items:
        return Decimal(0)
    return _d(sum(1 for item in items if predicate(item))) / _d(len(items))


def _component(parts: Sequence[tuple[int, Decimal]], maximum: int) -> Decimal:
    return _round_tenth(sum((_d(weight) * ratio for weight, ratio in parts), Decimal(0)), maximum)


def audit_report_quality(
    *,
    brief: ResearchBrief,
    coverages: Sequence[DimensionCoverage],
    analyses: Sequence[DimensionAnalysis],
    claims: Sequence[ReportClaim],
    sources: Sequence[ReportSource],
    render_lint: ReportRenderLint,
    audit_id: str = "report-quality-v1",
    audited_at: str | None = None,
) -> ReportQualityResult:
    """Evaluate ReportQualityRubricV1 and return its three-state decision."""

    coverage_by_id, analysis_by_id, source_by_id = _index_inputs(
        brief, coverages, analyses, claims, sources
    )
    applicable_core = [
        item
        for item in brief.dimensions
        if item.importance == "core" and coverage_by_id[item.dimension_id].status != "not_applicable"
    ]
    covered_or_partial = [
        item
        for item in applicable_core
        if coverage_by_id[item.dimension_id].status in {"covered", "partially_covered"}
    ]
    covered = [
        item for item in applicable_core if coverage_by_id[item.dimension_id].status == "covered"
    ]
    key_claims = [item for item in claims if item.kind == "key_judgment"]

    direct_answer_ratio = _ratio(
        applicable_core,
        lambda item: (
            item.dimension_id in analysis_by_id
            and bool(analysis_by_id[item.dimension_id].direct_answer)
            and analysis_by_id[item.dimension_id].relevance_score >= 0.65
        ),
    )
    normalized_intent = _normalized_match_text(
        " ".join((brief.user_question, *brief.subjects))
    )
    dimension_intent = {
        item.dimension_id: _normalized_match_text(item.question)
        for item in brief.dimensions
    }

    def key_claim_maps_intent(claim: ReportClaim) -> bool:
        claim_text = _normalized_match_text(claim.text)
        bound_dimension_text = dimension_intent.get(claim.dimension_id, "")
        return any(
            (token := _normalized_match_text(raw_token))
            and token in normalized_intent
            and (token in claim_text or token in bound_dimension_text)
            for raw_token in claim.intent_tokens
        )

    mapped_key_ratio = _ratio(
        applicable_core,
        lambda item: any(
            claim.dimension_id == item.dimension_id
            and key_claim_maps_intent(claim)
            and not claim.metadata_pseudo_judgment
            for claim in key_claims
        ),
    )
    relevance = _component(((15, direct_answer_ratio), (10, mapped_key_ratio)), 25)

    coverage_values = {
        "covered": Decimal(1),
        "partially_covered": Decimal("0.5"),
        "uncovered": Decimal(0),
    }
    weighted_coverage = (
        sum(
            (coverage_values[coverage_by_id[item.dimension_id].status] for item in applicable_core),
            Decimal(0),
        )
        / _d(len(applicable_core))
        if applicable_core
        else Decimal(0)
    )
    conclusion_evidence_ratio = _ratio(
        applicable_core,
        lambda item: (
            item.dimension_id in analysis_by_id
            and bool(analysis_by_id[item.dimension_id].direct_answer)
            and bool(
                set(analysis_by_id[item.dimension_id].winning_evidence_ids)
                & set(coverage_by_id[item.dimension_id].winning_evidence_ids)
            )
        ),
    )
    coverage_score = _component(((20, weighted_coverage), (5, conclusion_evidence_ratio)), 25)

    first_party_ratio = _ratio(
        applicable_core,
        lambda item: (
            not item.first_party_required
            or (
                coverage_by_id[item.dimension_id].first_party_satisfied
                and any(
                    source.dimension_id == item.dimension_id
                    and source.winning
                    and source.first_party
                    and not source.invalid_page
                    and not source.captcha
                    for source in sources
                )
            )
        ),
    )
    two_strong_ratio = _ratio(
        covered,
        lambda item: len(
            {
                source.family_id
                for source in sources
                if source.dimension_id == item.dimension_id
                and source.winning
                and source.strong_family
                and not source.invalid_page
                and not source.captcha
                and not source.aggregator_replacement
            }
        )
        >= 2,
    )

    def winners_clean(dimension_id: str) -> bool:
        winning_ids = coverage_by_id[dimension_id].winning_evidence_ids
        if not winning_ids:
            return False
        return all(
            source_id in source_by_id
            and source_by_id[source_id].winning
            and not source_by_id[source_id].invalid_page
            and not source_by_id[source_id].captcha
            and not source_by_id[source_id].aggregator_replacement
            for source_id in winning_ids
        )

    clean_winner_ratio = _ratio(
        applicable_core, lambda item: winners_clean(item.dimension_id)
    )
    source_quality = _component(
        ((10, first_party_ratio), (5, two_strong_ratio), (5, clean_winner_ratio)), 20
    )

    def supported_field(dimension_id: str, kind: ClaimKind) -> bool:
        analysis = analysis_by_id.get(dimension_id)
        inference_ids = set(analysis.inference_claim_ids) if analysis is not None else set()
        return any(
            claim.dimension_id == dimension_id
            and claim.kind == kind
            and bool(claim.supported_fact_refs)
            and (claim.claim_id not in inference_ids or claim.is_inference)
            for claim in claims
        )

    current_ratio = _ratio(
        covered_or_partial, lambda item: supported_field(item.dimension_id, "current_state")
    )
    driver_ratio = _ratio(
        covered_or_partial, lambda item: supported_field(item.dimension_id, "driver_change")
    )
    impact_ratio = _ratio(
        covered_or_partial, lambda item: supported_field(item.dimension_id, "impact")
    )
    synthesis_reasoning = _component(
        ((5, current_ratio), (5, driver_ratio), (5, impact_ratio)), 15
    )

    time_sensitive = [
        item
        for item in applicable_core
        if any(source.dimension_id == item.dimension_id and source.time_sensitive for source in sources)
    ]

    def time_satisfied(dimension_id: str) -> bool:
        dimension_sources = [
            source
            for source in sources
            if source.dimension_id == dimension_id and source.winning and source.time_sensitive
        ]
        return bool(dimension_sources) and (
            all(source.within_time_window for source in dimension_sources)
            or any(source.explicit_no_new_evidence for source in dimension_sources)
        )

    temporal_ratio = _ratio(time_sensitive, lambda item: time_satisfied(item.dimension_id))
    uncertainty_targets = [
        item
        for item in applicable_core
        if coverage_by_id[item.dimension_id].status == "partially_covered"
        or any(
            claim.dimension_id == item.dimension_id
            and claim.kind in {"forecast", "counterevidence"}
            for claim in claims
        )
    ]
    if uncertainty_targets:
        uncertainty_ratio = _ratio(
            uncertainty_targets,
            lambda item: any(
                claim.dimension_id == item.dimension_id
                and claim.kind in {"uncertainty", "counterevidence"}
                and bool(claim.supported_fact_refs)
                for claim in claims
            ),
        )
    else:
        uncertainty_ratio = Decimal(1)
    timeliness_uncertainty = _component(
        ((5, temporal_ratio), (5, uncertainty_ratio)), 10
    )

    readability = _component(
        (
            (2, Decimal(int(render_lint.section_order_correct))),
            (2, Decimal(int(render_lint.first_screen_ok))),
            (1, Decimal(int(render_lint.clean_copy_ok))),
        ),
        5,
    )

    components = (
        relevance,
        coverage_score,
        source_quality,
        synthesis_reasoning,
        timeliness_uncertainty,
        readability,
    )
    total = _round_tenth(sum(components, Decimal(0)), 100)

    hard_failures: list[str] = []
    if any(coverage_by_id[item.dimension_id].status == "uncovered" for item in applicable_core):
        hard_failures.append("completed_core_uncovered")
    def key_claim_supported(claim: ReportClaim) -> bool:
        return (
            bool(claim.supported_fact_refs)
            and bool(claim.source_ids)
            and all(
                source_id in source_by_id
                and source_by_id[source_id].dimension_id == claim.dimension_id
                for source_id in claim.source_ids
            )
        )

    if any(not key_claim_supported(claim) for claim in key_claims):
        hard_failures.append("unsupported_key_claim")

    cited_sources = [
        source_by_id[source_id]
        for claim in claims
        for source_id in claim.source_ids
        if source_id in source_by_id
    ]
    if any(source.invalid_page or source.captcha for source in cited_sources):
        hard_failures.append("invalid_or_captcha_citation")
    if any(source.secondary_replaces_available_official for source in cited_sources):
        hard_failures.append("secondary_replaces_available_official")
    if any(
        source_id in source_by_id
        and source_by_id[source_id].dimension_id != claim.dimension_id
        for claim in claims
        for source_id in claim.source_ids
    ):
        hard_failures.append("citation_dimension_mismatch")
    if not render_lint.no_internal_diagnostics:
        hard_failures.append("internal_diagnostics_leak")

    universal_failures = set(hard_failures) - {"completed_core_uncovered"}
    all_core_covered = bool(applicable_core) and len(covered) == len(applicable_core)
    supported_key_claims = bool(key_claims) and all(key_claim_supported(claim) for claim in key_claims)
    partial_minimum = max(1, math.ceil(len(applicable_core) * 0.5))

    if total >= Decimal(80) and all_core_covered and not hard_failures:
        delivery_status: DeliveryStatus = "completed"
        reason_codes = ("quality_gate_passed",)
    elif (
        total >= Decimal(60)
        and supported_key_claims
        and len(covered_or_partial) >= partial_minimum
        and not universal_failures
    ):
        delivery_status = "partial"
        reason_codes = ("quality_gate_partial",)
    else:
        delivery_status = "insufficient_evidence"
        reason_codes = ("quality_gate_insufficient",)

    defects: list[str] = list(hard_failures)
    allowed_repairs: list[str] = []
    if synthesis_reasoning < Decimal(15):
        allowed_repairs.append("complete_dimension_analysis")
    if "secondary_replaces_available_official" in hard_failures:
        allowed_repairs.append("replace_secondary_citation")
    if "unsupported_key_claim" in hard_failures:
        allowed_repairs.append("narrow_unsupported_claim")
    if timeliness_uncertainty < Decimal(10):
        allowed_repairs.append("add_uncertainty")
    if readability < Decimal(5):
        allowed_repairs.append("rewrite_readability")

    timestamp = audited_at or datetime.now(UTC).isoformat()
    audit = ReportQualityAudit(
        audit_id=audit_id,
        rubric_version="ReportQualityRubricV1",
        relevance=float(relevance),
        coverage=float(coverage_score),
        source_quality=float(source_quality),
        synthesis_reasoning=float(synthesis_reasoning),
        timeliness_uncertainty=float(timeliness_uncertainty),
        readability=float(readability),
        total_score=float(total),
        hard_failures=tuple(hard_failures),
        defects=tuple(defects),
        allowed_repairs=tuple(dict.fromkeys(allowed_repairs)),
        passed=delivery_status == "completed",
        audited_at=timestamp,
    )
    return ReportQualityResult(
        audit=audit,
        delivery_status=delivery_status,
        reason_codes=reason_codes,
    )


class ReportQualityRubricV1:
    """Named, immutable entrypoint for the hash-locked v1 rubric."""

    version = "ReportQualityRubricV1"
    rubric_hash = REPORT_QUALITY_RUBRIC_V1_HASH
    manifest = REPORT_QUALITY_RUBRIC_V1_MANIFEST
    evaluate = staticmethod(audit_report_quality)


__all__ = [
    "REPORT_QUALITY_RUBRIC_V1_HASH",
    "REPORT_QUALITY_RUBRIC_V1_MANIFEST",
    "ReportClaim",
    "ReportQualityResult",
    "ReportQualityRubricV1",
    "ReportRenderLint",
    "ReportSource",
    "audit_report_quality",
    "lint_rendered_report",
    "render_report",
]
