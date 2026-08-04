from __future__ import annotations

from collections.abc import Mapping

import pytest

from deskpet.execution.contracts import (
    OutcomeStatus,
    RecoveryLease,
    RunContext,
    RunCreate,
    RunEvent,
    RunEventCandidate,
    fingerprint_json,
)
from deskpet.harness.drivers.workflow import WorkflowDriver
from deskpet.harness.profiles import ProfileRegistry, ProfileSpec
from deskpet.harness.ports import (
    CancelAcknowledgedCandidate,
    ChildAcceptedSignal,
    ChildTerminalSignal,
    DecisionSignal,
    DriverRecoveryDeferred,
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
        self.checkpointed_signals: set[tuple[str, str]] = set()

    async def launch_precreated(self, **kwargs):
        self.launches.append(kwargs)
        return {"accepted_event_id": self.accepted.event_id}

    async def recover_pending(self, *, only_run_ids=None, recovery_lease=None):
        self.recovers.append(only_run_ids)
        return list(only_run_ids or ())

    async def cancel_precreated(self, run_id, reason="user"):
        self.cancels.append((run_id, reason))
        return {"status": "cancel_requested"}

    async def resume_precreated(self, run_id, responses):
        self.resumes.append((run_id, responses))
        self.checkpointed_signals.update(
            (run_id, str(value["signal_id"]))
            for value in responses.values()
            if isinstance(value, Mapping) and value.get("signal_id")
        )
        return {"status": "accepted"}

    async def schedule_resume(self, run_id, responses):
        return await self.resume_precreated(run_id, responses)


class WorkflowResumeUow:
    def __init__(
        self,
        payload,
        *,
        run_id="workflow-run",
        resume_payload=None,
    ) -> None:
        self.payload = payload
        self.run_id = run_id
        self.resume_payload = dict(resume_payload or {})
        self.prepared: list[tuple[str, object]] = []
        self.delivered: set[str] = set()

    async def prepare_workflow_child_resume(self, signal_id, *, recovery_lease):
        self.prepared.append((signal_id, recovery_lease))
        return type("Prepared", (), {
            "request": type(
                "Request", (), {"nonce": "interrupt-1", "run_id": self.run_id}
            )(),
            "response": {"child_signal": self.payload},
        })()

    async def read_workflow_resume_payload(self, run_id):
        assert run_id == self.run_id
        return dict(self.resume_payload)


@pytest.mark.asyncio
async def test_prepare_recovery_defers_when_native_run_was_not_claimed() -> None:
    accepted = event("workflow-run", 1, "workflow.accepted", OutcomeStatus.ACCEPTED)

    class UnclaimedLauncher(Launcher):
        async def recover_pending(self, *, only_run_ids=None, recovery_lease=None):
            self.recovers.append(only_run_ids)
            return []

    launcher = UnclaimedLauncher(accepted)
    driver = WorkflowDriver(
        launcher,
        Events(accepted, accepted),
        profiles(),
        unit_of_work=WorkflowResumeUow({}),
    )
    lease = RecoveryLease("workflow-run", "kernel", 1, 100.0)

    with pytest.raises(DriverRecoveryDeferred, match="waiting"):
        await driver.prepare_recovery("workflow-run", lease)

    assert launcher.recovers == [{"workflow-run"}]


class CrashSafeLauncher(Launcher):
    def __init__(self, accepted, uow, fail):
        super().__init__(accepted)
        self.uow = uow
        self.fail = fail

    async def resume_precreated(self, run_id, responses):
        self.resumes.append((run_id, responses))
        payload = next(iter(responses.values()))
        if self.fail == "before":
            raise RuntimeError("crash before checkpoint commit")
        self.checkpointed_signals.add((run_id, payload["signal_id"]))
        self.uow.delivered.add(payload["signal_id"])
        if self.fail == "after":
            raise RuntimeError("crash after checkpoint commit")
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
    spec = RunCreate(
        run_id=run_id,
        idempotency_key=f"root:session-1:request-1:turn-1",
        context=context,
        payload_fingerprint=fingerprint_json({"text": "research"}),
        capability_fingerprint="c" * 64,
        driver_kind="workflow",
        profile_key="durable.default",
        persistence_level="durable",
    )
    return DriverStart(
        run_id=run_id,
        session_id="session-1",
        canonical_messages=({"role": "user", "content": "research"},),
        run_context=context,
        run_spec=spec,
        profile_key="durable.default",
        request_payload={"topic": "research"},
        capability_snapshot={"tools": ["read_file"]},
    )


async def collect(iterator):
    return [item async for item in iterator]


def profiles() -> ProfileRegistry:
    return ProfileRegistry((ProfileSpec(
        "durable.default", "fixture", "workflow",
        workflow_key="fixture.v1", workflow_name="fixture_workflow",
        workflow_version="v1", state_factory=lambda **kwargs: kwargs,
        context_factory=lambda **kwargs: kwargs,
    ),))


@pytest.mark.asyncio
async def test_workflow_driver_commits_accepted_before_following_terminal() -> None:
    accepted = event("workflow-run", 1, "workflow.accepted", OutcomeStatus.ACCEPTED)
    final = event("workflow-run", 2, "workflow.final", OutcomeStatus.SUCCEEDED)
    launcher = Launcher(accepted)
    driver = WorkflowDriver(
        launcher,
        Events(accepted, final),
        profiles(),
        poll_interval=0.001,
    )

    start = request()
    candidates = await collect(driver.start(start))

    assert candidates == [PersistedEventCandidate(accepted), PersistedEventCandidate(final)]
    launch = launcher.launches[0]
    assert launch["run_id"] == "workflow-run"
    assert launch["workflow_name"] == "fixture_workflow"
    assert launch["principal_id"] == "principal-1"
    assert launch["start_payload"] == {"topic": "research"}
    assert launch["execution_spec"] is start.run_spec


@pytest.mark.asyncio
async def test_workflow_driver_cancel_is_explicit_run_ack() -> None:
    accepted = event("workflow-run", 1, "workflow.accepted", OutcomeStatus.ACCEPTED)
    final = event("workflow-run", 2, "workflow.final", OutcomeStatus.CANCELLED)
    launcher = Launcher(accepted)
    driver = WorkflowDriver(
        launcher,
        Events(accepted, final),
        profiles(),
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
        profiles(),
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
    driver = WorkflowDriver(launcher, Events(accepted, final), profiles())

    with pytest.raises(ValueError, match="interrupt nonce"):
        await collect(
            driver.signal(DecisionSignal("workflow-run", "decision-1", {"approved": True}))
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        (
            ChildAcceptedSignal("workflow-run", "command-1", "child-1", "signal-1"),
            {
                "kind": "child_accepted", "signal_id": "signal-1",
                "command_id": "command-1", "child_run_id": "child-1",
            },
        ),
        (
            ChildTerminalSignal(
                "workflow-run", "command-1", "child-1", "completed",
                {"answer": 42}, "signal-1",
            ),
            {
                "kind": "child_terminal", "signal_id": "signal-1",
                "command_id": "command-1", "child_run_id": "child-1",
                "status": "completed", "value": {"answer": 42},
            },
        ),
    ],
)
async def test_workflow_child_signal_checkpoints_stable_response_then_acks(
    signal, expected
) -> None:
    accepted = event("workflow-run", 1, "workflow.accepted", OutcomeStatus.ACCEPTED)
    final = event("workflow-run", 2, "workflow.final", OutcomeStatus.SUCCEEDED)
    launcher = Launcher(accepted)
    uow = WorkflowResumeUow(expected)
    lease = RecoveryLease("workflow-run", "scheduler", 1, 100.0)
    driver = WorkflowDriver(
        launcher, Events(accepted, final), profiles(),
        unit_of_work=uow,
    )

    assert await collect(driver.signal(signal, recovery_lease=lease)) == []

    assert launcher.resumes == [("workflow-run", {"interrupt-1": expected})]
    assert uow.prepared == [("signal-1", lease)]


@pytest.mark.asyncio
async def test_workflow_child_signal_rejects_cross_run_decision_before_launcher() -> None:
    accepted = event("workflow-run", 1, "workflow.accepted", OutcomeStatus.ACCEPTED)
    payload = {
        "kind": "child_accepted", "signal_id": "signal-a",
        "command_id": "command-1", "child_run_id": "child-1",
    }
    launcher = Launcher(accepted)
    uow = WorkflowResumeUow(payload, run_id="workflow-run-a")
    driver = WorkflowDriver(launcher, Events(accepted, accepted), profiles(), unit_of_work=uow)

    with pytest.raises(ValueError, match="run binding mismatch"):
        await collect(driver.signal(
            ChildAcceptedSignal("workflow-run-b", "command-1", "child-1", "signal-a"),
            recovery_lease=RecoveryLease("workflow-run-b", "scheduler", 1, 100.0),
        ))

    assert launcher.resumes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("crash_point", ["before", "after"])
async def test_workflow_child_resume_replay_is_stable_across_checkpoint_crash(
    crash_point
) -> None:
    accepted = event("workflow-run", 1, "workflow.accepted", OutcomeStatus.ACCEPTED)
    payload = {
        "kind": "child_accepted", "signal_id": "stable-signal",
        "command_id": "command-1", "child_run_id": "child-1",
    }
    uow = WorkflowResumeUow(payload)
    launcher = CrashSafeLauncher(accepted, uow, crash_point)
    driver = WorkflowDriver(launcher, Events(accepted, accepted), profiles(), unit_of_work=uow)
    signal = ChildAcceptedSignal(
        "workflow-run", "command-1", "child-1", "stable-signal"
    )
    lease = RecoveryLease("workflow-run", "scheduler", 1, 100.0)

    with pytest.raises(RuntimeError, match=f"crash {crash_point} checkpoint"):
        await collect(driver.signal(signal, recovery_lease=lease))
    launcher.fail = None
    await collect(driver.signal(signal, recovery_lease=lease))

    assert launcher.checkpointed_signals == {("workflow-run", "stable-signal")}
    assert [next(iter(responses)) for _, responses in launcher.resumes] == [
        "interrupt-1", "interrupt-1",
    ]
    assert uow.delivered == {"stable-signal"}
