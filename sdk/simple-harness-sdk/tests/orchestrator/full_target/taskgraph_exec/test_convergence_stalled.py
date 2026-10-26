"""Convergence watchdog with an injected clock and real durable event storage."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_orchestrator.contracts import Budget, Mission, MissionStatus
from agent_orchestrator.orchestrator.taskgraph_convergence import (
    ConvergenceAction,
    ConvergenceActionKind,
    ConvergenceObservation,
    TaskGraphConvergence,
)
from agent_orchestrator.storage.store import Store
from agent_orchestrator.storage.taskgraph_convergence import ConvergenceJob


@pytest.mark.parametrize("deadline", [3600, 12])
@pytest.mark.parametrize("foreign", [False, True])
def test_waiting_past_turn_deadline_emits_one_stalled_event(tmp_path, deadline, foreign):
    async def case():
        store = Store.open(tmp_path / "orch.db")
        store.insert_mission(
            Mission(
                id="m",
                goal="g",
                success_criteria=("ok",),
                stop_conditions=(),
                allowed_tools=(),
                risk_level="sandbox",
                budget=Budget(max_tokens=1000, max_attempts=2),
                tenant_id="t",
                status=MissionStatus.CREATED,
                created_at=1.0,
                version=1,
                idempotency_key="m",
            ),
            spec_hash="f" * 64,
        )
        job = ConvergenceJob(
            job_id="j",
            mission_id="m",
            decision_id="d",
            request_id="r",
            command_id="c",
            source_revision=1,
            candidate_hash="f" * 64,
            impact_hash="e" * 64,
            state="WAITING",
            row_version=2,
            created_at=1,
            updated_at=2,
            targets=(),
        )
        quiet = False
        action = ConvergenceAction(
            occurrence_id="o",
            original_identity="critic-subject",
            kind=ConvergenceActionKind.CANCEL_SERVICE_INTENT,
        )

        async def cancel(*args, **kwargs):
            pass

        def advance(*args, **kwargs):
            nonlocal job
            if kwargs["ready"]:
                job = replace(job, state="READY", row_version=job.row_version + 1)
            return job

        jobs = SimpleNamespace(store=store, get_job=lambda *_: job, advance_state=advance)
        runtime = SimpleNamespace(cancel_service_intent=cancel)
        convergence = TaskGraphConvergence(jobs, runtime=runtime, turn_deadline_seconds=deadline)
        # Isolate the watchdog from admission/graph readers; never manufacture quiescence.
        convergence._observe = lambda current: ConvergenceObservation(
            job_id=current.job_id,
            job_version=current.row_version,
            target_occurrences=(),
            actions=() if quiet or foreign else (action,),
            quiescent=quiet,
            unsettled_subjects=() if quiet else ("critic-subject",),
        )
        try:
            await convergence.advance_convergence("m", "j", now_ms=int((2 + deadline) * 1000))
            assert not [
                e for e in store.list_events("m") if e.type == "TaskGraphConvergenceStalled"
            ]
            for now in (int((3 + deadline) * 1000), int((63 + deadline) * 1000)):
                current = await convergence.advance_convergence("m", "j", now_ms=now)
                assert current.state == "WAITING" and current.row_version == 2
            events = [e for e in store.list_events("m") if e.type == "TaskGraphConvergenceStalled"]
            assert len(events) == 1
            assert events[0].payload["job_id"] == "j"
            assert events[0].payload["waiting_seconds"] == deadline + 1
            assert events[0].payload["unsettled_subjects"] == ["critic-subject"]
            quiet = True
            # The old fence can still advance as soon as the actual source is quiet.
            current = await convergence.advance_convergence(
                "m", "j", now_ms=int((64 + deadline) * 1000)
            )
            assert current.state == "READY"
        finally:
            store.close()

    asyncio.run(case())
