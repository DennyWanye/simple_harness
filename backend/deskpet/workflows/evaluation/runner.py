"""Deterministic, dependency-injected foundation for local eval suites."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .adapters import adapt_exception, adapt_legacy_result
from .models import (
    EvaluationExample,
    EvaluationOutcome,
    EvaluationVerdict,
    EvaluatorType,
    ExperimentAggregate,
    ExperimentResult,
)
from .store import EvaluationStore


@dataclass(frozen=True, slots=True)
class EvaluationExecution:
    """One executor output plus optional product-run correlation."""

    output: Any
    trace_id: str | None = None
    run_id: str | None = None
    span_id: str | None = None


Executor = Callable[[EvaluationExample], Any | Awaitable[Any]]
Evaluator = Callable[[EvaluationExample, Any], Any | Awaitable[Any]]


def _stable_id(kind: str, experiment_id: str, example_id: str) -> str:
    payload = f"{kind}\0{experiment_id}\0{example_id}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


def _json_safe(value: Any) -> Any:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return json.loads(serialized)


async def _resolve(value: Any | Awaitable[Any]) -> Any:
    return await value if inspect.isawaitable(value) else value


class LocalEvaluationRunner:
    """Run a dataset in stable example order without requiring a live service."""

    def __init__(
        self,
        store: EvaluationStore,
        *,
        evaluator_name: str,
        evaluator_version: str,
        evaluator_type: str = EvaluatorType.CODE_RULE,
        pass_threshold: float | None = None,
        higher_is_better: bool = True,
        monotonic=time.perf_counter,
    ) -> None:
        self.store = store
        self.evaluator_name = evaluator_name
        self.evaluator_version = evaluator_version
        self.evaluator_type = evaluator_type
        self.pass_threshold = pass_threshold
        self.higher_is_better = higher_is_better
        self._monotonic = monotonic

    async def run(
        self,
        *,
        dataset_id: str,
        version_key: str,
        execute: Executor,
        evaluate: Evaluator,
        config: dict[str, Any] | None = None,
        experiment_id: str | None = None,
    ) -> ExperimentAggregate:
        experiment = await self.store.start_experiment(
            dataset_id=dataset_id,
            version_key=version_key,
            config=config,
            experiment_id=experiment_id,
        )
        errors = 0
        for example in await self.store.list_examples(dataset_id):
            if await self._run_example(experiment.experiment_id, example, execute, evaluate):
                errors += 1
        await self.store.finish_experiment(
            experiment.experiment_id,
            status="completed_with_errors" if errors else "completed",
        )
        return await self.store.aggregate(experiment.experiment_id)

    async def _run_example(
        self,
        experiment_id: str,
        example: EvaluationExample,
        execute: Executor,
        evaluate: Evaluator,
    ) -> bool:
        started = self._monotonic()
        evaluation_id = _stable_id("evaluation", experiment_id, example.example_id)
        fallback_trace_id = _stable_id("trace", experiment_id, example.example_id)
        execution = EvaluationExecution(output=None, trace_id=fallback_trace_id)
        stage = "execute"
        try:
            raw_execution = await _resolve(execute(example))
            execution = (
                raw_execution
                if isinstance(raw_execution, EvaluationExecution)
                else EvaluationExecution(output=raw_execution, trace_id=fallback_trace_id)
            )
            normalized_output = _json_safe(execution.output)
            stage = "evaluate"
            raw_outcome = await _resolve(evaluate(example, execution.output))
            outcome = adapt_legacy_result(
                raw_outcome,
                evaluator_name=self.evaluator_name,
                evaluator_version=self.evaluator_version,
                evaluator_type=self.evaluator_type,
                pass_threshold=self.pass_threshold,
                higher_is_better=self.higher_is_better,
                conservative=True,
            )
            trace_id = execution.trace_id or fallback_trace_id
            evaluation = await self.store.record_evaluation(
                trace_id=trace_id,
                run_id=execution.run_id,
                span_id=execution.span_id,
                evaluation_id=evaluation_id,
                outcome=outcome,
            )
            latency_ms = max(0.0, (self._monotonic() - started) * 1000.0)
            degraded = outcome.degraded or outcome.verdict == EvaluationVerdict.ERROR
            await self.store.record_result(
                ExperimentResult(
                    experiment_id=experiment_id,
                    example_id=example.example_id,
                    run_id=execution.run_id,
                    evaluation_id=evaluation.evaluation_id,
                    status="error" if degraded else "completed",
                    score=outcome.score,
                    latency_ms=latency_ms,
                    error_taxonomy="degraded_evaluator" if degraded else None,
                    result={
                        "output": normalized_output,
                        "verdict": outcome.verdict,
                        "degraded": outcome.degraded,
                    },
                )
            )
            return degraded
        except Exception as error:
            latency_ms = max(0.0, (self._monotonic() - started) * 1000.0)
            outcome = adapt_exception(
                error,
                evaluator_name=self.evaluator_name,
                evaluator_version=self.evaluator_version,
                evaluator_type=self.evaluator_type,
            )
            error_evaluation_id = f"{evaluation_id}-error"
            evaluation = await self.store.record_evaluation(
                trace_id=execution.trace_id or fallback_trace_id,
                run_id=execution.run_id,
                span_id=execution.span_id,
                evaluation_id=error_evaluation_id,
                outcome=outcome,
            )
            await self.store.record_result(
                ExperimentResult(
                    experiment_id=experiment_id,
                    example_id=example.example_id,
                    run_id=execution.run_id,
                    evaluation_id=evaluation.evaluation_id,
                    status="error",
                    latency_ms=latency_ms,
                    error_taxonomy=f"{stage}:{type(error).__name__}",
                    result={"stage": stage, "error_type": type(error).__name__},
                )
            )
            return True
