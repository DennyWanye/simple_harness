from __future__ import annotations

from dataclasses import dataclass

import pytest

from deskpet.workflows.definition import WorkflowManifest
from deskpet.workflows.evaluation import HumanEvaluationRecord
from deskpet.workflows.outbox import OutboxError, stable_delivery_id, stable_event_id
from deskpet.workflows.runner import WorkflowRegistry, WorkflowRunner
from deskpet.workflows.service import WorkflowService, WorkflowServiceError
from deskpet.workflows.store import FencedAsyncSqliteSaver, WorkflowRunStore
from deskpet.workflows.trace import SpanKind, TraceStore


def _manifest() -> WorkflowManifest:
    return WorkflowManifest(
        workflow_name="service-workflow",
        workflow_version="1",
        state_schema_version=1,
        durability="sync",
        recursion_limit=32,
        max_supersteps=16,
        definition_hash="definition-v1",
        state_hash="state-v1",
        prompt_hash="prompt-v1",
        tool_hash="tool-v1",
        policy_hash="policy-v1",
        callable_source_hash="callable-v1",
        dependency_lock_hash="lock-v1",
        implementation_bundle_hash="implementation-v1",
    )


@dataclass
class _Workflow:
    manifest: WorkflowManifest


class _Executable:
    manifest = _manifest()

    async def ainvoke(self, state, context, **kwargs):
        return state

    async def resume(self, responses, context, **kwargs):
        return responses


async def _service(tmp_path):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path)
    registry = WorkflowRegistry()
    executable = _Executable()
    registry.register(_Workflow(executable.manifest), executable=executable)
    runner = WorkflowRunner(store, FencedAsyncSqliteSaver(path), registry, owner="service-test")
    return WorkflowService(store, runner), store


async def _start(service: WorkflowService, **overrides):
    values = {
        "venue": "agent-loop",
        "base_session_id": "session-base",
        "delivery_session_id": "session-delivery",
        "request_id": "request-1",
        "turn_id": "turn-1",
        "workflow_name": "service-workflow",
        "workflow_version": "1",
        "capability_snapshot": {"tools": ["read_file"]},
        "start_payload": {"topic": "durability"},
    }
    values.update(overrides)
    return await service.start_workflow(**values)


@pytest.mark.asyncio
async def test_start_identity_snapshot_and_outbox_are_idempotent(tmp_path):
    service, store = await _service(tmp_path)

    first = await _start(service, base_epoch=7)
    second = await _start(service, base_epoch=7)

    assert first["run_id"] == second["run_id"]
    assert first["created"] is True
    assert second["created"] is False
    assert first["accepted_event_id"] == stable_event_id(
        first["run_id"], "run:create:accepted"
    )
    assert second["accepted_event_id"] == first["accepted_event_id"]
    accepted = first["accepted_event"]
    assert accepted["payload"]["card"]["status"] == "running"
    assert accepted["hydration"]["apply_before_seen"] is True
    assert accepted["hydration"]["card_mutating"] is True
    deliveries = accepted["deliveries"]
    assert {item["channel"] for item in deliveries} == {"session_message", "websocket"}
    for item in deliveries:
        assert item["delivery_id"] == stable_delivery_id(
            accepted["event_id"], item["channel"], item["target_id"]
        )
    assert await store.get_session_ref(first["run_id"], "base") == {
        "run_id": first["run_id"],
        "session_kind": "base",
        "session_id": "session-base",
        "session_epoch": 7,
        "deleted_at": None,
    }
    assert (await store.get_session_ref(first["run_id"], "delivery"))["session_epoch"] == 7

    with pytest.raises(WorkflowServiceError) as conflict:
        await _start(service, base_epoch=7, start_payload={"topic": "different"})
    assert conflict.value.code == "start_payload_conflict"
    assert conflict.value.details["existing_run_id"] == first["run_id"]


@pytest.mark.asyncio
async def test_code_delivery_ref_uses_code_epoch_when_delivery_targets_code_session(tmp_path):
    service, store = await _service(tmp_path)

    started = await _start(
        service,
        base_epoch=2,
        code_session_id="code-project",
        code_epoch=5,
        delivery_session_id="code-project",
    )

    assert (await store.get_session_ref(started["run_id"], "base"))["session_epoch"] == 2
    assert (await store.get_session_ref(started["run_id"], "code"))["session_epoch"] == 5
    assert (await store.get_session_ref(started["run_id"], "delivery"))["session_epoch"] == 5


@pytest.mark.asyncio
async def test_session_tombstone_cancels_bound_runs_and_marks_refs(tmp_path):
    service, store = await _service(tmp_path)
    started = await _start(service, base_epoch=3)

    cancelled = await service.cancel_runs_for_session("session-base")

    assert [item["run_id"] for item in cancelled] == [started["run_id"]]
    assert cancelled[0]["status"] == "cancelled"
    assert (await store.get_session_ref(started["run_id"], "base"))["deleted_at"] is not None


@pytest.mark.asyncio
async def test_delivery_expected_version_cas_writes_atomic_audit_event(tmp_path):
    service, _ = await _service(tmp_path)
    started = await _start(service)
    delivery = started["accepted_event"]["deliveries"][0]

    retried = await service.retry_delivery(
        delivery["delivery_id"], expected_version=0, reason="operator_retry"
    )
    assert retried["delivery"]["status"] == "pending"
    assert retried["delivery"]["version"] == 1
    assert retried["audit_event"]["event_type"] == "delivery.audit"
    assert retried["audit_event"]["payload"]["delivery"]["version"] == 1

    repeated = await service.retry_delivery(
        delivery["delivery_id"], expected_version=0, reason="operator_retry"
    )
    assert repeated["idempotent"] is True
    assert repeated["audit_event"]["event_id"] == retried["audit_event"]["event_id"]

    with pytest.raises(OutboxError) as stale:
        await service.discard_delivery(delivery["delivery_id"], expected_version=0)
    assert stale.value.code == "stale_delivery_version"
    assert stale.value.current_version == 1

    discarded = await service.discard_delivery(
        delivery["delivery_id"], expected_version=1, reason="session_deleted"
    )
    assert discarded["delivery"]["status"] == "discarded"
    assert discarded["delivery"]["version"] == 2

    events = await service.events_after_seq(started["run_id"], 0)
    assert [item["seq"] for item in events["events"]] == [1, 2, 3]
    assert events["latest_seq"] == 3


@pytest.mark.asyncio
async def test_run_detail_matches_frontend_aggregate_and_history_degrades_missing_ids(tmp_path):
    service, store = await _service(tmp_path)
    started = await _start(service)
    run_id = started["run_id"]
    row = await store.get_run(run_id)
    assert row is not None

    db = await store._connect()
    try:
        await db.execute(
            """INSERT INTO workflow_nodes(
                node_execution_id,run_id,node_id,base_checkpoint_id,invocation_key,
                latest_attempt,latest_status,updated_at
            ) VALUES('execution-1',?,'search','base','search:1',1,'succeeded',10)""",
            (run_id,),
        )
        await db.execute(
            """INSERT INTO workflow_node_attempts(
                node_execution_id,retry_attempt,status,started_at,ended_at
            ) VALUES('execution-1',1,'succeeded',9,10)"""
        )
        await db.commit()
    finally:
        await db.close()

    trace = TraceStore(store.path)
    await trace.start_run(
        trace_id=row["trace_id"],
        run_id=run_id,
        session_id="session-base",
        kind="workflow",
    )
    await trace.start_span(
        trace_id=row["trace_id"],
        run_id=run_id,
        span_id="span-1",
        name="search",
        kind=SpanKind.NODE,
    )
    await service.evaluation_store.record_human(
        HumanEvaluationRecord(
            trace_id=row["trace_id"],
            run_id=run_id,
            evaluator_name="reviewer",
            evaluator_version="1",
            verdict="pass",
            score=0.9,
            evaluation_id="evaluation-1",
        )
    )

    detail = await service.run_detail(run_id)
    assert {"run_id", "nodes", "edges", "spans", "checkpoints", "evaluations"} <= set(detail)
    assert detail["run_id"] == run_id
    assert detail["nodes"][0]["id"] == "search"
    assert detail["spans"][0]["span_id"] == "span-1"
    evaluation = detail["evaluations"][0]
    assert evaluation["evaluation_id"] == "evaluation-1"
    assert evaluation["evaluator_name"] == "reviewer"
    assert evaluation["evaluator_version"] == "1"
    assert evaluation["verdict"] == "pass"
    assert evaluation["score"] == 0.9

    accepted_id = started["accepted_event_id"]
    hydration = await service.hydrate_history_event_ids(
        run_id, [accepted_id, "missing-event"]
    )
    assert hydration["degraded"] is True
    assert hydration["missing_event_ids"] == ["missing-event"]
    assert hydration["mark_seen_after_reduce"] is True
    assert hydration["events"][0]["hydration"]["apply_before_seen"] is True


@pytest.mark.asyncio
async def test_session_history_hydration_enforces_delivery_target_and_epoch(tmp_path):
    service, _store = await _service(tmp_path)
    started = await _start(service, base_epoch=7)
    event_id = started["accepted_event_id"]

    hydrated = await service.hydrate_session_history_event_ids(
        "session-delivery", [event_id, "missing"], current_session_epoch=7
    )
    assert [event["event_id"] for event in hydrated["events"]] == [event_id]
    assert hydrated["missing_event_ids"] == ["missing"]
    assert hydrated["fenced_event_ids"] == []

    wrong_epoch = await service.hydrate_session_history_event_ids(
        "session-delivery", [event_id], current_session_epoch=8
    )
    assert wrong_epoch["events"] == []
    assert wrong_epoch["fenced_event_ids"] == [event_id]

    wrong_target = await service.hydrate_session_history_event_ids(
        "another-session", [event_id], current_session_epoch=7
    )
    assert wrong_target["events"] == []
    assert wrong_target["fenced_event_ids"] == [event_id]

    deleted = await service.hydrate_session_history_event_ids(
        "session-delivery",
        [event_id],
        current_session_epoch=7,
        session_deleted=True,
    )
    assert deleted["events"] == []
    assert deleted["fenced_event_ids"] == [event_id]
