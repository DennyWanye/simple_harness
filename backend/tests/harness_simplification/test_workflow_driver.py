from __future__ import annotations

from collections.abc import Mapping

import pytest

from deskpet.execution.contracts import (
    OutcomeStatus,
    RunContext,
    RunEvent,
    RunEventCandidate,
)
from deskpet.harness.drivers.workflow import (
    LauncherWorkflowSignalResumer,
    WorkflowDriver,
    WorkflowProfile,
)
from deskpet.harness.ports import (
    CancelAcknowledgedCandidate,
    DecisionSignal,
    DriverStart,
    PersistedEventCandidate,
)


def event(run_id: str, seq: int, kind: str, status: str) -> RunEvent:
    return RunEvent(
        event_id=f"event-{seq}",
        run_id=run_id,
        root_run_id=run_id,
        session_id="session-1",
        durable_seq=seq,
        live_cursor=None,
        candidate=RunEventCandidate(
            event_key=f"key-{seq}",
            kind=kind,
            status=status,
            driver_kind="workflow",
            payload={"kind": "final" if kind.endswith("final") else "accepted"},
        ),
        created_at=float(seq),
    )


class Events:
    def __init__(self, accepted: RunEvent, final: RunEvent) -> None:
        self.accepted = accepted
        self.final = final

    async def get_event(self, event_id: str) -> RunEvent:
        assert event_id == self.accepted.event_id
        return self.accepted

    async def list_events(self, run_id: str, *, after_durable_seq: int = 0):
        assert run_id == self.accepted.run_id
        return (self.final,) if after_durable_seq < 2 else ()


class Launcher:
    def __init__(self, accepted: RunEvent) -> None:
        self.accepted = accepted
        self.launches: list[Mapping[str, object]] = []
        self.cancels: list[tuple[str, str]] = []
        self.recovers: list[set[str] | None] = []
        self.resumes: list[tuple[str, Mapping[str, object]]] = []

    async def launch_precreated(self, **kwargs):
        self.launches.append(kwargs)
        return {"accepted_event_id": self.accepted.event_id}

    async def recover_pending(self, *, only_run_ids=None):
        self.recovers.append(only_run_ids)
        return list(only_run_ids or ())

    async def cancel_precreated(self, run_id, reason="user"):
        self.cancels.append((run_id, reason))
        return {"status": "cancel_requested"}

    async def resume_run(self, run_id, responses):
        self.resumes.append((run_id, responses))
        return {"status": "accepted"}


def request(run_id: str = "workflow-run") -> DriverStart:
    context = RunContext(
        session_id="session-1",
        root_run_id=run_id,
        parent_run_id=None,
        request_id="request-1",
        turn_id="turn-1",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="trace-1",
        principal_id="principal-1",
        auth_epoch=3,
    )
    return DriverStart(
        run_id=run_id,
        session_id="session-1",
        canonical_messages=({"role": "user", "content": "research"},),
        run_context=context,
        profile_key="durable.default",
        request_payload={"topic": "research"},
        capability_snapshot={"tools": ["read_file"]},
    )


async def collect(iterator):
    return [item async for item in iterator]


@pytest.mark.asyncio
async def test_workflow_driver_commits_accepted_before_following_terminal() -> None:
    accepted = event("workflow-run", 1, "workflow.accepted", OutcomeStatus.ACCEPTED)
    final = event("workflow-run", 2, "workflow.final", OutcomeStatus.SUCCEEDED)
    launcher = Launcher(accepted)
    driver = WorkflowDriver(
        launcher,
        Events(accepted, final),
        [
            WorkflowProfile(
                "durable.default",
                "fixture_workflow",
                "v1",
                lambda **kwargs: kwargs,
                lambda **kwargs: kwargs,
            )
        ],
        poll_interval=0.001,
    )

    candidates = await collect(driver.start(request()))

    assert candidates == [PersistedEventCandidate(accepted), PersistedEventCandidate(final)]
    launch = launcher.launches[0]
    assert launch["run_id"] == "workflow-run"
    assert launch["workflow_name"] == "fixture_workflow"
    assert launch["principal_id"] == "principal-1"
    assert launch["start_payload"] == {"topic": "research"}


@pytest.mark.asyncio
async def test_workflow_driver_cancel_is_explicit_run_ack() -> None:
    accepted = event("workflow-run", 1, "workflow.accepted", OutcomeStatus.ACCEPTED)
    final = event("workflow-run", 2, "workflow.final", OutcomeStatus.CANCELLED)
    launcher = Launcher(accepted)
    driver = WorkflowDriver(
        launcher,
        Events(accepted, final),
        [
            WorkflowProfile(
                "durable.default",
                "fixture_workflow",
                "v1",
                lambda **kwargs: kwargs,
                lambda **kwargs: kwargs,
            )
        ],
    )
    assert await collect(driver.cancel("workflow-run", "user_stop")) == [
        CancelAcknowledgedCandidate("workflow-run", "user_stop")
    ]
    assert launcher.cancels == [("workflow-run", "user_stop")]


@pytest.mark.asyncio
async def test_workflow_signal_resumer_uses_resolved_interrupt_nonce() -> None:
    accepted = event("workflow-run", 1, "workflow.accepted", OutcomeStatus.ACCEPTED)
    final = event("workflow-run", 2, "workflow.final", OutcomeStatus.SUCCEEDED)
    launcher = Launcher(accepted)
    driver = WorkflowDriver(
        launcher,
        Events(accepted, final),
        [
            WorkflowProfile(
                "durable.default",
                "fixture_workflow",
                "v1",
                lambda **kwargs: kwargs,
                lambda **kwargs: kwargs,
            )
        ],
        signal_resumer=LauncherWorkflowSignalResumer(launcher),
    )

    candidates = await collect(
        driver.signal(
            DecisionSignal(
                "workflow-run",
                "decision-1",
                {"approved": True, "decision_status": "allowed"},
                nonce="interrupt-1",
                version=1,
            )
        )
    )

    assert candidates == []
    assert launcher.resumes == [
        (
            "workflow-run",
            {
                "interrupt-1": {
                    "approved": True,
                    "decision_status": "allowed",
                }
            },
        )
    ]


@pytest.mark.asyncio
async def test_workflow_signal_resumer_fails_closed_without_nonce() -> None:
    accepted = event("workflow-run", 1, "workflow.accepted", OutcomeStatus.ACCEPTED)
    final = event("workflow-run", 2, "workflow.final", OutcomeStatus.SUCCEEDED)
    launcher = Launcher(accepted)
    resumer = LauncherWorkflowSignalResumer(launcher)

    with pytest.raises(ValueError, match="interrupt nonce"):
        await resumer.resume(
            DecisionSignal("workflow-run", "decision-1", {"approved": True})
        )
