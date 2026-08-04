"""Immutable JSON contracts for the DeepResearch v3 workflow."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Any, Literal, Mapping

from ..contracts import JsonValue, validate_json_value


BRANCH_IDS = tuple(f"b{index}" for index in range(6))
BRANCH_STAGES = ("expand", "search", "direct", "fetch", "score")
ClaimKind = Literal["factual", "inference", "opinion"]


@dataclass(frozen=True, slots=True)
class BranchBudgetState:
    query_remaining: int = 6
    url_remaining: int = 12
    fetch_remaining: int = 8
    llm_remaining: int = 4
    engine_retry_limit: int = 2
    deadline_at: float = 0.0

    def __post_init__(self) -> None:
        if any(value < 0 for value in (
            self.query_remaining, self.url_remaining, self.fetch_remaining,
            self.llm_remaining, self.engine_retry_limit,
        )):
            raise ValueError("branch budgets must be non-negative")

    def to_json(self) -> dict[str, JsonValue]:
        result = asdict(self)
        validate_json_value(result)
        return result

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "BranchBudgetState":
        return cls(**{name: value[name] for name in cls.__dataclass_fields__ if name in value})

    def consume(self, **amounts: int) -> "BranchBudgetState":
        values = asdict(self)
        for name, amount in amounts.items():
            if name not in values or name in {"deadline_at", "engine_retry_limit"}:
                raise ValueError(f"unknown consumable budget: {name}")
            values[name] = max(0, int(values[name]) - max(0, int(amount)))
        return BranchBudgetState(**values)


@dataclass(frozen=True, slots=True)
class BranchWorkItem:
    branch_id: str
    active: bool
    mode: str
    questions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.branch_id not in BRANCH_IDS or self.mode not in {"fanout", "flat"}:
            raise ValueError("invalid branch work item")
        if self.active != bool(self.questions):
            raise ValueError("active branch must have questions and no-op branch must not")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "branch_id": self.branch_id,
            "active": self.active,
            "mode": self.mode,
            "questions": list(self.questions),
        }


@dataclass(frozen=True, slots=True)
class EvidencePassage:
    passage_id: str
    source_citation_id: int
    source_content_hash: str
    canonical_url: str
    question_id: str
    text: str
    start: int
    end: int
    relevance: float

    def __post_init__(self) -> None:
        if not self.passage_id or self.source_citation_id < 1:
            raise ValueError("invalid evidence passage identity")
        if self.start < 0 or self.end <= self.start or not self.text.strip():
            raise ValueError("invalid evidence passage span")
        if not 0.0 <= self.relevance <= 1.0:
            raise ValueError("invalid evidence passage relevance")

    def to_json(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = asdict(self)
        validate_json_value(value)
        return value

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "EvidencePassage":
        return cls(
            passage_id=str(value["passage_id"]),
            source_citation_id=int(value["source_citation_id"]),
            source_content_hash=str(value["source_content_hash"]),
            canonical_url=str(value.get("canonical_url") or ""),
            question_id=str(value.get("question_id") or ""),
            text=str(value["text"]),
            start=int(value["start"]),
            end=int(value["end"]),
            relevance=float(value["relevance"]),
        )


@dataclass(frozen=True, slots=True)
class AtomicClaim:
    claim_id: str
    text: str
    kind: ClaimKind
    citation_ids: tuple[int, ...]
    repaired_from: str | None = None

    def __post_init__(self) -> None:
        if not self.claim_id or not self.text.strip():
            raise ValueError("invalid atomic claim")
        if self.kind not in {"factual", "inference", "opinion"}:
            raise ValueError("invalid claim kind")
        if any(value < 1 for value in self.citation_ids):
            raise ValueError("invalid claim citation")

    def to_json(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "claim_id": self.claim_id,
            "text": self.text,
            "kind": self.kind,
            "citation_ids": list(self.citation_ids),
            "repaired_from": self.repaired_from,
        }
        validate_json_value(value)
        return value

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "AtomicClaim":
        raw_citations = value.get("citation_ids")
        if not isinstance(raw_citations, list):
            raise ValueError("invalid claim citations")
        return cls(
            claim_id=str(value["claim_id"]),
            text=str(value["text"]),
            kind=str(value["kind"]),  # type: ignore[arg-type]
            citation_ids=tuple(int(item) for item in raw_citations),
            repaired_from=(str(value["repaired_from"]) if value.get("repaired_from") else None),
        )


@dataclass(frozen=True, slots=True)
class SupportDecision:
    claim_id: str
    supported: bool
    winning_passage_ids: tuple[str, ...]
    winning_source_citation_ids: tuple[int, ...]
    reason_codes: tuple[str, ...]
    lexical_score: float

    def to_json(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "claim_id": self.claim_id,
            "supported": self.supported,
            "winning_passage_ids": list(self.winning_passage_ids),
            "winning_source_citation_ids": list(self.winning_source_citation_ids),
            "reason_codes": list(self.reason_codes),
            "lexical_score": self.lexical_score,
        }
        validate_json_value(value)
        return value


@dataclass(frozen=True, slots=True)
class PublishDecision:
    passed: bool
    factual_claim_count_pre_repair: int
    supported_factual_count: int
    published_factual_count: int
    support_rate: float
    citation_count: int
    independent_domain_count: int
    body_bytes: int
    reason_codes: tuple[str, ...]

    def to_json(self) -> dict[str, JsonValue]:
        value: dict[str, JsonValue] = {
            "passed": self.passed,
            "factual_claim_count_pre_repair": self.factual_claim_count_pre_repair,
            "supported_factual_count": self.supported_factual_count,
            "published_factual_count": self.published_factual_count,
            "support_rate": self.support_rate,
            "citation_count": self.citation_count,
            "independent_domain_count": self.independent_domain_count,
            "body_bytes": self.body_bytes,
            "reason_codes": list(self.reason_codes),
        }
        validate_json_value(value)
        return value


def branch_patch(
    *,
    work_item: BranchWorkItem,
    stage: str,
    result: list[JsonValue],
    errors: list[dict[str, JsonValue]],
    budget_before: BranchBudgetState,
    budget_after: BranchBudgetState,
) -> dict[str, JsonValue]:
    if stage not in BRANCH_STAGES:
        raise ValueError("invalid branch stage")
    payload: dict[str, JsonValue] = {
        "branch_id": work_item.branch_id,
        "stage": stage,
        "active": work_item.active,
        "mode": work_item.mode,
        "questions": list(work_item.questions),
        "result": copy.deepcopy(result),
        "errors": copy.deepcopy(errors),
        "budget_before": budget_before.to_json(),
        "budget_after": budget_after.to_json(),
    }
    validate_json_value(payload)
    return payload


def no_op_patch(
    work_item: BranchWorkItem,
    stage: str,
    budget: BranchBudgetState,
) -> dict[str, JsonValue]:
    return branch_patch(
        work_item=work_item,
        stage=stage,
        result=[],
        errors=[],
        budget_before=budget,
        budget_after=budget,
    )


def canonical_url(value: str) -> str:
    return value.strip().lower().rstrip("/")


__all__ = [
    "BRANCH_IDS", "BRANCH_STAGES", "AtomicClaim", "BranchBudgetState",
    "BranchWorkItem", "ClaimKind", "EvidencePassage", "PublishDecision",
    "SupportDecision", "branch_patch", "canonical_url", "no_op_patch",
]
