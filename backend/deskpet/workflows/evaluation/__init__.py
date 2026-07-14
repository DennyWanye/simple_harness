"""Local, durable workflow evaluation primitives."""

from .adapters import adapt_bool, adapt_exception, adapt_legacy_result, adapt_mapping, adapt_score
from .models import (
    EvaluationDataset,
    EvaluationExample,
    EvaluationExperiment,
    EvaluationOutcome,
    EvaluationRecord,
    EvaluationVerdict,
    EvaluatorType,
    ExperimentAggregate,
    ExperimentComparison,
    ExperimentResult,
    HumanEvaluationRecord,
    PairwiseEvaluationRecord,
    PairwiseWinner,
)
from .runner import EvaluationExecution, LocalEvaluationRunner
from .store import EvaluationStore

__all__ = [
    "EvaluationDataset",
    "EvaluationExample",
    "EvaluationExperiment",
    "EvaluationExecution",
    "EvaluationOutcome",
    "EvaluationRecord",
    "EvaluationVerdict",
    "EvaluatorType",
    "ExperimentAggregate",
    "ExperimentComparison",
    "ExperimentResult",
    "HumanEvaluationRecord",
    "LocalEvaluationRunner",
    "PairwiseEvaluationRecord",
    "PairwiseWinner",
    "EvaluationStore",
    "adapt_bool",
    "adapt_exception",
    "adapt_legacy_result",
    "adapt_mapping",
    "adapt_score",
]
