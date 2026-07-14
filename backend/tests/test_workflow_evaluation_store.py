from __future__ import annotations

import pytest

from deskpet.workflows.evaluation import (
    EvaluationOutcome,
    EvaluationStore,
    ExperimentResult,
    HumanEvaluationRecord,
    PairwiseEvaluationRecord,
)


@pytest.mark.asyncio
async def test_store_persists_human_and_pairwise_records(tmp_path):
    store = EvaluationStore(tmp_path / "workflow.db", clock=lambda: 123.0)
    human = await store.record_human(
        HumanEvaluationRecord(
            trace_id="trace-1",
            run_id="run-1",
            span_id="span-1",
            evaluator_name="reviewer-local",
            evaluator_version="rubric-v3",
            verdict="pass",
            score=0.9,
            labels=("useful",),
            explanation="Meets the rubric.",
            evidence_refs=("artifact:deck",),
            evaluation_id="human-1",
        )
    )
    pairwise = await store.record_pairwise(
        PairwiseEvaluationRecord(
            trace_id="trace-2",
            evaluator_name="pairwise-rule",
            evaluator_version="1",
            left_version_key="workflow:a",
            right_version_key="workflow:b",
            winner="right",
            explanation="B has complete citations.",
            evaluation_id="pairwise-1",
        )
    )

    loaded_human = await store.get_evaluation(human.evaluation_id)
    loaded_pairwise = await store.get_evaluation(pairwise.evaluation_id)
    assert loaded_human is not None
    assert loaded_human.outcome.evaluator_type == "human"
    assert loaded_human.outcome.labels == ("human", "useful")
    assert loaded_human.outcome.evidence_refs == ("artifact:deck",)
    assert loaded_pairwise is not None
    assert loaded_pairwise.outcome.evaluator_type == "pairwise"
    assert loaded_pairwise.outcome.verdict == "right"
    assert "left_version=workflow:a" in loaded_pairwise.outcome.labels
    assert "right_version=workflow:b" in loaded_pairwise.outcome.labels


@pytest.mark.asyncio
async def test_store_compares_pass_rate_score_latency_and_errors(tmp_path):
    store = EvaluationStore(tmp_path / "workflow.db", clock=lambda: 50.0)
    dataset = await store.create_dataset(name="code-fixed", version="1")
    example = await store.add_example(
        dataset_id=dataset.dataset_id,
        example_id="example",
        input_data={"task": "edit"},
        expected={"tests": "pass"},
    )
    left = await store.start_experiment(dataset_id=dataset.dataset_id, version_key="workflow:left")
    right = await store.start_experiment(dataset_id=dataset.dataset_id, version_key="workflow:right")

    left_eval = await store.record_evaluation(
        trace_id="left-trace",
        evaluation_id="left-eval",
        outcome=EvaluationOutcome("rule", "1", "code_rule", "fail", score=0.2),
    )
    right_eval = await store.record_evaluation(
        trace_id="right-trace",
        evaluation_id="right-eval",
        outcome=EvaluationOutcome("rule", "1", "code_rule", "pass", score=0.8),
    )
    await store.record_result(
        ExperimentResult(
            experiment_id=left.experiment_id,
            example_id=example.example_id,
            evaluation_id=left_eval.evaluation_id,
            status="completed",
            score=0.2,
            latency_ms=20.0,
            result={"output": "left"},
        )
    )
    await store.record_result(
        ExperimentResult(
            experiment_id=right.experiment_id,
            example_id=example.example_id,
            evaluation_id=right_eval.evaluation_id,
            status="completed",
            score=0.8,
            latency_ms=12.0,
            result={"output": "right"},
        )
    )

    comparison = await store.compare(left.experiment_id, right.experiment_id)
    assert comparison.left.pass_rate == 0.0
    assert comparison.right.pass_rate == 1.0
    assert comparison.pass_rate_delta == 1.0
    assert comparison.average_score_delta == pytest.approx(0.6)
    assert comparison.average_latency_ms_delta == -8.0
    assert comparison.error_delta == 0
