"""Strict, versioned JSON contracts for the DeepResearch v5 quality loop.

The contracts deliberately persist wall-clock anchors and accumulated active
duration only.  A process-local monotonic epoch is never serialized, because
monotonic values from two backend processes are not comparable.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, ClassVar, Literal

from ..contracts import JsonValue, validate_json_value

ResearchProfile = Literal["generic_research", "policy_education", "technology_intelligence"]
DimensionImportance = Literal["core", "supporting"]
CoverageStatus = Literal["covered", "partially_covered", "uncovered", "not_applicable"]
DeliveryStatus = Literal["completed", "partial", "insufficient_evidence"]
EngineTerminalStatus = Literal["completed", "error", "cancelled"]
UsageSource = Literal["provider", "usage_unknown"]


class ContractValidationError(ValueError):
    """A persisted v5 contract failed closed."""


def _object(value: Mapping[str, Any], keys: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractValidationError(f"{name} must be an object")
    raw = dict(value)
    unknown = sorted(set(raw) - keys)
    missing = sorted(keys - set(raw))
    if unknown or missing:
        raise ContractValidationError(
            f"{name} keys differ (missing={missing}, unknown={unknown})"
        )
    if raw.get("schema_version") != 1:
        raise ContractValidationError(f"{name}.schema_version must be 1")
    validate_json_value(raw)
    return raw


def _text(value: Any, path: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{path} must be a non-empty string")
    return value


def _strings(value: Any, path: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ContractValidationError(f"{path} must be an array of non-empty strings")
    if len(set(value)) != len(value):
        raise ContractValidationError(f"{path} must not contain duplicates")
    return tuple(value)


def _integer(value: Any, path: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractValidationError(f"{path} must be an integer >= {minimum}")
    return value


def _number(value: Any, path: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < minimum:
        raise ContractValidationError(f"{path} must be a number >= {minimum}")
    result = float(value)
    validate_json_value(result, path=path)
    return result


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ContractValidationError(f"{path} must be a boolean")
    return value


def _timestamp(value: Any, path: str, *, optional: bool = False) -> str | None:
    result = _text(value, path, optional=optional)
    if result is None:
        return None
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError(f"{path} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ContractValidationError(f"{path} must include a timezone")
    return result


def _json_object(value: Any, path: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractValidationError(f"{path} must be an object")
    validate_json_value(value, path=path)
    return copy.deepcopy(value)


def _enum(value: Any, allowed: set[str], path: str) -> str:
    if value not in allowed:
        raise ContractValidationError(f"{path} must be one of {sorted(allowed)}")
    return str(value)


def _finalize(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    validate_json_value(value)
    return value


@dataclass(frozen=True, slots=True)
class ResearchDimension:
    dimension_id: str
    question: str
    importance: DimensionImportance
    expected_source_types: tuple[str, ...]
    query_targets: tuple[str, ...]
    first_party_required: bool = False
    not_applicable_when: tuple[str, ...] = ()
    SCHEMA_VERSION: ClassVar[int] = 1

    def validate(self) -> None:
        _text(self.dimension_id, "dimension_id"); _text(self.question, "question")
        _enum(self.importance, {"core", "supporting"}, "importance")
        _strings(list(self.expected_source_types), "expected_source_types")
        _strings(list(self.query_targets), "query_targets")
        _boolean(self.first_party_required, "first_party_required")
        _strings(list(self.not_applicable_when), "not_applicable_when")

    def __post_init__(self) -> None: self.validate()

    def to_json(self) -> dict[str, JsonValue]:
        return _finalize({"schema_version": 1, "dimension_id": self.dimension_id,
            "question": self.question, "importance": self.importance,
            "expected_source_types": list(self.expected_source_types),
            "query_targets": list(self.query_targets),
            "first_party_required": self.first_party_required,
            "not_applicable_when": list(self.not_applicable_when)})

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ResearchDimension:
        raw = _object(value, {"schema_version", "dimension_id", "question", "importance",
            "expected_source_types", "query_targets", "first_party_required", "not_applicable_when"}, cls.__name__)
        return cls(str(_text(raw["dimension_id"], "dimension_id")), str(_text(raw["question"], "question")),
            _enum(raw["importance"], {"core", "supporting"}, "importance"),  # type: ignore[arg-type]
            _strings(raw["expected_source_types"], "expected_source_types"),
            _strings(raw["query_targets"], "query_targets"),
            _boolean(raw["first_party_required"], "first_party_required"),
            _strings(raw["not_applicable_when"], "not_applicable_when"))


@dataclass(frozen=True, slots=True)
class ResearchBrief:
    brief_id: str
    user_question: str
    profile: ResearchProfile
    as_of_date: str
    locale: str
    geography: str | None
    subjects: tuple[str, ...]
    expected_decision: str
    dimensions: tuple[ResearchDimension, ...]
    not_applicable_conditions: tuple[str, ...] = ()
    SCHEMA_VERSION: ClassVar[int] = 1

    def validate(self) -> None:
        _text(self.brief_id, "brief_id"); _text(self.user_question, "user_question")
        _enum(self.profile, {"generic_research", "policy_education", "technology_intelligence"}, "profile")
        _text(self.as_of_date, "as_of_date"); _text(self.locale, "locale")
        _text(self.geography, "geography", optional=True); _strings(list(self.subjects), "subjects")
        _text(self.expected_decision, "expected_decision")
        if not self.dimensions or len({item.dimension_id for item in self.dimensions}) != len(self.dimensions):
            raise ContractValidationError("dimensions must be non-empty with unique ids")
        if not any(item.importance == "core" for item in self.dimensions):
            raise ContractValidationError("at least one core dimension is required")
        _strings(list(self.not_applicable_conditions), "not_applicable_conditions")

    def __post_init__(self) -> None: self.validate()

    def to_json(self) -> dict[str, JsonValue]:
        return _finalize({"schema_version": 1, "brief_id": self.brief_id,
            "user_question": self.user_question, "profile": self.profile,
            "as_of_date": self.as_of_date, "locale": self.locale, "geography": self.geography,
            "subjects": list(self.subjects), "expected_decision": self.expected_decision,
            "dimensions": [item.to_json() for item in self.dimensions],
            "not_applicable_conditions": list(self.not_applicable_conditions)})

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ResearchBrief:
        raw = _object(value, {"schema_version", "brief_id", "user_question", "profile", "as_of_date",
            "locale", "geography", "subjects", "expected_decision", "dimensions", "not_applicable_conditions"}, cls.__name__)
        if not isinstance(raw["dimensions"], list): raise ContractValidationError("dimensions must be an array")
        return cls(str(_text(raw["brief_id"], "brief_id")), str(_text(raw["user_question"], "user_question")),
            _enum(raw["profile"], {"generic_research", "policy_education", "technology_intelligence"}, "profile"),  # type: ignore[arg-type]
            str(_text(raw["as_of_date"], "as_of_date")), str(_text(raw["locale"], "locale")),
            _text(raw["geography"], "geography", optional=True), _strings(raw["subjects"], "subjects"),
            str(_text(raw["expected_decision"], "expected_decision")),
            tuple(ResearchDimension.from_json(item) for item in raw["dimensions"]),
            _strings(raw["not_applicable_conditions"], "not_applicable_conditions"))


@dataclass(frozen=True, slots=True)
class DimensionCoverage:
    dimension_id: str
    status: CoverageStatus
    evidence_passage_ids: tuple[str, ...]
    winning_evidence_ids: tuple[str, ...]
    source_family_ids: tuple[str, ...]
    first_party_satisfied: bool
    relevance_score: float
    gap_reasons: tuple[str, ...] = ()

    def validate(self) -> None:
        _text(self.dimension_id, "dimension_id")
        _enum(self.status, {"covered", "partially_covered", "uncovered", "not_applicable"}, "status")
        for name, values in (("evidence_passage_ids", self.evidence_passage_ids), ("winning_evidence_ids", self.winning_evidence_ids), ("source_family_ids", self.source_family_ids), ("gap_reasons", self.gap_reasons)):
            _strings(list(values), name)
        _boolean(self.first_party_satisfied, "first_party_satisfied")
        score = _number(self.relevance_score, "relevance_score")
        if score > 1: raise ContractValidationError("relevance_score must be <= 1")
        if self.status == "not_applicable" and (self.evidence_passage_ids or self.winning_evidence_ids):
            raise ContractValidationError("not_applicable coverage cannot own evidence")
        if self.status == "covered" and not self.winning_evidence_ids:
            raise ContractValidationError("covered dimension requires winning evidence")

    def __post_init__(self) -> None: self.validate()
    def to_json(self) -> dict[str, JsonValue]:
        return _finalize({"schema_version": 1, "dimension_id": self.dimension_id, "status": self.status,
            "evidence_passage_ids": list(self.evidence_passage_ids), "winning_evidence_ids": list(self.winning_evidence_ids),
            "source_family_ids": list(self.source_family_ids), "first_party_satisfied": self.first_party_satisfied,
            "relevance_score": self.relevance_score, "gap_reasons": list(self.gap_reasons)})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> DimensionCoverage:
        raw = _object(value, {"schema_version", "dimension_id", "status", "evidence_passage_ids", "winning_evidence_ids", "source_family_ids", "first_party_satisfied", "relevance_score", "gap_reasons"}, cls.__name__)
        return cls(str(_text(raw["dimension_id"], "dimension_id")), _enum(raw["status"], {"covered", "partially_covered", "uncovered", "not_applicable"}, "status"), _strings(raw["evidence_passage_ids"], "evidence_passage_ids"), _strings(raw["winning_evidence_ids"], "winning_evidence_ids"), _strings(raw["source_family_ids"], "source_family_ids"), _boolean(raw["first_party_satisfied"], "first_party_satisfied"), _number(raw["relevance_score"], "relevance_score"), _strings(raw["gap_reasons"], "gap_reasons"))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class EvidenceSourceFamily:
    family_id: str
    canonical_url: str
    canonical_source_id: str
    source_type: str
    source_tier: Literal["first_party", "secondary", "aggregator"]
    member_urls: tuple[str, ...]
    original_document_url: str | None
    page_quality: Literal["valid", "invalid", "unknown"]
    content_hashes: tuple[str, ...]

    def validate(self) -> None:
        for name, value in (("family_id", self.family_id), ("canonical_url", self.canonical_url), ("canonical_source_id", self.canonical_source_id), ("source_type", self.source_type)): _text(value, name)
        _enum(self.source_tier, {"first_party", "secondary", "aggregator"}, "source_tier")
        _strings(list(self.member_urls), "member_urls"); _strings(list(self.content_hashes), "content_hashes")
        _text(self.original_document_url, "original_document_url", optional=True)
        _enum(self.page_quality, {"valid", "invalid", "unknown"}, "page_quality")
        if self.canonical_url not in self.member_urls: raise ContractValidationError("canonical_url must belong to member_urls")
    def __post_init__(self) -> None: self.validate()
    def to_json(self) -> dict[str, JsonValue]:
        return _finalize({"schema_version": 1, "family_id": self.family_id, "canonical_url": self.canonical_url, "canonical_source_id": self.canonical_source_id, "source_type": self.source_type, "source_tier": self.source_tier, "member_urls": list(self.member_urls), "original_document_url": self.original_document_url, "page_quality": self.page_quality, "content_hashes": list(self.content_hashes)})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> EvidenceSourceFamily:
        raw = _object(value, {"schema_version", "family_id", "canonical_url", "canonical_source_id", "source_type", "source_tier", "member_urls", "original_document_url", "page_quality", "content_hashes"}, cls.__name__)
        return cls(str(_text(raw["family_id"], "family_id")), str(_text(raw["canonical_url"], "canonical_url")), str(_text(raw["canonical_source_id"], "canonical_source_id")), str(_text(raw["source_type"], "source_type")), _enum(raw["source_tier"], {"first_party", "secondary", "aggregator"}, "source_tier"), _strings(raw["member_urls"], "member_urls"), _text(raw["original_document_url"], "original_document_url", optional=True), _enum(raw["page_quality"], {"valid", "invalid", "unknown"}, "page_quality"), _strings(raw["content_hashes"], "content_hashes"))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class GapWorkItem:
    item_id: str
    operation_id: str
    dimension_id: str
    work_kind: Literal["query", "source_target", "fetch"]
    query: str | None
    source_target: str | None
    attempt: int
    reason: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"] = "pending"
    def validate(self) -> None:
        for name, value in (("item_id", self.item_id), ("operation_id", self.operation_id), ("dimension_id", self.dimension_id), ("reason", self.reason)): _text(value, name)
        _enum(self.work_kind, {"query", "source_target", "fetch"}, "work_kind")
        _text(self.query, "query", optional=True); _text(self.source_target, "source_target", optional=True)
        if self.query is None and self.source_target is None: raise ContractValidationError("gap work requires query or source_target")
        _integer(self.attempt, "attempt", minimum=1); _enum(self.status, {"pending", "running", "completed", "failed", "cancelled"}, "status")
    def __post_init__(self) -> None: self.validate()
    def to_json(self) -> dict[str, JsonValue]:
        return _finalize({"schema_version": 1, "item_id": self.item_id, "operation_id": self.operation_id, "dimension_id": self.dimension_id, "work_kind": self.work_kind, "query": self.query, "source_target": self.source_target, "attempt": self.attempt, "reason": self.reason, "status": self.status})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> GapWorkItem:
        raw = _object(value, {"schema_version", "item_id", "operation_id", "dimension_id", "work_kind", "query", "source_target", "attempt", "reason", "status"}, cls.__name__)
        return cls(str(_text(raw["item_id"], "item_id")), str(_text(raw["operation_id"], "operation_id")), str(_text(raw["dimension_id"], "dimension_id")), _enum(raw["work_kind"], {"query", "source_target", "fetch"}, "work_kind"), _text(raw["query"], "query", optional=True), _text(raw["source_target"], "source_target", optional=True), _integer(raw["attempt"], "attempt", minimum=1), str(_text(raw["reason"], "reason")), _enum(raw["status"], {"pending", "running", "completed", "failed", "cancelled"}, "status"))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ResearchBudgetLedger:
    ledger_id: str
    profile: ResearchProfile
    wall_clock_anchor: str
    accumulated_active_seconds: float
    soft_checkpoint_seconds: float
    lease_seconds: float
    automatic_limit_seconds: float
    max_input_tokens: int
    max_output_tokens: int
    max_cost_micros: int
    committed_input_tokens: int = 0
    committed_output_tokens: int = 0
    committed_cost_micros: int = 0
    usage_unknown_count: int = 0

    def validate(self) -> None:
        _text(self.ledger_id, "ledger_id"); _enum(self.profile, {"generic_research", "policy_education", "technology_intelligence"}, "profile")
        _timestamp(self.wall_clock_anchor, "wall_clock_anchor")
        for name in ("accumulated_active_seconds", "soft_checkpoint_seconds", "lease_seconds", "automatic_limit_seconds"): _number(getattr(self, name), name)
        if not 0 < self.soft_checkpoint_seconds <= self.automatic_limit_seconds or not 0 < self.lease_seconds <= self.automatic_limit_seconds: raise ContractValidationError("invalid time budget ordering")
        for name in ("max_input_tokens", "max_output_tokens", "max_cost_micros", "committed_input_tokens", "committed_output_tokens", "committed_cost_micros", "usage_unknown_count"): _integer(getattr(self, name), name)
        if self.committed_input_tokens > self.max_input_tokens or self.committed_output_tokens > self.max_output_tokens or self.committed_cost_micros > self.max_cost_micros: raise ContractValidationError("committed usage exceeds budget")
    def __post_init__(self) -> None: self.validate()
    def record_active_interval(self, *, started_monotonic: float, ended_monotonic: float, wall_clock_anchor: str) -> ResearchBudgetLedger:
        start = _number(started_monotonic, "started_monotonic"); end = _number(ended_monotonic, "ended_monotonic")
        _timestamp(wall_clock_anchor, "wall_clock_anchor")
        if end < start: raise ContractValidationError("monotonic interval cannot run backwards")
        return replace(self, wall_clock_anchor=wall_clock_anchor, accumulated_active_seconds=self.accumulated_active_seconds + end - start)
    def to_json(self) -> dict[str, JsonValue]:
        return _finalize({"schema_version": 1, **{name: getattr(self, name) for name in ("ledger_id", "profile", "wall_clock_anchor", "accumulated_active_seconds", "soft_checkpoint_seconds", "lease_seconds", "automatic_limit_seconds", "max_input_tokens", "max_output_tokens", "max_cost_micros", "committed_input_tokens", "committed_output_tokens", "committed_cost_micros", "usage_unknown_count")}})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ResearchBudgetLedger:
        names = {"ledger_id", "profile", "wall_clock_anchor", "accumulated_active_seconds", "soft_checkpoint_seconds", "lease_seconds", "automatic_limit_seconds", "max_input_tokens", "max_output_tokens", "max_cost_micros", "committed_input_tokens", "committed_output_tokens", "committed_cost_micros", "usage_unknown_count"}
        raw = _object(value, {"schema_version", *names}, cls.__name__)
        return cls(str(_text(raw["ledger_id"], "ledger_id")), _enum(raw["profile"], {"generic_research", "policy_education", "technology_intelligence"}, "profile"), str(_timestamp(raw["wall_clock_anchor"], "wall_clock_anchor")), _number(raw["accumulated_active_seconds"], "accumulated_active_seconds"), _number(raw["soft_checkpoint_seconds"], "soft_checkpoint_seconds"), _number(raw["lease_seconds"], "lease_seconds"), _number(raw["automatic_limit_seconds"], "automatic_limit_seconds"), *(_integer(raw[name], name) for name in ("max_input_tokens", "max_output_tokens", "max_cost_micros", "committed_input_tokens", "committed_output_tokens", "committed_cost_micros", "usage_unknown_count")))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ResearchLLMResult:
    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    cache_tokens: int | None
    usage_source: UsageSource
    request_id: str | None
    def validate(self) -> None:
        _text(self.content, "content"); _text(self.model, "model"); _enum(self.usage_source, {"provider", "usage_unknown"}, "usage_source"); _text(self.request_id, "request_id", optional=True)
        for name in ("input_tokens", "output_tokens", "cache_tokens"):
            value = getattr(self, name)
            if value is not None: _integer(value, name)
        if self.usage_source == "provider" and (self.input_tokens is None or self.output_tokens is None): raise ContractValidationError("provider usage requires input/output tokens")
        if self.usage_source == "usage_unknown" and any(getattr(self, name) is not None for name in ("input_tokens", "output_tokens", "cache_tokens")): raise ContractValidationError("unknown usage cannot masquerade as actual token counts")
    def __post_init__(self) -> None: self.validate()
    def to_json(self) -> dict[str, JsonValue]: return _finalize({"schema_version": 1, "content": self.content, "model": self.model, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "cache_tokens": self.cache_tokens, "usage_source": self.usage_source, "request_id": self.request_id})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ResearchLLMResult:
        raw = _object(value, {"schema_version", "content", "model", "input_tokens", "output_tokens", "cache_tokens", "usage_source", "request_id"}, cls.__name__)
        def token(name: str) -> int | None: return None if raw[name] is None else _integer(raw[name], name)
        return cls(str(_text(raw["content"], "content")), str(_text(raw["model"], "model")), token("input_tokens"), token("output_tokens"), token("cache_tokens"), _enum(raw["usage_source"], {"provider", "usage_unknown"}, "usage_source"), _text(raw["request_id"], "request_id", optional=True))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ResearchLLMLedgerEntry:
    call_id: str
    role: str
    model: str
    result_ref: str
    input_tokens: int | None
    output_tokens: int | None
    cache_tokens: int | None
    usage_source: UsageSource
    elapsed_ms: int
    coverage_gain: float
    quality_gain: float
    recorded_at: str
    def validate(self) -> None:
        for name, value in (("call_id", self.call_id), ("role", self.role), ("model", self.model), ("result_ref", self.result_ref)): _text(value, name)
        ResearchLLMResult("ref-only", self.model, self.input_tokens, self.output_tokens, self.cache_tokens, self.usage_source, None)
        _integer(self.elapsed_ms, "elapsed_ms"); _number(self.coverage_gain, "coverage_gain"); _number(self.quality_gain, "quality_gain"); _timestamp(self.recorded_at, "recorded_at")
    def __post_init__(self) -> None: self.validate()
    def to_json(self) -> dict[str, JsonValue]: return _finalize({"schema_version": 1, **{name: getattr(self, name) for name in ("call_id", "role", "model", "result_ref", "input_tokens", "output_tokens", "cache_tokens", "usage_source", "elapsed_ms", "coverage_gain", "quality_gain", "recorded_at")}})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ResearchLLMLedgerEntry:
        names = {"call_id", "role", "model", "result_ref", "input_tokens", "output_tokens", "cache_tokens", "usage_source", "elapsed_ms", "coverage_gain", "quality_gain", "recorded_at"}; raw = _object(value, {"schema_version", *names}, cls.__name__)
        def token(name: str) -> int | None: return None if raw[name] is None else _integer(raw[name], name)
        return cls(str(_text(raw["call_id"], "call_id")), str(_text(raw["role"], "role")), str(_text(raw["model"], "model")), str(_text(raw["result_ref"], "result_ref")), token("input_tokens"), token("output_tokens"), token("cache_tokens"), _enum(raw["usage_source"], {"provider", "usage_unknown"}, "usage_source"), _integer(raw["elapsed_ms"], "elapsed_ms"), _number(raw["coverage_gain"], "coverage_gain"), _number(raw["quality_gain"], "quality_gain"), str(_timestamp(raw["recorded_at"], "recorded_at")))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class DimensionAnalysis:
    dimension_id: str
    direct_answer: str | None
    fact_claim_ids: tuple[str, ...]
    inference_claim_ids: tuple[str, ...]
    limitation_claim_ids: tuple[str, ...]
    winning_evidence_ids: tuple[str, ...]
    relevance_score: float
    confidence: Literal["high", "medium", "low", "insufficient"]
    def validate(self) -> None:
        _text(self.dimension_id, "dimension_id"); _text(self.direct_answer, "direct_answer", optional=True)
        for name in ("fact_claim_ids", "inference_claim_ids", "limitation_claim_ids", "winning_evidence_ids"): _strings(list(getattr(self, name)), name)
        if _number(self.relevance_score, "relevance_score") > 1: raise ContractValidationError("relevance_score must be <= 1")
        _enum(self.confidence, {"high", "medium", "low", "insufficient"}, "confidence")
    def __post_init__(self) -> None: self.validate()
    def to_json(self) -> dict[str, JsonValue]: return _finalize({"schema_version": 1, "dimension_id": self.dimension_id, "direct_answer": self.direct_answer, "fact_claim_ids": list(self.fact_claim_ids), "inference_claim_ids": list(self.inference_claim_ids), "limitation_claim_ids": list(self.limitation_claim_ids), "winning_evidence_ids": list(self.winning_evidence_ids), "relevance_score": self.relevance_score, "confidence": self.confidence})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> DimensionAnalysis:
        raw = _object(value, {"schema_version", "dimension_id", "direct_answer", "fact_claim_ids", "inference_claim_ids", "limitation_claim_ids", "winning_evidence_ids", "relevance_score", "confidence"}, cls.__name__)
        return cls(str(_text(raw["dimension_id"], "dimension_id")), _text(raw["direct_answer"], "direct_answer", optional=True), _strings(raw["fact_claim_ids"], "fact_claim_ids"), _strings(raw["inference_claim_ids"], "inference_claim_ids"), _strings(raw["limitation_claim_ids"], "limitation_claim_ids"), _strings(raw["winning_evidence_ids"], "winning_evidence_ids"), _number(raw["relevance_score"], "relevance_score"), _enum(raw["confidence"], {"high", "medium", "low", "insufficient"}, "confidence"))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ReportQualityAudit:
    audit_id: str
    rubric_version: str
    relevance: float
    coverage: float
    source_quality: float
    synthesis_reasoning: float
    timeliness_uncertainty: float
    readability: float
    total_score: float
    hard_failures: tuple[str, ...]
    defects: tuple[str, ...]
    allowed_repairs: tuple[str, ...]
    passed: bool
    audited_at: str
    def validate(self) -> None:
        _text(self.audit_id, "audit_id"); _text(self.rubric_version, "rubric_version")
        maxima = {"relevance": 25, "coverage": 25, "source_quality": 20, "synthesis_reasoning": 15, "timeliness_uncertainty": 10, "readability": 5, "total_score": 100}
        for name, maximum in maxima.items():
            if _number(getattr(self, name), name) > maximum: raise ContractValidationError(f"{name} exceeds rubric maximum")
        for name in ("hard_failures", "defects", "allowed_repairs"): _strings(list(getattr(self, name)), name)
        _boolean(self.passed, "passed"); _timestamp(self.audited_at, "audited_at")
        component_total = self.relevance + self.coverage + self.source_quality + self.synthesis_reasoning + self.timeliness_uncertainty + self.readability
        if abs(component_total - self.total_score) > 0.051: raise ContractValidationError("total_score must equal component sum")
        if self.passed and (self.total_score < 80 or self.hard_failures): raise ContractValidationError("passed audit requires score >= 80 and no hard failures")
    def __post_init__(self) -> None: self.validate()
    def to_json(self) -> dict[str, JsonValue]: return _finalize({"schema_version": 1, **{name: (list(getattr(self, name)) if name in {"hard_failures", "defects", "allowed_repairs"} else getattr(self, name)) for name in ("audit_id", "rubric_version", "relevance", "coverage", "source_quality", "synthesis_reasoning", "timeliness_uncertainty", "readability", "total_score", "hard_failures", "defects", "allowed_repairs", "passed", "audited_at")}})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ReportQualityAudit:
        names = {"audit_id", "rubric_version", "relevance", "coverage", "source_quality", "synthesis_reasoning", "timeliness_uncertainty", "readability", "total_score", "hard_failures", "defects", "allowed_repairs", "passed", "audited_at"}; raw = _object(value, {"schema_version", *names}, cls.__name__)
        return cls(str(_text(raw["audit_id"], "audit_id")), str(_text(raw["rubric_version"], "rubric_version")), *(_number(raw[name], name) for name in ("relevance", "coverage", "source_quality", "synthesis_reasoning", "timeliness_uncertainty", "readability", "total_score")), _strings(raw["hard_failures"], "hard_failures"), _strings(raw["defects"], "defects"), _strings(raw["allowed_repairs"], "allowed_repairs"), _boolean(raw["passed"], "passed"), str(_timestamp(raw["audited_at"], "audited_at")))


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    status: DeliveryStatus
    reason_codes: tuple[str, ...]
    report_ref: str | None
    summary_ref: str
    evidence_snapshot_hash: str | None
    quality_audit_ref: str | None
    continue_until: str | None
    decided_at: str
    def validate(self) -> None:
        _enum(self.status, {"completed", "partial", "insufficient_evidence"}, "status"); _strings(list(self.reason_codes), "reason_codes")
        _text(self.report_ref, "report_ref", optional=True); _text(self.summary_ref, "summary_ref"); _text(self.evidence_snapshot_hash, "evidence_snapshot_hash", optional=True); _text(self.quality_audit_ref, "quality_audit_ref", optional=True); _timestamp(self.continue_until, "continue_until", optional=True); _timestamp(self.decided_at, "decided_at")
        if self.status == "completed" and self.report_ref is None: raise ContractValidationError("completed delivery requires report_ref")
        if self.status == "insufficient_evidence" and self.report_ref is not None: raise ContractValidationError("insufficient delivery cannot create a full report")
        if self.status in {"partial", "insufficient_evidence"} and (self.evidence_snapshot_hash is None or self.continue_until is None): raise ContractValidationError("continuable delivery requires pinned evidence snapshot and expiry")
    def __post_init__(self) -> None: self.validate()
    def to_json(self) -> dict[str, JsonValue]: return _finalize({"schema_version": 1, "status": self.status, "reason_codes": list(self.reason_codes), "report_ref": self.report_ref, "summary_ref": self.summary_ref, "evidence_snapshot_hash": self.evidence_snapshot_hash, "quality_audit_ref": self.quality_audit_ref, "continue_until": self.continue_until, "decided_at": self.decided_at})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> DeliveryDecision:
        raw = _object(value, {"schema_version", "status", "reason_codes", "report_ref", "summary_ref", "evidence_snapshot_hash", "quality_audit_ref", "continue_until", "decided_at"}, cls.__name__)
        return cls(_enum(raw["status"], {"completed", "partial", "insufficient_evidence"}, "status"), _strings(raw["reason_codes"], "reason_codes"), _text(raw["report_ref"], "report_ref", optional=True), str(_text(raw["summary_ref"], "summary_ref")), _text(raw["evidence_snapshot_hash"], "evidence_snapshot_hash", optional=True), _text(raw["quality_audit_ref"], "quality_audit_ref", optional=True), _timestamp(raw["continue_until"], "continue_until", optional=True), str(_timestamp(raw["decided_at"], "decided_at")))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ResearchOperationLineage:
    operation_id: str
    run_id: str
    parent_run_id: str | None
    parent_operation_id: str | None
    snapshot_hash: str | None
    parent_report_ref: str | None
    budget_lease_id: str
    created_at: str
    def validate(self) -> None:
        _text(self.operation_id, "operation_id"); _text(self.run_id, "run_id"); _text(self.parent_run_id, "parent_run_id", optional=True); _text(self.parent_operation_id, "parent_operation_id", optional=True); _text(self.snapshot_hash, "snapshot_hash", optional=True); _text(self.parent_report_ref, "parent_report_ref", optional=True); _text(self.budget_lease_id, "budget_lease_id"); _timestamp(self.created_at, "created_at")
        continuing = self.parent_run_id is not None
        if continuing != all(value is not None for value in (self.parent_operation_id, self.snapshot_hash)): raise ContractValidationError("parent lineage and snapshot must be declared together")
        if self.parent_run_id == self.run_id: raise ContractValidationError("lineage cannot parent itself")
    def __post_init__(self) -> None: self.validate()
    def to_json(self) -> dict[str, JsonValue]: return _finalize({"schema_version": 1, **{name: getattr(self, name) for name in ("operation_id", "run_id", "parent_run_id", "parent_operation_id", "snapshot_hash", "parent_report_ref", "budget_lease_id", "created_at")}})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ResearchOperationLineage:
        names = {"operation_id", "run_id", "parent_run_id", "parent_operation_id", "snapshot_hash", "parent_report_ref", "budget_lease_id", "created_at"}; raw = _object(value, {"schema_version", *names}, cls.__name__)
        return cls(str(_text(raw["operation_id"], "operation_id")), str(_text(raw["run_id"], "run_id")), _text(raw["parent_run_id"], "parent_run_id", optional=True), _text(raw["parent_operation_id"], "parent_operation_id", optional=True), _text(raw["snapshot_hash"], "snapshot_hash", optional=True), _text(raw["parent_report_ref"], "parent_report_ref", optional=True), str(_text(raw["budget_lease_id"], "budget_lease_id")), str(_timestamp(raw["created_at"], "created_at")))


@dataclass(frozen=True, slots=True)
class ResearchControlCommand:
    command_id: str
    run_id: str
    action: Literal["generate_now", "continue_research", "retry_from_start", "cancel_settle"]
    idempotency_key: str
    status: Literal["open", "accepted", "observed", "settled", "consumed", "rejected", "expired"]
    expected_run_version: int
    observed_checkpoint_id: str | None
    payload: dict[str, JsonValue]
    result: dict[str, JsonValue]
    expires_at: str
    created_at: str
    updated_at: str
    def validate(self) -> None:
        for name, value in (("command_id", self.command_id), ("run_id", self.run_id), ("idempotency_key", self.idempotency_key)): _text(value, name)
        _enum(self.action, {"generate_now", "continue_research", "retry_from_start", "cancel_settle"}, "action"); _enum(self.status, {"open", "accepted", "observed", "settled", "consumed", "rejected", "expired"}, "status"); _integer(self.expected_run_version, "expected_run_version"); _text(self.observed_checkpoint_id, "observed_checkpoint_id", optional=True); _json_object(self.payload, "payload"); _json_object(self.result, "result")
        for name in ("expires_at", "created_at", "updated_at"): _timestamp(getattr(self, name), name)
        if self.status in {"observed", "settled", "consumed"} and self.observed_checkpoint_id is None: raise ContractValidationError("observed command states require checkpoint identity")
    def __post_init__(self) -> None: self.validate()
    def to_json(self) -> dict[str, JsonValue]: return _finalize({"schema_version": 1, "command_id": self.command_id, "run_id": self.run_id, "action": self.action, "idempotency_key": self.idempotency_key, "status": self.status, "expected_run_version": self.expected_run_version, "observed_checkpoint_id": self.observed_checkpoint_id, "payload": copy.deepcopy(self.payload), "result": copy.deepcopy(self.result), "expires_at": self.expires_at, "created_at": self.created_at, "updated_at": self.updated_at})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ResearchControlCommand:
        raw = _object(value, {"schema_version", "command_id", "run_id", "action", "idempotency_key", "status", "expected_run_version", "observed_checkpoint_id", "payload", "result", "expires_at", "created_at", "updated_at"}, cls.__name__)
        return cls(str(_text(raw["command_id"], "command_id")), str(_text(raw["run_id"], "run_id")), _enum(raw["action"], {"generate_now", "continue_research", "retry_from_start", "cancel_settle"}, "action"), str(_text(raw["idempotency_key"], "idempotency_key")), _enum(raw["status"], {"open", "accepted", "observed", "settled", "consumed", "rejected", "expired"}, "status"), _integer(raw["expected_run_version"], "expected_run_version"), _text(raw["observed_checkpoint_id"], "observed_checkpoint_id", optional=True), _json_object(raw["payload"], "payload"), _json_object(raw["result"], "result"), str(_timestamp(raw["expires_at"], "expires_at")), str(_timestamp(raw["created_at"], "created_at")), str(_timestamp(raw["updated_at"], "updated_at")))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ResearchEvidenceSnapshot:
    snapshot_hash: str
    parent_run_id: str
    dimension_coverages: tuple[DimensionCoverage, ...]
    passage_blob_refs: tuple[str, ...]
    source_families: tuple[EvidenceSourceFamily, ...]
    query_fingerprints: tuple[str, ...]
    budget_summary: dict[str, JsonValue]
    created_at: str
    continue_until: str
    @staticmethod
    def _hash(payload: Mapping[str, JsonValue]) -> str:
        encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    def _content(self) -> dict[str, JsonValue]:
        return _finalize({"schema_version": 1, "parent_run_id": self.parent_run_id, "dimension_coverages": [item.to_json() for item in self.dimension_coverages], "passage_blob_refs": list(self.passage_blob_refs), "source_families": [item.to_json() for item in self.source_families], "query_fingerprints": list(self.query_fingerprints), "budget_summary": copy.deepcopy(self.budget_summary), "created_at": self.created_at, "continue_until": self.continue_until})
    def validate(self) -> None:
        _text(self.snapshot_hash, "snapshot_hash"); _text(self.parent_run_id, "parent_run_id"); _strings(list(self.passage_blob_refs), "passage_blob_refs"); _strings(list(self.query_fingerprints), "query_fingerprints"); _json_object(self.budget_summary, "budget_summary"); _timestamp(self.created_at, "created_at"); _timestamp(self.continue_until, "continue_until")
        if len({item.dimension_id for item in self.dimension_coverages}) != len(self.dimension_coverages): raise ContractValidationError("snapshot dimension ids must be unique")
        if len({item.family_id for item in self.source_families}) != len(self.source_families): raise ContractValidationError("snapshot source family ids must be unique")
        if self.snapshot_hash != self._hash(self._content()): raise ContractValidationError("snapshot_hash does not match canonical manifest")
    def __post_init__(self) -> None: self.validate()
    @classmethod
    def create(cls, *, parent_run_id: str, dimension_coverages: Sequence[DimensionCoverage], passage_blob_refs: Sequence[str], source_families: Sequence[EvidenceSourceFamily], query_fingerprints: Sequence[str], budget_summary: Mapping[str, JsonValue], created_at: str, continue_until: str) -> ResearchEvidenceSnapshot:
        provisional = {"schema_version": 1, "parent_run_id": parent_run_id, "dimension_coverages": [item.to_json() for item in dimension_coverages], "passage_blob_refs": list(passage_blob_refs), "source_families": [item.to_json() for item in source_families], "query_fingerprints": list(query_fingerprints), "budget_summary": copy.deepcopy(dict(budget_summary)), "created_at": created_at, "continue_until": continue_until}
        return cls(cls._hash(provisional), parent_run_id, tuple(dimension_coverages), tuple(passage_blob_refs), tuple(source_families), tuple(query_fingerprints), copy.deepcopy(dict(budget_summary)), created_at, continue_until)
    def to_json(self) -> dict[str, JsonValue]: return _finalize({"snapshot_hash": self.snapshot_hash, **self._content()})
    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> ResearchEvidenceSnapshot:
        raw = _object(value, {"schema_version", "snapshot_hash", "parent_run_id", "dimension_coverages", "passage_blob_refs", "source_families", "query_fingerprints", "budget_summary", "created_at", "continue_until"}, cls.__name__)
        if not isinstance(raw["dimension_coverages"], list) or not isinstance(raw["source_families"], list): raise ContractValidationError("snapshot nested contracts must be arrays")
        return cls(str(_text(raw["snapshot_hash"], "snapshot_hash")), str(_text(raw["parent_run_id"], "parent_run_id")), tuple(DimensionCoverage.from_json(item) for item in raw["dimension_coverages"]), _strings(raw["passage_blob_refs"], "passage_blob_refs"), tuple(EvidenceSourceFamily.from_json(item) for item in raw["source_families"]), _strings(raw["query_fingerprints"], "query_fingerprints"), _json_object(raw["budget_summary"], "budget_summary"), str(_timestamp(raw["created_at"], "created_at")), str(_timestamp(raw["continue_until"], "continue_until")))


def validate_terminal_projection(*, engine_status: str, delivery_decision: DeliveryDecision | None, terminal_public: Mapping[str, Any] | None) -> None:
    """Enforce that engine and research terminal states never impersonate each other."""
    _enum(engine_status, {"completed", "error", "cancelled"}, "engine_status")
    if engine_status != "completed":
        if delivery_decision is not None or terminal_public is not None:
            raise ContractValidationError("error/cancelled engine terminals cannot project research delivery")
        return
    if delivery_decision is None or terminal_public is None:
        raise ContractValidationError("completed engine terminal requires a delivery decision and public projection")
    raw = dict(terminal_public)
    if not set(raw).issubset({"delivery_status", "action_matrix"}) or set(raw) < {"delivery_status"} or raw["delivery_status"] != delivery_decision.status:
        raise ContractValidationError("terminal_public.delivery_status must equal delivery_decision.status")
    actions = raw.get("action_matrix", [])
    if not isinstance(actions, list):
        raise ContractValidationError("terminal_public.action_matrix must be an array")
    enabled_continue = False
    seen: set[str] = set()
    for item in actions:
        if not isinstance(item, Mapping) or set(item) != {"action_id", "enabled"}:
            raise ContractValidationError("terminal_public action entry is invalid")
        action_id = item.get("action_id")
        enabled = item.get("enabled")
        if action_id not in {"generate_now", "continue_research", "retry_from_start", "cancel_settle"} or action_id in seen or not isinstance(enabled, bool):
            raise ContractValidationError("terminal_public action entry is invalid")
        seen.add(str(action_id))
        enabled_continue = enabled_continue or (action_id == "continue_research" and enabled)
    continuable = delivery_decision.status in {"partial", "insufficient_evidence"}
    if enabled_continue != continuable:
        raise ContractValidationError("continue_research capability must match continuable delivery")
    if enabled_continue and (
        delivery_decision.evidence_snapshot_hash is None
        or len(delivery_decision.evidence_snapshot_hash) != 64
        or any(ch not in "0123456789abcdef" for ch in delivery_decision.evidence_snapshot_hash)
        or delivery_decision.continue_until is None
    ):
        raise ContractValidationError("continue_research requires a canonical pinned snapshot")


# Compatibility-friendly public spellings used by the plan and later slices.
LedgerEntry = ResearchLLMLedgerEntry
LLMBudgetLedger = ResearchBudgetLedger
ResearchControlDecision = ResearchControlCommand


__all__ = [
    "ContractValidationError",
    "CoverageStatus",
    "DeliveryDecision",
    "DeliveryStatus",
    "DimensionAnalysis",
    "DimensionCoverage",
    "DimensionImportance",
    "EngineTerminalStatus",
    "EvidenceSourceFamily",
    "GapWorkItem",
    "LLMBudgetLedger",
    "LedgerEntry",
    "ReportQualityAudit",
    "ResearchBrief",
    "ResearchBudgetLedger",
    "ResearchControlCommand",
    "ResearchControlDecision",
    "ResearchDimension",
    "ResearchEvidenceSnapshot",
    "ResearchLLMLedgerEntry",
    "ResearchLLMResult",
    "ResearchOperationLineage",
    "ResearchProfile",
    "UsageSource",
    "validate_terminal_projection",
]
