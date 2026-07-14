from __future__ import annotations

import asyncio
import time

import pytest

from deskpet.workflows.contracts import WorkflowContext, WorkflowRunStatus
from deskpet.workflows.launcher import WorkflowLauncher
from deskpet.workflows.progress import WorkflowProgressReporter
from deskpet.workflows.runner import WorkflowRunResult


class _Runner:
    def __init__(self):
        self.resume_context = None
        self.run_context = None
        self.persist_terminal = lambda run_id: None

    async def recover_expired(self):
        return []

    async def run(self, run_id, state, context):
        self.run_context = context
        await asyncio.sleep(0)
        self.persist_terminal(run_id)
        return WorkflowRunResult(run_id, WorkflowRunStatus.COMPLETED, state)

    async def resume(self, run_id, responses, context):
        self.resume_context = context
        self.persist_terminal(run_id)
        return WorkflowRunResult(
            run_id,
            WorkflowRunStatus.COMPLETED,
            {"values": {}, "responses": responses},
        )


class _Outbox:
    def __init__(self):
        self.events = []
        self.deliveries = []

    async def ensure_event(self, **kwargs):
        self.events.append(kwargs)
        return {"event_id": kwargs["event_key"]}

    async def list_deliveries(self, *, run_id=None, status=None, cursor=None, limit=100):
        assert cursor is None
        items = [
            delivery
            for delivery in self.deliveries
            if (run_id is None or delivery["run_id"] == run_id)
            and (status is None or delivery["status"] == status)
        ]
        return {"items": items[:limit], "next_cursor": None}


class _Service:
    def __init__(self):
        self.runner = _Runner()
        self.outbox = _Outbox()
        self.delivered = []
        self.run_store = self
        self.recovery_rows = []
        self.delivery_session_id = "delivery-session"
        self.runner.persist_terminal = self._persist_native_terminal

    def _persist_native_terminal(self, run_id):
        event_id = f"native-final:{run_id}"
        if any(item["event_id"] == event_id for item in self.outbox.deliveries):
            return
        self.outbox.deliveries.append(
            {
                "event_id": event_id,
                "run_id": run_id,
                "status": "pending",
                "next_attempt_at": None,
            }
        )

    async def start_workflow(self, **kwargs):
        return {"run_id": "run-1", "created": True, "accepted_event_id": "accepted"}

    async def get_run(self, run_id):
        return {
            "thread_id": run_id,
            "session_id": "session",
            "request_id": "request",
            "turn_id": "turn",
            "workflow_name": "test",
            "workflow_version": "v1",
        }

    async def list_runs(self, *, limit):
        return list(self.recovery_rows)

    async def next_retry_at(self, run_id):
        return None

    async def get_capability_snapshot(self, run_id):
        return {"_workflow_start": {"start_payload": {"topic": "recovered"}}}

    async def get_session_ref(self, run_id, session_kind):
        assert session_kind == "delivery"
        return {
            "run_id": run_id,
            "session_kind": session_kind,
            "session_id": self.delivery_session_id,
            "session_epoch": 3,
            "deleted_at": None,
        }

    async def deliver_event_once(self, event_id):
        unfinished = [
            item
            for item in self.outbox.deliveries
            if item["event_id"] == event_id and item["status"] in {"pending", "failed"}
        ]
        if event_id == "accepted" or unfinished:
            self.delivered.append(event_id)
        for item in unfinished:
            item["status"] = "delivered"


@pytest.mark.asyncio
async def test_launcher_delivers_native_terminal_without_recreating_event():
    service = _Service()
    launcher = WorkflowLauncher(service)  # type: ignore[arg-type]
    accepted = await launcher.launch(
        workflow_name="test",
        workflow_version="v1",
        session_id="session",
        request_id="request",
        turn_id="turn",
        start_payload={"topic": "x"},
        capability_snapshot={},
        state_factory=lambda **kwargs: kwargs,
        context_factory=WorkflowContext,
    )

    assert accepted["accepted"] is True
    await asyncio.gather(*tuple(launcher._tasks))
    assert service.outbox.events == []
    assert service.delivered == ["accepted", "native-final:run-1"]

    await launcher.dispatch_due_work()
    assert service.delivered == ["accepted", "native-final:run-1"]
    progress = service.runner.run_context.ports["progress"]
    assert isinstance(progress, WorkflowProgressReporter)


@pytest.mark.asyncio
async def test_launcher_recovers_retryable_run_from_persisted_start_payload():
    service = _Service()
    service.recovery_rows = [
        {
            "run_id": "run-1",
            "workflow_name": "test",
            "workflow_version": "v1",
            "status": "retryable",
            "session_id": "session",
        }
    ]
    launcher = WorkflowLauncher(service)  # type: ignore[arg-type]
    seen = []
    launcher.register_adapter(
        "test",
        "v1",
        state_factory=lambda **kwargs: seen.append(kwargs) or kwargs,
        context_factory=WorkflowContext,
    )

    assert await launcher.recover_pending() == ["run-1"]
    await asyncio.gather(*tuple(launcher._tasks))

    assert seen[0]["topic"] == "recovered"
    assert service.delivered == ["native-final:run-1"]
    assert service.outbox.events == []
    progress = service.runner.run_context.ports["progress"]
    assert progress.targets == (
        ("session_message", "delivery-session"),
        ("websocket", "delivery-session"),
    )


@pytest.mark.asyncio
async def test_launcher_resume_rebuilds_context_and_delivers_native_event():
    service = _Service()
    launcher = WorkflowLauncher(service)  # type: ignore[arg-type]

    async def context_factory(row, start_payload):
        return WorkflowContext(
            ports={"tool": {"session": row["session_id"], "topic": start_payload["topic"]}}
        )

    launcher.register_adapter(
        "test",
        "v1",
        state_factory=lambda **kwargs: kwargs,
        context_factory=context_factory,
    )
    result = await launcher.resume_run("run-1", {"interrupt-1": {"approved": True}})

    assert result.status is WorkflowRunStatus.COMPLETED
    assert service.runner.resume_context.ports["tool"] == {
        "session": "session",
        "topic": "recovered",
    }
    assert service.delivered == ["native-final:run-1"]
    assert service.outbox.events == []


@pytest.mark.asyncio
async def test_launcher_schedule_resume_acknowledges_before_graph_completion():
    service = _Service()
    launcher = WorkflowLauncher(service)  # type: ignore[arg-type]
    release = asyncio.Event()
    calls = 0

    async def slow_resume(run_id, responses):
        nonlocal calls
        calls += 1
        assert run_id == "run-1"
        assert responses == {"interrupt-1": "approve"}
        await release.wait()
        return WorkflowRunResult(run_id, WorkflowRunStatus.COMPLETED, {})

    launcher.resume_run = slow_resume  # type: ignore[method-assign]
    accepted = await asyncio.wait_for(
        launcher.schedule_resume("run-1", {"interrupt-1": "approve"}),
        timeout=0.1,
    )
    duplicate = await launcher.schedule_resume(
        "run-1", {"interrupt-1": "approve"}
    )

    assert accepted == {
        "accepted": True,
        "created": True,
        "completion_semantics": "accepted_async",
        "run_id": "run-1",
    }
    assert duplicate["created"] is False
    await asyncio.sleep(0)
    assert calls == 1
    assert "run-1" in launcher._scheduled_run_ids

    release.set()
    await asyncio.gather(*tuple(launcher._tasks))
    assert calls == 1
    assert "run-1" not in launcher._scheduled_run_ids


@pytest.mark.asyncio
async def test_launcher_does_not_publish_terminal_while_run_is_waiting():
    service = _Service()

    async def waiting_run(run_id, state, context):
        return WorkflowRunResult(run_id, WorkflowRunStatus.WAITING, state)

    service.runner.run = waiting_run
    launcher = WorkflowLauncher(service)  # type: ignore[arg-type]
    await launcher.launch(
        workflow_name="test",
        workflow_version="v1",
        session_id="session",
        request_id="request-waiting",
        turn_id="turn-waiting",
        start_payload={"topic": "wait"},
        capability_snapshot={},
        state_factory=lambda **kwargs: kwargs,
        context_factory=WorkflowContext,
    )
    await asyncio.gather(*tuple(launcher._tasks))

    assert service.outbox.events == []
    assert service.delivered == ["accepted"]


@pytest.mark.asyncio
async def test_launcher_recovers_pending_and_due_failed_deliveries_after_crash():
    service = _Service()
    now = time.time()
    service.outbox.deliveries.extend(
        [
            {
                "event_id": "pending-event",
                "run_id": "run-pending",
                "status": "pending",
                "next_attempt_at": None,
            },
            {
                "event_id": "due-failed-event",
                "run_id": "run-failed",
                "status": "failed",
                "next_attempt_at": now - 1,
            },
            {
                "event_id": "future-failed-event",
                "run_id": "run-future",
                "status": "failed",
                "next_attempt_at": now + 60,
            },
        ]
    )
    launcher = WorkflowLauncher(service)  # type: ignore[arg-type]

    recovered = await launcher.recover_due_deliveries(now=now)

    assert recovered == ["pending-event", "due-failed-event"]
    assert service.delivered == ["pending-event", "due-failed-event"]
    assert launcher._next_deadline == pytest.approx(now + 60)


@pytest.mark.asyncio
async def test_dispatcher_waits_for_wakeup_instead_of_busy_polling():
    service = _Service()
    launcher = WorkflowLauncher(service)  # type: ignore[arg-type]
    calls = 0

    async def dispatch_once():
        nonlocal calls
        calls += 1
        return []

    launcher.dispatch_due_work = dispatch_once  # type: ignore[method-assign]
    launcher.start_dispatcher(interval_seconds=30)
    await asyncio.sleep(0.05)
    assert calls == 1

    launcher.notify_dispatcher()
    await asyncio.sleep(0.05)
    assert calls == 2
    await launcher.shutdown()
