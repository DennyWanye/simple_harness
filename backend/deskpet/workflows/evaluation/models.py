"""Framework-neutral contracts for local workflow evaluation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EvaluatorType(StrEnum):
    CODE_RULE = "code_rule"
    VERIFY_GATE = "verify_gate"
    GOAL_CHECKER = "goal_checker"
    EXTERNAL = "external_evaluator"
    SELF_CHECK = "self_check_gate"
    PPT_VISUAL = "ppt_visual_review"
    LLM_JUDGE = "llm_judge"
    HUMAN = "human"
    PAIRWISE = "pairwise"
    LEGACY = "legacy"


class EvaluationVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    REVISE = "revise"
    ABSTAIN = "abstain"
    ERROR = "error"
    LEFT = "left"
    RIGHT = "right"
    TIE = "tie"


class PairwiseWinner(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    TIE = "tie"


def _required(value: str, field_name: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _string_tuple(values: tuple[str, ...] | list[str], field_name: str) -> tuple[str, ...]:
    normalized = tuple(str(value).strip() for value in values)
    if any(not value for value in normalized):
        raise ValueError(f"{field_name} must contain only non-empty strings")
    return normalized


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """Portable evaluator output with no dependency on a gate or graph engine."""

    evaluator_name: str
    evaluator_version: str
    evaluator_type: str
    verdict: str
    score: float | None = None
    labels: tuple[str, ...] = ()
    explanation: str | None = None
    evidence_refs: tuple[str, ...] = ()
    degraded: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluator_name", _required(self.evaluator_name, "evaluator_name"))
        object.__setattr__(self, "evaluator_version", _required(self.evaluator_version, "evaluator_version"))
        object.__setattr__(self, "evaluator_type", _required(self.evaluator_type, "evaluator_type"))
        object.__setattr__(self, "verdict", _required(self.verdict, "verdict").lower())
        object.__setattr__(self, "labels", _string_tuple(self.labels, "labels"))
        object.__setattr__(self, "evidence_refs", _string_tuple(self.evidence_refs, "evidence_refs"))
        if self.score is not None:
            score = float(self.score)
            if not math.isfinite(score):
                raise ValueError("score must be finite")
            object.__setattr__(self, "score", score)
        if self.explanation is not None:
            object.__setattr__(self, "explanation", str(self.explanation))

    @property
    def passed(self) -> bool:
        return self.verdict == EvaluationVerdict.PASS


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    evaluation_id: str
    trace_id: str
    outcome: EvaluationOutcome
    created_at: float
    run_id: str | None = None
    span_id: str | None = None


@dataclass(frozen=True, slots=True)
class HumanEvaluationRecord:
    trace_id: str
    evaluator_name: str
    evaluator_version: str
    verdict: str
    score: float | None = None
    labels: tuple[str, ...] = ()
    explanation: str | None = None
    evidence_refs: tuple[str, ...] = ()
    run_id: str | None = None
    span_id: str | None = None
    evaluation_id: str | None = None
    created_at: float | None = None

    def to_outcome(self) -> EvaluationOutcome:
        return EvaluationOutcome(
            evaluator_name=self.evaluator_name,
            evaluator_version=self.evaluator_version,
            evaluator_type=EvaluatorType.HUMAN,
            verdict=self.verdict,
            score=self.score,
            labels=("human", *self.labels),
            explanation=self.explanation,
            evidence_refs=self.evidence_refs,
        )


@dataclass(frozen=True, slots=True)
class PairwiseEvaluationRecord:
    trace_id: str
    evaluator_name: str
    evaluator_version: str
    left_version_key: str
    right_version_key: str
    winner: str
    score: float | None = None
    labels: tuple[str, ...] = ()
    explanation: str | None = None
    evidence_refs: tuple[str, ...] = ()
    run_id: str | None = None
    span_id: str | None = None
    evaluation_id: str | None = None
    created_at: float | None = None

    def __post_init__(self) -> None:
        winner = str(self.winner).strip().lower()
        if winner not in {item.value for item in PairwiseWinner}:
            raise ValueError("winner must be left, right, or tie")
        object.__setattr__(self, "winner", winner)
        _required(self.left_version_key, "left_version_key")
        _required(self.right_version_key, "right_version_key")

    def to_outcome(self) -> EvaluationOutcome:
        return EvaluationOutcome(
            evaluator_name=self.evaluator_name,
            evaluator_version=self.evaluator_version,
            evaluator_type=EvaluatorType.PAIRWISE,
            verdict=self.winner,
            score=self.score,
            labels=(
                "pairwise",
                f"left_version={self.left_version_key}",
                f"right_version={self.right_version_key}",
                f"winner={self.winner}",
                *self.labels,
            ),
            explanation=self.explanation,
            evidence_refs=self.evidence_refs,
        )


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    dataset_id: str
    name: str
    version: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class EvaluationExample:
    example_id: str
    dataset_id: str
    input_data: dict[str, Any]
    expected: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0


@dataclass(frozen=True, slots=True)
class EvaluationExperiment:
    experiment_id: str
    dataset_id: str
    version_key: str
    config: dict[str, Any]
    status: str
    started_at: float
    ended_at: float | None = None


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    experiment_id: str
    example_id: str
    status: str
    result: dict[str, Any]
    run_id: str | None = None
    evaluation_id: str | None = None
    score: float | None = None
    latency_ms: float | None = None
    error_taxonomy: str | None = None


@dataclass(frozen=True, slots=True)
class ExperimentAggregate:
    experiment_id: str
    version_key: str
    total: int
    completed: int
    passed: int
    errors: int
    pass_rate: float
    average_score: float | None
    average_latency_ms: float | None


@dataclass(frozen=True, slots=True)
class ExperimentComparison:
    left: ExperimentAggregate
    right: ExperimentAggregate
    pass_rate_delta: float
    average_score_delta: float | None
    average_latency_ms_delta: float | None
    error_delta: int
