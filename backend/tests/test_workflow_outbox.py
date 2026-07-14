from __future__ import annotations

import pytest

from deskpet.workflows.launcher import WorkflowLauncher
from deskpet.workflows.outbox import WorkflowOutbox
from deskpet.workflows.service import WorkflowService
from deskpet.workflows.store import WorkflowRunStore


async def _run_store(tmp_path):
    store = WorkflowRunStore(tmp_path / "workflow.db")
    run_id, _ = await store.create_run(
        request_key="outbox-boundary",
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="deep_research",
        workflow_version="v2",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=2,
    )
    return store, run_id


@pytest.mark.asyncio
async def test_session_delivery_survives_websocket_failure_without_duplicate(tmp_path):
    store, run_id = await _run_store(tmp_path)
    outbox = WorkflowOutbox(store)
    event = await outbox.ensure_event(
        run_id=run_id,
        event_key="progress:search",
        event_type="workflow.progress",
        payload={"stage": "search", "status": "completed"},
        deliveries=(("session_message", "session"), ("websocket", "session")),
    )
    session_calls = 0
    websocket_calls = 0

    async def persist_session(envelope, delivery):
        nonlocal session_calls
        session_calls += 1

    async def send_websocket(envelope, delivery):
        nonlocal websocket_calls
        websocket_calls += 1
        if websocket_calls == 1:
            raise ConnectionError("socket disconnected after SessionDB commit")

    service = WorkflowService(
        store,
        runner=object(),
        delivery_handlers={
            "session_message": persist_session,
            "websocket": send_websocket,
        },
    )
    first = await service.deliver_event_once(event["event_id"])
    second = await service.deliver_event_once(event["event_id"])

    assert {row["status"] for row in first["deliveries"]} == {"delivered", "failed"}
    assert {row["status"] for row in second["deliveries"]} == {"delivered"}
    assert session_calls == 1
    assert websocket_calls == 2


@pytest.mark.asyncio
async def test_startup_recovers_orphaned_delivering_claim(tmp_path):
    store, run_id = await _run_store(tmp_path)
    outbox = WorkflowOutbox(store)
    event = await outbox.ensure_event(
        run_id=run_id,
        event_key="progress:fetch",
        event_type="workflow.progress",
        payload={"stage": "fetch", "status": "completed"},
        deliveries=(("session_message", "session"),),
    )
    delivery = event["deliveries"][0]
    claimed = await outbox.mutate_delivery(
        delivery["delivery_id"], action="begin", expected_version=delivery["version"]
    )
    assert claimed["delivery"]["status"] == "delivering"
    calls = 0

    async def persist(envelope, current):
        nonlocal calls
        calls += 1

    service = WorkflowService(
        store,
        runner=object(),
        delivery_handlers={"session_message": persist},
    )
    launcher = WorkflowLauncher(service)
    recovered = await launcher.recover_due_deliveries(recover_claimed=True)

    assert recovered == [event["event_id"]]
    assert calls == 1
    final = await outbox.get_delivery(delivery["delivery_id"])
    assert final is not None
    assert final["status"] == "delivered"
    assert final["attempts"] == 2
