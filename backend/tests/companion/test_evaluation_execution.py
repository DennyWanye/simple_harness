from __future__ import annotations

from typing import Any

import pytest

from deskpet.capabilities.process_job import ManagedProcessStartReceipt
from deskpet.companion.authority import RevocationBarrier
from deskpet.companion.contracts import (
    CompanionStateError,
    EvaluationExecutionPermit,
    OwnerRef,
)
from deskpet.companion.evaluation_execution import (
    EvaluationExecutionError,
    FenceAwareEvaluationCaseExecutor,
    FrozenEvaluationCaseV1,
)


def _permit(*, mode: str = "safe_auto") -> EvaluationExecutionPermit:
    return EvaluationExecutionPermit(
        permit_id="permit-1",
        evaluation_id="evaluation-1",
        mode=mode,
        candidate_id="candidate-1",
        package_hash="package-1",
        manifest_hash="manifest-1",
        archive_hash="archive-1",
        suite_hash="suite-1",
        runner_policy_hash="runner-policy-1",
        issued_revocation_epoch=0,
        preflight_ref="preflight-1",
        preflight_hash="preflight-hash-1",
        risk_ref="risk-1",
        risk_hash="risk-hash-1",
        permit_hash="permit-hash-1",
        reason_code="evaluation_permitted",
        authorization_id=(
            "evaluation-authorization-1"
            if mode == "user_authorized"
            else None
        ),
    )


def _frozen(*, mode: str = "safe_auto") -> FrozenEvaluationCaseV1:
    return FrozenEvaluationCaseV1(
        evaluation_id="evaluation-1",
        case_id="case-1",
        variant="candidate",
        candidate_id="candidate-1",
        package_hash="package-1",
        manifest_hash="manifest-1",
        archive_hash="archive-1",
        suite_hash="suite-1",
        runner_policy_hash="runner-policy-1",
        input_hash="input-hash-1",
        adapter_id="evaluation-case-adapter",
        adapter_version="1",
        adapter_fingerprint="evaluation-adapter-build-1",
        permit=_permit(mode=mode),
        execution_kind="managed_process",
        start_envelope={"command": ["python", "case.py"]},
        completion_envelope={"probe": "case-result"},
    )


class _Store:
    def __init__(
        self,
        *,
        authorization_ready: bool = True,
        recovery_only: bool = False,
    ) -> None:
        self.authorization_ready = authorization_ready
        self.recovery_only = recovery_only
        self.trace: list[str] = []
        self.launch = None
        self.start_ack = None
        self.completion = None
        self.abort = None
        self.fence: dict[str, Any] = {
            "evaluation_id": "evaluation-1",
            "evaluation_status": "running",
            "candidate_id": "candidate-1",
            "candidate_status": "evaluating",
            "package_hash": "package-1",
            "manifest_hash": "manifest-1",
            "archive_hash": "archive-1",
            "suite_hash": "suite-1",
            "runner_policy_hash": "runner-policy-1",
            "permit_id": "permit-1",
            "permit_mode": "safe_auto",
            "permit_hash": "permit-hash-1",
            "permit_status": "claimed",
            "issued_revocation_epoch": 0,
            "preflight_ref": "preflight-1",
            "preflight_hash": "preflight-hash-1",
            "risk_ref": "risk-1",
            "risk_hash": "risk-hash-1",
            "risk_level": "low",
            "authorization_id": None,
            "authorization_consumed": False,
            "policy_verified": True,
            "case_id": "case-1",
            "variant": "candidate",
            "case_status": "leased",
            "claim_owner": "worker-1",
            "claim_epoch": 1,
            "attempt_ordinal": 1,
            "recovery_only": recovery_only,
            "input_hash": "input-hash-1",
            "input_content_state": "live",
            "adapter_id": "evaluation-case-adapter",
            "adapter_version": "1",
            "adapter_fingerprint": "evaluation-adapter-build-1",
        }

    def claim_evaluation_case(self, _owner, **kwargs):
        self.trace.append("claim")
        if not self.authorization_ready:
            raise CompanionStateError("evaluation_case_fence_closed")
        self.fence["claim_owner"] = kwargs["claim_owner"]
        return {
            "claim_epoch": 1,
            "attempt": 1,
            "recovery_only": int(self.recovery_only),
        }

    def get_evaluation_execution_fence(self, _owner, **_kwargs):
        self.trace.append("fence")
        return dict(self.fence)

    def create_evaluation_case_launch(
        self, _owner, launch, *, claim_owner
    ):
        self.trace.append("launch")
        assert claim_owner == self.fence["claim_owner"]
        self.launch = launch
        return {"launch_id": launch.launch_id, "status": "claimed"}

    def record_evaluation_case_start_ack(self, _owner, **kwargs):
        self.trace.append("start_ack")
        self.start_ack = kwargs["ack"]
        return {"status": "started"}

    def commit_evaluation_case_completion(self, _owner, **kwargs):
        self.trace.append("commit")
        self.completion = kwargs["completion"]
        self.fence["case_status"] = "committed"
        return {"status": "committed"}

    def abort_evaluation_case_execution(self, _owner, **kwargs):
        self.trace.append("abort")
        self.abort = kwargs
        self.fence["case_status"] = (
            "inconclusive"
            if kwargs["survivor_count"] == 0
            else "cleanup_required"
        )
        return {"status": self.fence["case_status"]}

    def settle_evaluation_case_launch(self, _owner, **kwargs):
        self.trace.append(f"settle:{kwargs['status']}")
        return {"status": kwargs["status"]}

    def get_evaluation_case_recovery_state(self, _owner, **_kwargs):
        self.trace.append("recovery")
        return {
            "launch_id": "old-launch-1",
            "launch_status": "started",
        }


class _Port:
    def __init__(
        self,
        *,
        outcome: str = "started",
        barrier: RevocationBarrier | None = None,
        revoke_during_completion: bool = False,
    ) -> None:
        self.outcome = outcome
        self.barrier = barrier
        self.revoke_during_completion = revoke_during_completion
        self.trace: list[str] = []
        self.start_calls = 0
        self.alive = False
        self.receipt: ManagedProcessStartReceipt | None = None

    async def start_and_ack(
        self, *, runtime_instance_id, start_envelope, authorization_nonce
    ):
        self.trace.append("start")
        self.start_calls += 1
        assert self.barrier is None or self.barrier._readers == 1
        assert start_envelope["command"][-1] == "case.py"
        assert authorization_nonce
        self.alive = self.outcome in {"started", "unknown"}
        self.receipt = ManagedProcessStartReceipt(
            outcome=self.outcome,
            runtime_instance_id=runtime_instance_id,
            adapter_identity="managed-process-test-v1",
            job_identity="job-1" if self.outcome == "started" else None,
            pid=3210 if self.outcome == "started" else None,
            process_create_time=123.5
            if self.outcome == "started"
            else None,
            command_line_hash="command-hash-1"
            if self.outcome == "started"
            else None,
            reason_code=f"test_{self.outcome}",
        )
        return self.receipt

    async def await_health(self, receipt, health_envelope):
        self.trace.append("completion")
        assert receipt is self.receipt
        assert health_envelope == {"probe": "case-result"}
        assert self.barrier is None or self.barrier._readers == 0
        if self.revoke_during_completion:
            assert self.barrier is not None
            async with self.barrier.exclusive():
                pass
        return {
            "status": "completed",
            "outcome_ref": "evaluation-outcome:1",
            "outcome_hash": "evaluation-outcome-hash-1",
            "assertions": {"passed": True},
            "judge_result": {"score": 1},
            "usage": {"tokens": 3},
            "reason_code": "case_completed",
        }

    async def abort_exact(self, receipt):
        self.trace.append("abort_exact")
        assert receipt is self.receipt
        self.alive = False

    async def recover_exact(self, *, runtime_instance_id, start_identity):
        self.trace.append("recover_exact")
        assert runtime_instance_id
        del start_identity
        return self.receipt if self.alive else None

    async def abort_unacknowledged(
        self, *, runtime_instance_id, authorization_nonce
    ):
        self.trace.append("abort_unacknowledged")
        assert runtime_instance_id and authorization_nonce
        self.alive = False


def _executor(store, port, barrier):
    return FenceAwareEvaluationCaseExecutor(
        store=store,
        barrier=barrier,
        managed_process_port=port,
        in_process_port=port,
    )


@pytest.mark.asyncio
async def test_no_process_is_started_before_evaluation_authorization() -> None:
    store = _Store(authorization_ready=False)
    port = _Port()
    with pytest.raises(
        CompanionStateError, match="evaluation_case_fence_closed"
    ):
        await _executor(store, port, RevocationBarrier()).execute_case(
            OwnerRef("alice", 1),
            _frozen(mode="user_authorized"),
            claim_owner="worker-1",
        )

    assert port.start_calls == 0
    assert store.trace == ["claim"]


@pytest.mark.asyncio
async def test_unconsumed_exact_code_authorization_fails_inside_fence() -> None:
    barrier = RevocationBarrier()
    store = _Store()
    store.fence.update(
        {
            "permit_mode": "user_authorized",
            "risk_level": "unknown",
            "authorization_id": "evaluation-authorization-1",
            "authorization_consumed": False,
        }
    )
    port = _Port(barrier=barrier)
    with pytest.raises(
        EvaluationExecutionError, match="evaluation_execution_fence_stale"
    ):
        await _executor(store, port, barrier).execute_case(
            OwnerRef("alice", 1),
            _frozen(mode="user_authorized"),
            claim_owner="worker-1",
        )

    assert port.start_calls == 0
    assert store.trace == ["claim", "fence"]


@pytest.mark.asyncio
async def test_missing_store_fence_api_fails_before_claim_or_process() -> None:
    class _IncompleteStore:
        def claim_evaluation_case(self, *_args, **_kwargs):
            raise AssertionError("claim must not be attempted")

    port = _Port()
    with pytest.raises(
        EvaluationExecutionError,
        match="evaluation_store_execution_api_unavailable",
    ):
        await _executor(
            _IncompleteStore(), port, RevocationBarrier()
        ).execute_case(
            OwnerRef("alice", 1),
            _frozen(),
            claim_owner="worker-1",
        )
    assert port.start_calls == 0


@pytest.mark.asyncio
async def test_start_completion_and_result_commit_use_two_short_fences() -> None:
    barrier = RevocationBarrier()
    store = _Store()
    port = _Port(barrier=barrier)
    outcome = await _executor(store, port, barrier).execute_case(
        OwnerRef("alice", 1), _frozen(), claim_owner="worker-1"
    )

    assert outcome.status == "committed"
    assert outcome.result_hash == store.completion.result_hash
    assert store.trace == [
        "claim",
        "fence",
        "launch",
        "start_ack",
        "fence",
        "commit",
    ]
    assert port.trace == [
        "start",
        "completion",
        "abort_exact",
        "recover_exact",
    ]
    assert store.start_ack.pid == 3210
    assert store.completion.survivor_count == 0


@pytest.mark.asyncio
async def test_not_started_has_zero_survivor_receipt_and_is_retryable() -> None:
    barrier = RevocationBarrier()
    store = _Store()
    port = _Port(outcome="not_started", barrier=barrier)
    outcome = await _executor(store, port, barrier).execute_case(
        OwnerRef("alice", 1), _frozen(), claim_owner="worker-1"
    )

    assert outcome.status == "not_started"
    assert port.trace == [
        "start",
        "abort_unacknowledged",
        "recover_exact",
    ]
    assert store.trace[-1] == "settle:not_started"
    assert "start_ack" not in store.trace
    assert "commit" not in store.trace


@pytest.mark.asyncio
async def test_unknown_start_is_aborted_and_never_resent() -> None:
    barrier = RevocationBarrier()
    store = _Store()
    port = _Port(outcome="unknown", barrier=barrier)
    outcome = await _executor(store, port, barrier).execute_case(
        OwnerRef("alice", 1), _frozen(), claim_owner="worker-1"
    )

    assert outcome.status == "inconclusive"
    assert port.start_calls == 1
    assert port.trace == [
        "start",
        "abort_unacknowledged",
        "recover_exact",
    ]
    assert store.abort["expected_launch_status"] == "claimed"
    assert store.abort["survivor_count"] == 0


@pytest.mark.asyncio
async def test_revocation_during_completion_aborts_exact_child() -> None:
    barrier = RevocationBarrier()
    store = _Store()
    port = _Port(
        barrier=barrier,
        revoke_during_completion=True,
    )
    outcome = await _executor(store, port, barrier).execute_case(
        OwnerRef("alice", 1), _frozen(), claim_owner="worker-1"
    )

    assert outcome.status == "inconclusive"
    assert outcome.reason_code == "evaluation_execution_fence_stale"
    assert port.start_calls == 1
    assert port.trace[-2:] == ["abort_exact", "recover_exact"]
    assert "commit" not in store.trace
    assert store.abort["expected_launch_status"] == "started"
    assert store.abort["survivor_count"] == 0


@pytest.mark.asyncio
async def test_redacted_input_fails_before_durable_or_physical_launch() -> None:
    barrier = RevocationBarrier()
    store = _Store()
    store.fence["input_content_state"] = "redacted"
    port = _Port(barrier=barrier)
    with pytest.raises(
        EvaluationExecutionError, match="evaluation_input_redacted"
    ):
        await _executor(store, port, barrier).execute_case(
            OwnerRef("alice", 1), _frozen(), claim_owner="worker-1"
        )

    assert port.start_calls == 0
    assert "launch" not in store.trace


@pytest.mark.asyncio
async def test_stolen_lease_is_recovery_only_and_never_creates_launch() -> None:
    barrier = RevocationBarrier()
    store = _Store(recovery_only=True)
    port = _Port(barrier=barrier)
    outcome = await _executor(store, port, barrier).execute_case(
        OwnerRef("alice", 1), _frozen(), claim_owner="worker-1"
    )

    assert outcome.status == "recovery_only"
    assert outcome.launch_id == "old-launch-1"
    assert port.start_calls == 0
    assert store.trace == ["claim", "recovery"]
