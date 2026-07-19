from __future__ import annotations

import pytest

from deskpet.memory.session_db import SessionDB
from deskpet.workflows.delivery import DeliveryAttemptResultV1
from deskpet.workflows.outbox import OutboxError, WorkflowOutbox
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store import WorkflowRunStore


async def _run(store: WorkflowRunStore, key: str, session_id: str, epoch: int) -> str:
    run_id, _ = await store.create_run(
        request_key=key,
        session_id=session_id,
        request_id=f"request-{key}",
        turn_id=f"turn-{key}",
        workflow_name="deep_research",
        workflow_version="v6",
        manifest_hash=f"manifest-hash-{key}",
        implementation_hash="implementation-v6",
        capability_hash="capability-v6",
        capability_snapshot={},
        state_schema_version=6,
    )
    await store.bind_session_refs(run_id, (("delivery", session_id, epoch),))
    return run_id


async def _v6_event(
    outbox: WorkflowOutbox,
    store: WorkflowRunStore,
    *,
    run_id: str,
    session_id: str,
    channels: tuple[str, ...] = ("session_message",),
    required: frozenset[str] = frozenset({"session_message"}),
):
    manifest_ref = f"manifest-{run_id}"
    event = await outbox.ensure_event(
        run_id=run_id,
        event_key="terminal:assistant",
        event_type="workflow.final_assistant",
        payload={"text": f"answer:{run_id}", "manifest_ref": manifest_ref},
        deliveries=tuple((channel, session_id) for channel in channels),
    )
    db = await store._connect()
    try:
        for channel in channels:
            await db.execute(
                """UPDATE workflow_deliveries
                SET intent_id=?,manifest_ref=?,required_durable=?
                WHERE event_id=? AND channel=?""",
                (
                    f"intent-{run_id}",
                    manifest_ref,
                    1 if channel in required else 0,
                    event["event_id"],
                    channel,
                ),
            )
        await db.commit()
    finally:
        await db.close()
    return event, manifest_ref


def _session_handler(session_db: SessionDB, store: WorkflowRunStore):
    async def deliver(event, delivery):
        ref = await store.get_session_ref(str(event["run_id"]), "delivery")
        if ref is None or str(ref["session_id"]) != str(delivery["target_id"]):
            return DeliveryAttemptResultV1.discarded_fenced("session_binding_mismatch")
        message_id = await session_db.append_message_if_epoch(
            str(delivery["target_id"]),
            "assistant",
            str(event["payload"]["text"]),
            expected_epoch=int(ref["session_epoch"]),
            workflow_event_id=str(event["event_id"]),
            projection_kind="final_assistant",
            context_visibility="conversation",
        )
        if message_id is None:
            return DeliveryAttemptResultV1.discarded_fenced("session_epoch_mismatch")
        return DeliveryAttemptResultV1.delivered()

    return deliver


@pytest.mark.asyncio
async def test_fenced_handler_result_never_marks_delivered_or_rebinds_and_new_run_succeeds(
    tmp_path,
):
    store = WorkflowRunStore(tmp_path / "workflow.db")
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    await session_db.ensure_session("session-a")
    old_run = await _run(store, "old", "session-a", 0)
    outbox = WorkflowOutbox(store)
    old_event, old_manifest = await _v6_event(
        outbox, store, run_id=old_run, session_id="session-a"
    )
    service = WorkflowService(
        store,
        runner=object(),
        outbox=outbox,
        delivery_handlers={"session_message": _session_handler(session_db, store)},
    )

    assert await session_db.tombstone_session("session-a", deleted_at=10.0) == 1
    first = await service.deliver_event_once(old_event["event_id"])
    old_row = first["deliveries"][0]
    assert old_row["status"] == "discarded"
    assert old_row["last_error"] == "fenced:session_epoch_mismatch"
    assert first["delivery_aggregate"]["status"] == "fenced"

    # Recreating the same session advances its live binding, but the old durable
    # identity remains terminal and is never rebound or projected.
    await session_db.ensure_session("session-a")
    replay = await service.deliver_event_once(old_event["event_id"])
    assert replay["deliveries"][0]["delivery_id"] == old_row["delivery_id"]
    assert replay["deliveries"][0]["status"] == "discarded"
    assert await session_db.get_messages("session-a") == []
    with pytest.raises(OutboxError, match="frozen outbox policy"):
        await service.retry_delivery(
            old_row["delivery_id"], expected_version=old_row["version"]
        )
    assert (await service.delivery_aggregate(old_run, manifest_ref=old_manifest))["status"] == "fenced"

    state = await session_db.get_session_delivery_state("session-a")
    new_run = await _run(store, "new", "session-a", int(state["epoch"]))
    new_event, _ = await _v6_event(
        outbox, store, run_id=new_run, session_id="session-a"
    )
    delivered = await service.deliver_event_once(new_event["event_id"])
    assert delivered["deliveries"][0]["status"] == "delivered"
    assert delivered["delivery_aggregate"]["status"] == "delivered"
    messages = await session_db.get_messages("session-a")
    assert [row["workflow_event_id"] for row in messages] == [new_event["event_id"]]


@pytest.mark.asyncio
async def test_retryable_session_failure_retries_once_then_deduplicates_without_ws_success(
    tmp_path,
):
    now = [100.0]
    store = WorkflowRunStore(tmp_path / "workflow.db")
    session_db = SessionDB(tmp_path / "state.db")
    await session_db.initialize()
    await session_db.ensure_session("session-b")
    run_id = await _run(store, "retry", "session-b", 0)
    outbox = WorkflowOutbox(store, clock=lambda: now[0])
    event, _ = await _v6_event(
        outbox,
        store,
        run_id=run_id,
        session_id="session-b",
        channels=("session_message", "websocket"),
    )
    durable = _session_handler(session_db, store)
    calls = 0

    async def transient_then_persist(envelope, delivery):
        nonlocal calls
        calls += 1
        if calls == 1:
            return DeliveryAttemptResultV1.retryable_failure(
                "session_db_temporary_error"
            )
        return await durable(envelope, delivery)

    # Deliberately omit a websocket handler: realtime transport is optional and
    # cannot contribute to the required durable aggregate.
    service = WorkflowService(
        store,
        runner=object(),
        outbox=outbox,
        delivery_handlers={"session_message": transient_then_persist},
    )
    first = await service.deliver_event_once(event["event_id"])
    by_channel = {row["channel"]: row for row in first["deliveries"]}
    assert by_channel["session_message"]["status"] == "failed"
    assert by_channel["session_message"]["attempts"] == 1
    assert by_channel["session_message"]["next_attempt_at"] == 101.0
    assert by_channel["websocket"]["status"] == "pending"
    assert first["delivery_aggregate"]["status"] == "retrying"

    # A non-due call cannot bypass the deterministic outbox wait.
    unchanged = await service.deliver_event_once(event["event_id"])
    assert unchanged["delivery_aggregate"]["status"] == "retrying"
    assert calls == 1

    now[0] = 101.0
    second = await service.deliver_event_once(event["event_id"])
    assert second["delivery_aggregate"]["status"] == "delivered"
    assert calls == 2
    replay = await service.deliver_event_once(event["event_id"])
    assert replay["delivery_aggregate"]["status"] == "delivered"
    assert calls == 2
    messages = await session_db.get_messages("session-b")
    assert len(messages) == 1
    assert messages[0]["workflow_event_id"] == event["event_id"]


@pytest.mark.asyncio
async def test_required_delivery_aggregate_is_query_time_receipt_reduction(tmp_path):
    now = [20.0]
    store = WorkflowRunStore(tmp_path / "workflow.db")
    run_id = await _run(store, "aggregate", "session-c", 0)
    outbox = WorkflowOutbox(store, clock=lambda: now[0])
    event, manifest_ref = await _v6_event(
        outbox,
        store,
        run_id=run_id,
        session_id="session-c",
        channels=("session_message", "artifact", "websocket"),
        required=frozenset({"session_message", "artifact"}),
    )
    queued = await outbox.delivery_aggregate(run_id, manifest_ref=manifest_ref)
    assert queued is not None
    assert set(queued) == {
        "schema_version",
        "run_id",
        "manifest_ref",
        "status",
        "required_total",
        "pending",
        "delivering",
        "delivered",
        "retrying",
        "fenced",
        "failed",
        "updated_at",
    }
    assert queued["status"] == "queued"
    assert queued["required_total"] == 2 and queued["pending"] == 2

    rows = {
        row["channel"]: row
        for row in await outbox.list_event_deliveries(event["event_id"])
    }
    begun = await outbox.mutate_delivery(
        rows["session_message"]["delivery_id"],
        action="begin",
        expected_version=rows["session_message"]["version"],
    )
    delivering = await outbox.delivery_aggregate(run_id, manifest_ref=manifest_ref)
    assert delivering is not None and delivering["status"] == "delivering"
    await outbox.mutate_delivery(
        begun["delivery"]["delivery_id"],
        action="failed",
        expected_version=begun["delivery"]["version"],
        reason="session_db_temporary_error",
    )
    retrying = await outbox.delivery_aggregate(run_id, manifest_ref=manifest_ref)
    assert retrying is not None and retrying["status"] == "retrying"

    artifact_begun = await outbox.mutate_delivery(
        rows["artifact"]["delivery_id"],
        action="begin",
        expected_version=rows["artifact"]["version"],
    )
    await outbox.mutate_delivery(
        rows["artifact"]["delivery_id"],
        action="discard",
        expected_version=artifact_begun["delivery"]["version"],
        reason="fenced:session_epoch_mismatch",
    )
    fenced = await outbox.delivery_aggregate(run_id, manifest_ref=manifest_ref)
    assert fenced is not None and fenced["status"] == "fenced"
    assert fenced["retrying"] == 1 and fenced["fenced"] == 1


@pytest.mark.asyncio
async def test_legacy_shaped_discard_return_is_contract_error_not_delivered(tmp_path):
    store = WorkflowRunStore(tmp_path / "workflow.db")
    run_id = await _run(store, "bad-handler", "session-d", 0)
    outbox = WorkflowOutbox(store, clock=lambda: 50.0)
    event, _ = await _v6_event(
        outbox, store, run_id=run_id, session_id="session-d"
    )

    async def invalid_legacy_shape(_event, _delivery):
        return {"discarded": True}

    service = WorkflowService(
        store,
        runner=object(),
        outbox=outbox,
        delivery_handlers={"session_message": invalid_legacy_shape},
    )
    result = await service.deliver_event_once(event["event_id"])
    row = result["deliveries"][0]
    assert row["status"] == "failed"
    assert row["last_error"] == "handler_contract_error"
    assert result["delivery_aggregate"]["status"] == "retrying"


@pytest.mark.asyncio
async def test_nonrequired_websocket_typed_best_effort_result_is_delivered(tmp_path):
    store = WorkflowRunStore(tmp_path / "workflow.db")
    run_id = await _run(store, "websocket-best-effort", "session-ws", 0)
    outbox = WorkflowOutbox(store, clock=lambda: 50.0)
    event, _ = await _v6_event(
        outbox,
        store,
        run_id=run_id,
        session_id="session-ws",
        channels=("session_message", "websocket"),
        required=frozenset({"session_message"}),
    )

    async def websocket_best_effort(_event, _delivery):
        return DeliveryAttemptResultV1.delivered("websocket_best_effort")

    service = WorkflowService(
        store,
        runner=object(),
        outbox=outbox,
        delivery_handlers={
            "session_message": websocket_best_effort,
            "websocket": websocket_best_effort,
        },
    )
    result = await service.deliver_event_once(event["event_id"])
    row = next(item for item in result["deliveries"] if item["channel"] == "websocket")
    assert row["channel"] == "websocket"
    assert row["required_durable"] == 0
    assert row["status"] == "delivered"
    assert row["last_error"] is None
