from __future__ import annotations

import pytest

from deskpet.workflows.trace import SpanKind, SpanStatus, TraceRedactor, TraceStore, use_span
from deskpet.workflows.store import WorkflowRunStore


@pytest.mark.asyncio
async def test_trace_tree_propagates_parent_and_redacts(tmp_path):
    now = [10.0]
    store = TraceStore(tmp_path / "workflow.db", clock=lambda: now[0])
    run = await store.start_run(session_id="s", kind="workflow", run_id="run")
    root = await store.start_span(
        trace_id=run.trace_id,
        run_id="run",
        name="deep-research",
        kind=SpanKind.WORKFLOW,
        attributes={"authorization": "Bearer secret-value-123456", "safe": "yes"},
    )
    with use_span(store.context(root)):
        child = await store.start_span(trace_id=run.trace_id, name="search", kind=SpanKind.NODE)
    now[0] = 10.5
    await store.finish_span(child.span_id, SpanStatus.OK)
    await store.finish_span(root.span_id, SpanStatus.OK)
    await store.finish_run(run.trace_id, SpanStatus.OK)

    tree = await store.tree(run.trace_id)
    assert tree is not None
    assert tree["run"]["status"] == "ok"
    spans = {span["name"]: span for span in tree["spans"]}
    assert spans["deep-research"]["attributes_json"] == '{"authorization": "[REDACTED]", "safe": "yes"}'
    assert spans["search"]["parent_span_id"] == root.span_id
    assert spans["search"]["duration_ms"] == 500.0


def test_redaction_rules_fail_closed_when_resource_is_missing(tmp_path):
    redactor = TraceRedactor.from_file(str(tmp_path / "missing.json"))
    assert redactor.redact("ordinary detail") == "[REDACTED]"


@pytest.mark.asyncio
async def test_succeeded_pending_span_reconciles_after_result_commit_crash(tmp_path):
    path = tmp_path / "workflow.db"
    runs = WorkflowRunStore(path)
    run_id, _ = await runs.create_run(
        request_key="trace-crash",
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="native-test",
        workflow_version="v1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=1,
    )
    trace = TraceStore(path, clock=lambda: 20.0)
    trace_run = await trace.start_run(
        trace_id="trace-crash",
        run_id=run_id,
        session_id="session",
        kind="workflow",
    )
    handler_invocations = 1
    span = await trace.start_span(
        trace_id=trace_run.trace_id,
        run_id=run_id,
        name="native-node",
        kind=SpanKind.NODE,
        node_id="node-1",
        attributes={
            "task_id": "task-1",
            "engine_kind": "deskpet-native",
            "snapshot_version": 1,
        },
    )
    db = await runs._connect()
    try:
        await db.execute(
            """INSERT INTO workflow_nodes(
                node_execution_id,run_id,node_id,base_checkpoint_id,invocation_key,
                task_id,task_path,latest_attempt,latest_status,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "execution-1", run_id, "node-1", "checkpoint-1", "invoke-1",
                "task-1", "node-1", 1, "succeeded_pending", 19.0,
            ),
        )
        await db.commit()
    finally:
        await db.close()

    reconciled = await trace.reconcile_native_node_spans(run_id)
    tree = await trace.tree(trace_run.trace_id)

    assert reconciled == 1
    assert handler_invocations == 1
    assert tree is not None
    recovered = next(item for item in tree["spans"] if item["span_id"] == span.span_id)
    assert recovered["status"] == SpanStatus.OK
    assert recovered["ended_at"] == 20.0
