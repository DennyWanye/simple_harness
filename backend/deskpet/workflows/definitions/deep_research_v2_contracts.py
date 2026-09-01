"""Strict JSON contracts shared by DeepResearch v2 branch nodes."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from ..contracts import JsonValue, validate_json_value


BRANCH_IDS = tuple(f"b{index}" for index in range(6))
BRANCH_STAGES = ("expand", "search", "direct", "fetch", "score")


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
        work_item=work_item, stage=stage, result=[], errors=[],
        budget_before=budget, budget_after=budget,
    )


def canonical_url(value: str) -> str:
    return value.strip().lower().rstrip("/")


__all__ = [
    "BRANCH_IDS", "BRANCH_STAGES", "BranchBudgetState", "BranchWorkItem",
    "branch_patch", "canonical_url", "no_op_patch",
]
