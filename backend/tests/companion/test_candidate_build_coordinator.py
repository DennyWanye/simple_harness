from __future__ import annotations

from dataclasses import replace

import pytest

from deskpet.companion.build_admission import (
    CandidateDraftReceiptExpectationV1,
    CandidateDraftReceiptV1,
    GrowthCandidateBuildPermitV1,
)
from deskpet.companion.candidate_build_coordinator import (
    CandidateBuildCoordinationError,
    CandidateBuildLaunchStateV1,
    CandidateBuildStatus,
    CompanionCandidateBuildCoordinator,
)


def _permit() -> GrowthCandidateBuildPermitV1:
    return GrowthCandidateBuildPermitV1.issue(
        build_id="build-1",
        owner_key="companion:owner-1",
        proposal_ref="proposal-1",
        proposal_hash="a" * 64,
        evidence_set_hash="b" * 64,
        source_fence_hash="c" * 64,
        target_fence_hash="d" * 64,
        lease_epoch=4,
    )


def _receipt() -> CandidateDraftReceiptV1:
    return CandidateDraftReceiptV1.issue_from_host(
        builder_launch_id="launch-1",
        child_run_id="child-1",
        child_start_hash="e" * 64,
        proposal_ref="proposal-1",
        proposal_hash="a" * 64,
        evidence_set_hash="b" * 64,
        target_fence_hash="d" * 64,
        validated_draft_hash="1" * 64,
        manifest_hash="2" * 64,
        archive_hash="3" * 64,
        file_set_hash="4" * 64,
        effect_topology_hash="5" * 64,
    )


def _expectation(receipt: CandidateDraftReceiptV1):
    return CandidateDraftReceiptExpectationV1(
        **{
            name: getattr(receipt, name)
            for name in CandidateDraftReceiptExpectationV1.__dataclass_fields__
        }
    )


class _PermitQuery:
    def __init__(self, permit):
        self.permit = permit
        self.calls = []

    def issue_for_claim(self, *, build_id, claim_owner, claim_epoch):
        self.calls.append((build_id, claim_owner, claim_epoch))
        assert build_id == self.permit.build_id
        assert claim_epoch == self.permit.lease_epoch
        return self.permit


class _States:
    def __init__(self, permit, *, trace):
        self.permit = permit
        self.trace = trace
        self.state = None
        self.crash_before_ack_child = False
        self.crash_after_ack_handoff = False
        self.crash_before_ack_built = False

    def _base(self, status, **kwargs):
        return CandidateBuildLaunchStateV1(
            build_id=self.permit.build_id,
            status=status,
            permit_id=self.permit.permit_id,
            permit_hash=self.permit.permit_hash,
            lease_epoch=self.permit.lease_epoch,
            builder_launch_id="launch-1",
            child_run_id="child-1",
            expected_child_start_hash="e" * 64,
            task_workspace="workspace-1",
            **kwargs,
        )

    def prepare_launch_pending(self, permit, *, task_workspace):
        self.trace.append("companion.launch_pending")
        assert permit == self.permit
        assert task_workspace == "workspace-1"
        if self.state is None:
            self.state = self._base(CandidateBuildStatus.LAUNCH_PENDING)
        return self.state

    def ack_child_precreated(
        self,
        permit,
        *,
        builder_launch_id,
        child_run_id,
        child_start_hash,
    ):
        self.trace.append("companion.ack_child_precreated")
        assert self.state.status is CandidateBuildStatus.LAUNCH_PENDING
        if self.crash_before_ack_child:
            self.crash_before_ack_child = False
            raise RuntimeError("crash_before_child_ack")
        self.state = replace(
            self.state, status=CandidateBuildStatus.CHILD_PRECREATED
        )
        return self.state

    def ack_running(self, permit, **kwargs):
        self.trace.append("companion.ack_running")
        assert self.state.status is CandidateBuildStatus.CHILD_PRECREATED
        self.state = replace(self.state, status=CandidateBuildStatus.RUNNING)
        return self.state

    def ack_handoff_pending(self, permit, *, expectation):
        self.trace.append("companion.ack_handoff_pending")
        assert self.state.status is CandidateBuildStatus.RUNNING
        self.state = replace(
            self.state,
            status=CandidateBuildStatus.HANDOFF_PENDING,
            draft_receipt_expectation=expectation,
        )
        if self.crash_after_ack_handoff:
            self.crash_after_ack_handoff = False
            raise RuntimeError("crash_after_handoff_ack")
        return self.state

    def ack_built(self, permit, *, expectation, handoff):
        self.trace.append("companion.ack_built")
        assert self.state.status is CandidateBuildStatus.HANDOFF_PENDING
        assert expectation == self.state.draft_receipt_expectation
        if self.crash_before_ack_built:
            self.crash_before_ack_built = False
            raise RuntimeError("crash_before_built_ack")
        self.state = replace(
            self.state,
            status=CandidateBuildStatus.BUILT,
            candidate_ref=str(handoff["candidate_ref"]),
            candidate_hash=str(handoff["candidate_hash"]),
        )
        return self.state

    def mark_stale(self, permit, *, reason_code):
        self.trace.append(f"companion.stale:{reason_code}")
        self.state = replace(
            self.state,
            status=CandidateBuildStatus.STALE,
            last_error=reason_code,
        )
        return self.state

    def mark_failed(self, permit, *, reason_code):
        self.trace.append(f"companion.failed:{reason_code}")
        self.state = replace(
            self.state,
            status=CandidateBuildStatus.FAILED,
            last_error=reason_code,
        )
        return self.state


class _Launches:
    def __init__(self, receipt, *, trace):
        self.receipt = receipt
        self.trace = trace
        self.child = None
        self.precreates = 0
        self.starts = 0

    def _identity(self, status):
        return {
            "builder_launch_id": "launch-1",
            "child_run_id": "child-1",
            "child_start_hash": "e" * 64,
            "status": status,
        }

    async def precreate_exact(self, permit, *, task_workspace):
        self.trace.append("execution.precreate")
        assert task_workspace == "workspace-1"
        if self.child is None:
            self.precreates += 1
            self.child = self._identity("precreated")
        return self.child

    async def start_precreated(self, permit):
        self.trace.append("execution.start")
        assert self.child is not None
        if self.child["status"] == "precreated":
            self.starts += 1
            self.child = self._identity("running")
        return self.child

    async def read_exact(self, permit):
        self.trace.append("execution.read")
        return self.child

    def complete(self):
        self.child = {
            **self._identity("succeeded"),
            "draft_receipt": {
                name: getattr(self.receipt, name)
                for name in CandidateDraftReceiptExpectationV1.__dataclass_fields__
            },
        }


class _Receipts:
    def __init__(self, receipt, *, trace):
        self.receipt = receipt
        self.trace = trace
        self.reads = 0

    def read_exact(self, expectation):
        self.trace.append("execution.read_receipt")
        self.reads += 1
        expectation.verify(self.receipt)
        return self.receipt


class _Output:
    def __init__(self, *, trace):
        self.trace = trace
        self.invocations = 0
        self.creations = 0
        self._result = None

    def handoff_candidate(self, *, permit, receipt):
        self.trace.append("companion.handoff")
        self.invocations += 1
        if self._result is None:
            self.creations += 1
            self._result = {
                "build_id": permit.build_id,
                "candidate_ref": "candidate-1",
                "candidate_hash": "6" * 64,
            }
        return self._result


def _stack():
    permit = _permit()
    receipt = _receipt()
    trace = []
    states = _States(permit, trace=trace)
    launches = _Launches(receipt, trace=trace)
    receipts = _Receipts(receipt, trace=trace)
    output = _Output(trace=trace)
    coordinator = CompanionCandidateBuildCoordinator(
        permits=_PermitQuery(permit),
        states=states,
        launches=launches,
        receipts=receipts,
        output=output,
    )
    return coordinator, permit, states, launches, receipts, output, trace


@pytest.mark.asyncio
async def test_launch_requires_companion_ack_before_child_start() -> None:
    coordinator, permit, states, launches, receipts, output, trace = _stack()

    state = await coordinator.drive_claim(
        build_id=permit.build_id,
        claim_owner="scheduler-1",
        claim_epoch=permit.lease_epoch,
        task_workspace="workspace-1",
    )

    assert state.status is CandidateBuildStatus.RUNNING
    assert trace.index("companion.ack_child_precreated") < trace.index(
        "execution.start"
    )
    assert launches.precreates == 1
    assert launches.starts == 1
    assert receipts.reads == output.invocations == 0

    launches.complete()
    state = await coordinator.reconcile(
        permit, task_workspace="workspace-1"
    )
    assert state.status is CandidateBuildStatus.BUILT
    assert receipts.reads == output.invocations == 1


@pytest.mark.asyncio
async def test_child_precreated_but_companion_not_acked_recovers_exact_child() -> None:
    coordinator, permit, states, launches, _receipts, _output, _trace = _stack()
    states.crash_before_ack_child = True

    with pytest.raises(RuntimeError, match="crash_before_child_ack"):
        await coordinator.reconcile(
            permit, task_workspace="workspace-1"
        )
    assert states.state.status is CandidateBuildStatus.LAUNCH_PENDING
    assert launches.precreates == 1
    assert launches.starts == 0

    state = await coordinator.reconcile(
        permit, task_workspace="workspace-1"
    )
    assert state.status is CandidateBuildStatus.RUNNING
    assert launches.precreates == 1
    assert launches.starts == 1


@pytest.mark.asyncio
async def test_handoff_pending_recovery_never_restarts_child() -> None:
    coordinator, permit, states, launches, receipts, output, _trace = _stack()
    await coordinator.reconcile(permit, task_workspace="workspace-1")
    launches.complete()
    states.crash_after_ack_handoff = True

    with pytest.raises(RuntimeError, match="crash_after_handoff_ack"):
        await coordinator.reconcile(
            permit, task_workspace="workspace-1"
        )
    assert states.state.status is CandidateBuildStatus.HANDOFF_PENDING
    starts_before = launches.starts

    state = await coordinator.reconcile(
        permit, task_workspace="workspace-1"
    )
    assert state.status is CandidateBuildStatus.BUILT
    assert launches.starts == starts_before
    assert receipts.reads == output.invocations == 1


@pytest.mark.asyncio
async def test_mismatched_precreated_child_marks_build_stale() -> None:
    coordinator, permit, states, launches, _receipts, _output, _trace = _stack()
    launches.child = {
        **launches._identity("precreated"),
        "child_start_hash": "7" * 64,
    }

    with pytest.raises(
        CandidateBuildCoordinationError,
        match="reserved_builder_child_identity_mismatch",
    ):
        await coordinator.reconcile(
            permit, task_workspace="workspace-1"
        )

    assert states.state.status is CandidateBuildStatus.STALE
    assert launches.starts == 0


@pytest.mark.asyncio
async def test_failed_child_terminates_without_receipt_or_handoff() -> None:
    coordinator, permit, states, launches, receipts, output, _trace = _stack()
    await coordinator.reconcile(permit, task_workspace="workspace-1")
    launches.child = launches._identity("failed")

    state = await coordinator.reconcile(
        permit, task_workspace="workspace-1"
    )

    assert state.status is CandidateBuildStatus.FAILED
    assert state.last_error == "builder_child_failed"
    assert receipts.reads == output.invocations == 0


@pytest.mark.asyncio
async def test_handoff_completed_but_built_ack_missing_replays_same_candidate() -> None:
    coordinator, permit, states, launches, receipts, output, _trace = _stack()
    await coordinator.reconcile(permit, task_workspace="workspace-1")
    launches.complete()
    states.crash_before_ack_built = True

    with pytest.raises(RuntimeError, match="crash_before_built_ack"):
        await coordinator.reconcile(
            permit, task_workspace="workspace-1"
        )
    assert states.state.status is CandidateBuildStatus.HANDOFF_PENDING
    assert output.creations == 1
    starts_before = launches.starts

    state = await coordinator.reconcile(
        permit, task_workspace="workspace-1"
    )
    assert state.status is CandidateBuildStatus.BUILT
    assert launches.starts == starts_before
    assert receipts.reads == output.invocations == 2
    assert output.creations == 1
