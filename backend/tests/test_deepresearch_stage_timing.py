from __future__ import annotations

from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from deskpet.workflows.contracts import NodeExecutionIdentity
from deskpet.workflows.store import WorkflowRunStore
from deskpet.workflows.trace import TraceStore
from deskpet.workflows.trace.observer import WorkflowExecutionObserver
from observability.metrics_sink import MetricsSink


class _Store:
    async def record_node_start(self, **kwargs):  # noqa: ANN003
        return f"execution-{kwargs['task_id']}"

    async def record_node_finish(self, execution_id, attempt, status, *, error_ref=None):  # noqa: ANN001
        return {
            "started_at": 100.0,
            "ended_at": 100.125,
            "duration_ms": 125.0,
        }


class _Traces:
    def __init__(self) -> None:
        self.finished = []

    async def start_span(self, **kwargs):  # noqa: ANN003
        return SimpleNamespace(span_id=kwargs["span_id"])

    async def finish_span(self, span_id, status, **kwargs):  # noqa: ANN001, ANN003
        self.finished.append((span_id, status, kwargs))


def _identity(status: str) -> NodeExecutionIdentity:
    return NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v5",
        thread_id="thread-timing",
        run_id="run-timing",
        checkpoint_id="checkpoint-timing",
        checkpoint_ns="",
        task_id=f"task-{status}",
        node_id="fetch",
        attempt=2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    ("succeeded_pending", "waiting", "cancelled", "retryable", "failed"),
)
async def test_deepresearch_observer_mirrors_every_terminal_stage_timing(
    status,
    monkeypatch,
):
    metrics = []
    monkeypatch.setattr(
        "observability.metrics_sink.record",
        lambda event, detail: metrics.append((event, detail)) or True,
    )
    observer = WorkflowExecutionObserver(_Store(), _Traces(), trace_id="trace-timing")
    identity = _identity(status)

    with capture_logs() as logs:
        await observer.node_started(identity)
        await observer.node_finished(
            identity,
            status,
            error=(RuntimeError(status) if status != "succeeded_pending" else None),
        )

    expected = {
        "run_id": "run-timing",
        "workflow_version": "v5",
        "stage": "fetch",
        "attempt": 2,
        "status": status,
        "duration_ms": 125.0,
    }
    assert metrics == [("deepresearch_stage_timing", expected)]
    event = next(row for row in logs if row.get("event") == "deepresearch_stage_timing")
    assert {key: event[key] for key in expected} == expected
    assert event["started_at"] == 100.0
    assert event["ended_at"] == 100.125


@pytest.mark.asyncio
async def test_node_attempt_finish_returns_durable_wall_clock_timing(tmp_path):
    now = [100.0]
    store = WorkflowRunStore(tmp_path / "workflow.db", clock=lambda: now[0])
    run_id, _ = await store.create_run(
        request_key="session:req:turn:deep-research-timing",
        session_id="session",
        request_id="req",
        turn_id="turn",
        workflow_name="deep_research",
        workflow_version="v5",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="caps",
        capability_snapshot={"tools": ["web_search"]},
        state_schema_version=1,
    )
    execution_id = await store.record_node_start(
        run_id=run_id,
        node_id="search",
        checkpoint_id="checkpoint",
        task_id="task",
        attempt=1,
    )

    now[0] = 102.5
    timing = await store.record_node_finish(
        execution_id,
        1,
        "succeeded_pending",
    )

    assert timing == {
        "started_at": 100.0,
        "ended_at": 102.5,
        "duration_ms": 2500.0,
    }


@pytest.mark.asyncio
async def test_stage_timing_is_persisted_to_metrics_and_trace(tmp_path, monkeypatch):
    now = [200.0]
    database = tmp_path / "workflow.db"
    store = WorkflowRunStore(database, clock=lambda: now[0])
    traces = TraceStore(database, clock=lambda: now[0])
    metrics = MetricsSink(tmp_path / "metrics.jsonl")
    monkeypatch.setattr("observability.metrics_sink._default_sink", metrics)
    run_id, _ = await store.create_run(
        request_key="session:req:turn:deep-research-timing-persisted",
        session_id="session",
        request_id="req",
        turn_id="turn",
        workflow_name="deep_research",
        workflow_version="v5",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="caps",
        capability_snapshot={"tools": ["web_search"]},
        state_schema_version=1,
    )
    await traces.start_run(
        trace_id="trace-persisted",
        run_id=run_id,
        session_id="session",
        kind="workflow",
        workflow_name="deep_research",
        workflow_version="v5",
    )
    observer = WorkflowExecutionObserver(store, traces, trace_id="trace-persisted")
    identity = NodeExecutionIdentity(
        workflow_name="deep_research",
        workflow_version="v5",
        thread_id="thread-persisted",
        run_id=run_id,
        checkpoint_id="checkpoint-persisted",
        checkpoint_ns="",
        task_id="task-persisted",
        node_id="search",
        attempt=1,
    )

    await observer.node_started(identity)
    now[0] = 201.25
    await observer.node_finished(identity, "succeeded_pending")

    row = metrics.read_all()[-1]
    assert row["event"] == "deepresearch_stage_timing"
    assert row["detail"] == {
        "run_id": run_id,
        "workflow_version": "v5",
        "stage": "search",
        "attempt": 1,
        "status": "succeeded_pending",
        "duration_ms": 1250.0,
    }
    tree = await traces.tree("trace-persisted")
    assert tree is not None
    span = tree["spans"][0]
    assert span["ended_at"] == 201.25
    assert span["duration_ms"] == 1250.0
