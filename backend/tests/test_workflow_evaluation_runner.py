from __future__ import annotations

import pytest

from deskpet.workflows.evaluation import EvaluationStore, LocalEvaluationRunner


@pytest.mark.asyncio
async def test_local_runner_uses_stable_order_and_records_degraded_errors(tmp_path):
    store = EvaluationStore(tmp_path / "workflow.db", clock=lambda: 100.0)
    dataset = await store.create_dataset(name="fixed-suite", version="1")
    for example_id, value in (("c", 0), ("a", 2), ("b", 1)):
        await store.add_example(
            dataset_id=dataset.dataset_id,
            example_id=example_id,
            input_data={"value": value},
            expected={"minimum": 2},
        )

    order: list[str] = []

    def execute(example):
        order.append(example.example_id)
        return {"value": example.input_data["value"]}

    def evaluate(example, output):
        if example.example_id == "b":
            raise RuntimeError("judge unavailable")
        return output["value"] >= example.expected["minimum"]

    ticks = iter((0.0, 0.01, 1.0, 1.02, 2.0, 2.03))
    runner = LocalEvaluationRunner(
        store,
        evaluator_name="fixed-rule",
        evaluator_version="rule-hash",
        monotonic=lambda: next(ticks),
    )
    summary = await runner.run(
        dataset_id=dataset.dataset_id,
        version_key="workflow:model:prompt:tool-schema:evaluator",
        execute=execute,
        evaluate=evaluate,
        experiment_id="experiment-fixed",
    )

    assert order == ["a", "b", "c"]
    assert summary.total == 3
    assert summary.completed == 2
    assert summary.passed == 1
    assert summary.errors == 1
    assert summary.pass_rate == pytest.approx(1 / 3)

    results = await store.list_results("experiment-fixed")
    assert [result.example_id for result in results] == ["a", "b", "c"]
    assert results[1].status == "error"
    assert results[1].error_taxonomy == "evaluate:RuntimeError"
    error_record = await store.get_evaluation(results[1].evaluation_id)
    assert error_record is not None
    assert error_record.outcome.verdict == "error"
    assert error_record.outcome.degraded is True
