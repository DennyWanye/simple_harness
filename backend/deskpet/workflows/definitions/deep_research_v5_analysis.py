"""Dimension-balanced passage selection and strict v5 analysis validation.

The policy is profile-neutral.  Profiles own their dimension definitions, while
this module owns the universal passage allocation and per-dimension analysis
contract used by generic, education, and technology research.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Mapping, Sequence

from .deep_research_v3_contracts import AtomicClaim, EvidencePassage
from .deep_research_v3_quality import evaluate_support
from .deep_research_v5_contracts import (
    ContractValidationError,
    DimensionAnalysis,
    DimensionCoverage,
    JsonValue,
    ResearchDimension,
)
from .deep_research_v5_evidence import AdmittedPassage, EvidenceEvaluation


AnalysisStatementKind = Literal["fact", "inference", "gap"]
_STATEMENT_FIELDS = (
    "finding",
    "current_state",
    "driver_or_change",
    "impact",
    "uncertainty",
    "counterevidence",
)
_GAP_MARKER = re.compile(
    r"(?:evidence\s+gap|insufficient\s+evidence|证据不足|证据缺口|暂无足够证据)",
    re.I,
)
_PSEUDO_FINDING = re.compile(
    r"(?:^|[。.!?；;]\s*)(?:发布(?:于|日期)|发布日期|文件(?:于.+)?印发|"
    r"published\s+on|.+\s+was\s+published\s+on|publication\s+date|"
    r"anniversary|周年|术语定义|文件标题|政策口号|全面推进|深入贯彻|"
    r"大力实施|持续推动|strengthen\b|promote\b|deepen\b)",
    re.I,
)
_TIER_ORDER = {"first_party": 0, "secondary": 1, "aggregator": 2}


def _require_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{path} must be a non-empty string")
    return " ".join(value.split())


def _require_string_list(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ContractValidationError(f"{path} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ContractValidationError(f"{path} must not contain duplicates")
    return tuple(value)


def _strict_object(
    value: Mapping[str, Any], *, keys: set[str], name: str
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{name} must be an object")
    raw = dict(value)
    missing = sorted(keys - set(raw))
    unknown = sorted(set(raw) - keys)
    if missing or unknown:
        raise ContractValidationError(
            f"{name} keys differ (missing={missing}, unknown={unknown})"
        )
    if raw.get("schema_version") != 1:
        raise ContractValidationError(f"{name}.schema_version must be 1")
    return raw


@dataclass(frozen=True, slots=True)
class AnalysisStatement:
    """One atomic fact, inference, or explicit evidence gap."""

    claim_id: str
    text: str
    kind: AnalysisStatementKind
    citation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.claim_id, "claim_id")
        _require_text(self.text, "text")
        if self.kind not in {"fact", "inference", "gap"}:
            raise ContractValidationError("kind must be fact, inference, or gap")
        if len(self.citation_ids) != len(set(self.citation_ids)) or any(
            not isinstance(item, str) or not item.strip() for item in self.citation_ids
        ):
            raise ContractValidationError("citation_ids must be unique non-empty strings")
        if self.kind == "fact" and not self.citation_ids:
            raise ContractValidationError("fact statements require citations")
        if self.kind == "gap":
            if self.citation_ids:
                raise ContractValidationError("gap statements cannot cite evidence")
            if not _GAP_MARKER.search(self.text):
                raise ContractValidationError("gap statements must explicitly name the evidence gap")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "schema_version": 1,
            "claim_id": self.claim_id,
            "text": self.text,
            "kind": self.kind,
            "citation_ids": list(self.citation_ids),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "AnalysisStatement":
        raw = _strict_object(
            value,
            keys={"schema_version", "claim_id", "text", "kind", "citation_ids"},
            name=cls.__name__,
        )
        kind = raw["kind"]
        if kind not in {"fact", "inference", "gap"}:
            raise ContractValidationError("kind must be fact, inference, or gap")
        return cls(
            claim_id=_require_text(raw["claim_id"], "claim_id"),
            text=_require_text(raw["text"], "text"),
            kind=kind,
            citation_ids=_require_string_list(raw["citation_ids"], "citation_ids"),
        )


@dataclass(frozen=True, slots=True)
class PerDimensionAnalysisPayload:
    """Strict model output for exactly one research dimension."""

    dimension_id: str
    finding: AnalysisStatement
    current_state: AnalysisStatement | None
    driver_or_change: AnalysisStatement | None
    impact: AnalysisStatement | None
    uncertainty: AnalysisStatement | None
    counterevidence: AnalysisStatement | None
    citation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.dimension_id, "dimension_id")
        statements = self.statements
        claim_ids = [item.claim_id for item in statements]
        if len(claim_ids) != len(set(claim_ids)):
            raise ContractValidationError("analysis claim ids must be unique")
        if len(self.citation_ids) != len(set(self.citation_ids)) or any(
            not isinstance(item, str) or not item.strip() for item in self.citation_ids
        ):
            raise ContractValidationError("citation_ids must be unique non-empty strings")
        cited = {citation for item in statements for citation in item.citation_ids}
        if cited != set(self.citation_ids):
            raise ContractValidationError(
                "payload citation_ids must exactly match statement citations"
            )
        if _PSEUDO_FINDING.search(self.finding.text):
            raise ContractValidationError("finding cannot be publication metadata or a slogan")

    @property
    def statements(self) -> tuple[AnalysisStatement, ...]:
        return tuple(
            value
            for value in (
                self.finding,
                self.current_state,
                self.driver_or_change,
                self.impact,
                self.uncertainty,
                self.counterevidence,
            )
            if value is not None
        )

    def to_json(self) -> dict[str, JsonValue]:
        result: dict[str, JsonValue] = {
            "schema_version": 1,
            "dimension_id": self.dimension_id,
            "citation_ids": list(self.citation_ids),
        }
        for field_name in _STATEMENT_FIELDS:
            statement = getattr(self, field_name)
            result[field_name] = statement.to_json() if statement is not None else None
        return result

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "PerDimensionAnalysisPayload":
        keys = {"schema_version", "dimension_id", "citation_ids", *_STATEMENT_FIELDS}
        raw = _strict_object(value, keys=keys, name=cls.__name__)
        parsed: dict[str, AnalysisStatement | None] = {}
        for field_name in _STATEMENT_FIELDS:
            item = raw[field_name]
            if item is not None and not isinstance(item, Mapping):
                raise ContractValidationError(f"{field_name} must be an object or null")
            parsed[field_name] = (
                AnalysisStatement.from_json(item) if item is not None else None
            )
        if parsed["finding"] is None:
            raise ContractValidationError("finding is required")
        return cls(
            dimension_id=_require_text(raw["dimension_id"], "dimension_id"),
            finding=parsed["finding"],  # type: ignore[arg-type]
            current_state=parsed["current_state"],
            driver_or_change=parsed["driver_or_change"],
            impact=parsed["impact"],
            uncertainty=parsed["uncertainty"],
            counterevidence=parsed["counterevidence"],
            citation_ids=_require_string_list(raw["citation_ids"], "citation_ids"),
        )


def _published_ordinal(value: str | None) -> int:
    if not value:
        return 0
    try:
        return date.fromisoformat(value[:10]).toordinal()
    except ValueError:
        return 0


def _passage_priority(passage: AdmittedPassage) -> tuple[int, int, float, int, str]:
    candidate = passage.candidate
    depth = len(candidate.span_text or candidate.body_text)
    return (
        _TIER_ORDER[candidate.source_tier],
        -_published_ordinal(candidate.published_date),
        -float(candidate.relevance),
        -depth,
        candidate.candidate_id,
    )


def _round_robin(
    queues: Sequence[list[AdmittedPassage]],
    selected: list[AdmittedPassage],
    selected_ids: set[str],
    maximum: int,
) -> None:
    cursor = 0
    while len(selected) < maximum and any(queues):
        queue = queues[cursor % len(queues)]
        cursor += 1
        if not queue:
            continue
        passage = queue.pop(0)
        candidate_id = passage.candidate.candidate_id
        if candidate_id in selected_ids:
            continue
        selected.append(passage)
        selected_ids.add(candidate_id)


def rerank_analysis_passages(
    dimensions: Sequence[ResearchDimension],
    evaluation: EvidenceEvaluation,
    *,
    maximum: int,
) -> tuple[AdmittedPassage, ...]:
    """Allocate family winners core-first, then fill by source quality.

    Winner queues are round-robin by dimension.  Therefore a productive core
    dimension cannot consume the first slot owed to another core dimension.
    """

    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
        raise ContractValidationError("maximum must be an integer >= 1")
    dimension_ids = [item.dimension_id for item in dimensions]
    if len(dimension_ids) != len(set(dimension_ids)):
        raise ContractValidationError("dimension ids must be unique")

    admitted_by_id = {
        item.candidate.candidate_id: item for item in evaluation.admitted_passages
    }
    if len(admitted_by_id) != len(evaluation.admitted_passages):
        raise ContractValidationError("admitted passage ids must be unique")
    coverage_by_dimension = {
        item.dimension_id: item for item in evaluation.dimension_coverages
    }
    if set(coverage_by_dimension) != set(dimension_ids):
        raise ContractValidationError("coverage dimensions must exactly match dimensions")

    winner_queues: dict[str, list[AdmittedPassage]] = {}
    all_winner_ids: set[str] = set()
    for dimension in dimensions:
        coverage = coverage_by_dimension[dimension.dimension_id]
        for candidate_id in coverage.winning_evidence_ids:
            passage = admitted_by_id.get(candidate_id)
            if passage is None or passage.candidate.dimension_id != dimension.dimension_id:
                raise ContractValidationError("winning passage is missing or crosses dimensions")
        by_family: dict[str, list[AdmittedPassage]] = {}
        for passage in evaluation.admitted_passages:
            if passage.candidate.dimension_id == dimension.dimension_id:
                by_family.setdefault(passage.family_id, []).append(passage)
        if set(by_family) != set(coverage.source_family_ids):
            raise ContractValidationError(
                "coverage source families must match admitted dimension passages"
            )
        queue = [
            min(by_family[family_id], key=_passage_priority)
            for family_id in coverage.source_family_ids
        ]
        all_winner_ids.update(item.candidate.candidate_id for item in queue)
        winner_queues[dimension.dimension_id] = sorted(queue, key=_passage_priority)

    selected: list[AdmittedPassage] = []
    selected_ids: set[str] = set()
    core_queues = [
        winner_queues[item.dimension_id]
        for item in dimensions
        if item.importance == "core" and winner_queues[item.dimension_id]
    ]
    supporting_queues = [
        winner_queues[item.dimension_id]
        for item in dimensions
        if item.importance == "supporting" and winner_queues[item.dimension_id]
    ]
    if maximum < len(core_queues):
        raise ContractValidationError(
            "maximum must reserve at least one passage for every core dimension with evidence"
        )
    if core_queues:
        _round_robin(core_queues, selected, selected_ids, maximum)
    if len(selected) < maximum and supporting_queues:
        _round_robin(supporting_queues, selected, selected_ids, maximum)

    importance = {item.dimension_id: item.importance for item in dimensions}
    extras = sorted(
        (
            item
            for item in evaluation.admitted_passages
            if item.candidate.candidate_id not in all_winner_ids
        ),
        key=lambda item: (
            0 if importance[item.candidate.dimension_id] == "core" else 1,
            *_passage_priority(item),
        ),
    )
    for passage in extras:
        if len(selected) >= maximum:
            break
        candidate_id = passage.candidate.candidate_id
        if candidate_id not in selected_ids:
            selected.append(passage)
            selected_ids.add(candidate_id)
    return tuple(selected)


# Public semantic alias used by node code and tests.
select_analysis_passages = rerank_analysis_passages


def _coverage_for(
    dimension_id: str, evaluation: EvidenceEvaluation
) -> DimensionCoverage:
    matches = [
        item for item in evaluation.dimension_coverages if item.dimension_id == dimension_id
    ]
    if len(matches) != 1:
        raise ContractValidationError("analysis dimension must have exactly one coverage row")
    return matches[0]


def _support_fact_statements(
    statements: Sequence[AnalysisStatement],
    passages: Sequence[AdmittedPassage],
) -> tuple[str, ...]:
    by_id = {item.candidate.candidate_id: item for item in passages}
    source_ids = {candidate_id: index for index, candidate_id in enumerate(sorted(by_id), 1)}
    stable_passages = []
    for candidate_id in sorted(by_id):
        candidate = by_id[candidate_id].candidate
        text = candidate.span_text or candidate.body_text
        stable_passages.append(
            EvidencePassage(
                passage_id=candidate.candidate_id,
                source_citation_id=source_ids[candidate_id],
                source_content_hash=candidate.effective_content_hash,
                canonical_url=candidate.effective_canonical_url,
                question_id=candidate.dimension_id,
                text=text,
                start=0,
                end=len(text),
                relevance=float(candidate.relevance),
            )
        )
    claims = [
        AtomicClaim(
            claim_id=item.claim_id,
            text=item.text,
            kind="factual",
            citation_ids=tuple(source_ids[citation] for citation in item.citation_ids),
        )
        for item in statements
        if item.kind == "fact"
    ]
    decisions = evaluate_support(claims, stable_passages)
    unsupported = [item.claim_id for item in decisions if not item.supported]
    if unsupported:
        raise ContractValidationError(
            f"unsupported fact statements: {sorted(unsupported)}"
        )
    return tuple(
        passage_id
        for decision in decisions
        for passage_id in decision.winning_passage_ids
        if passage_id in by_id
    )


def validate_dimension_analysis_payload(
    payload: PerDimensionAnalysisPayload | Mapping[str, Any],
    *,
    dimension: ResearchDimension,
    evaluation: EvidenceEvaluation,
    selected_passages: Sequence[AdmittedPassage],
) -> DimensionAnalysis:
    """Validate one model payload and convert it to the persisted v5 contract."""

    if not isinstance(payload, PerDimensionAnalysisPayload):
        payload = PerDimensionAnalysisPayload.from_json(payload)
    if payload.dimension_id != dimension.dimension_id:
        raise ContractValidationError("analysis payload crosses dimensions")

    coverage = _coverage_for(dimension.dimension_id, evaluation)
    selected_by_id = {
        item.candidate.candidate_id: item
        for item in selected_passages
        if item.candidate.dimension_id == dimension.dimension_id
    }
    if len(selected_by_id) != sum(
        item.candidate.dimension_id == dimension.dimension_id
        for item in selected_passages
    ):
        raise ContractValidationError("selected passage ids must be unique")
    allowed_ids = set(coverage.evidence_passage_ids) & set(selected_by_id)
    if any(citation not in allowed_ids for citation in payload.citation_ids):
        raise ContractValidationError(
            "analysis citations must belong to selected admitted passages in the dimension"
        )

    if coverage.status in {"uncovered", "not_applicable"}:
        if (
            payload.finding.kind != "gap"
            or payload.citation_ids
            or any(
                getattr(payload, field_name) is not None
                for field_name in _STATEMENT_FIELDS[1:]
            )
        ):
            raise ContractValidationError(
                "uncovered dimensions require one explicit gap and no conclusion"
            )
        return DimensionAnalysis(
            dimension_id=dimension.dimension_id,
            direct_answer=None,
            fact_claim_ids=(),
            inference_claim_ids=(),
            limitation_claim_ids=(payload.finding.claim_id,),
            winning_evidence_ids=(),
            relevance_score=0.0,
            confidence="insufficient",
        )

    if payload.finding.kind != "fact":
        raise ContractValidationError(
            "covered dimensions require a supported factual finding"
        )
    if not any(item.kind == "fact" for item in payload.statements):
        raise ContractValidationError("covered analysis requires at least one supported fact")

    winning_ids = _support_fact_statements(
        payload.statements, tuple(selected_by_id.values())
    )
    fact_ids = tuple(item.claim_id for item in payload.statements if item.kind == "fact")
    inference_ids = tuple(
        item.claim_id for item in payload.statements if item.kind == "inference"
    )
    limitation_ids = tuple(
        item.claim_id
        for field_name in ("uncertainty", "counterevidence")
        if (item := getattr(payload, field_name)) is not None
    )
    relevance = max(
        (float(selected_by_id[item].candidate.relevance) for item in winning_ids),
        default=0.0,
    )
    if coverage.status == "covered":
        confidence: Literal["high", "medium", "low", "insufficient"] = (
            "high" if relevance >= 0.8 else "medium"
        )
    else:
        confidence = "low"
    return DimensionAnalysis(
        dimension_id=dimension.dimension_id,
        direct_answer=payload.finding.text,
        fact_claim_ids=fact_ids,
        inference_claim_ids=inference_ids,
        limitation_claim_ids=limitation_ids,
        winning_evidence_ids=tuple(dict.fromkeys(winning_ids)),
        relevance_score=relevance,
        confidence=confidence,
    )


validate_analysis_payload = validate_dimension_analysis_payload


def make_gap_analysis_payload(
    dimension_id: str,
    *,
    reason: str = "Evidence gap: no admitted passage supports a reliable conclusion.",
) -> PerDimensionAnalysisPayload:
    """Build the only valid analysis shape for an uncovered dimension."""

    dimension_id = _require_text(dimension_id, "dimension_id")
    return PerDimensionAnalysisPayload(
        dimension_id=dimension_id,
        finding=AnalysisStatement(
            claim_id=f"gap:{dimension_id}",
            text=reason,
            kind="gap",
        ),
        current_state=None,
        driver_or_change=None,
        impact=None,
        uncertainty=None,
        counterevidence=None,
        citation_ids=(),
    )


__all__ = [
    "AnalysisStatement",
    "AnalysisStatementKind",
    "PerDimensionAnalysisPayload",
    "make_gap_analysis_payload",
    "rerank_analysis_passages",
    "select_analysis_passages",
    "validate_analysis_payload",
    "validate_dimension_analysis_payload",
]
