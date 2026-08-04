from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from deskpet.companion.action_decisions import (
    CompanionActionDecisionRejected,
    CompanionActionDecisionService,
    RecoveredCompanionDecision,
    SqliteExecutionDecisionRecovery,
    TrustedCompanionControlIdentity,
)
from deskpet.companion.contracts import OwnerRef
from deskpet.companion.identity_gate import FrozenOwnerIdentity, IdentityReadyGate
from deskpet.execution.contracts import ActorContext, DecisionSignal, RunRef
from deskpet.harness.adapters.venues import KernelRunClient


class FakeExecution:
    def __init__(self, record: RecoveredCompanionDecision) -> None:
        self.record = record

    async def recover_companion_decision(self, decision_id):
        assert decision_id == self.record.decision_id
        return self.record


class FakeKernelClient:
    def __init__(self) -> None:
        self.calls = []

    async def signal_decision(self, ref, actor, signal):
        self.calls.append((ref, actor, signal))
        return {"accepted": True}


class FakeCursor:
    def __init__(self, row) -> None:
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row) -> None:
        self.row = row

    def execute(self, statement, parameters):
        return FakeCursor(self.row)


class FakeCompanionStore:
    def __init__(self, row) -> None:
        self.row = row

    @contextmanager
    def read(self):
        yield FakeConnection(self.row)


def _recovered(**changes) -> RecoveredCompanionDecision:
    values = {
        "decision_id": "decision-1",
        "run_id": "run-1",
        "session_id": "session-original",
        "root_run_id": "root-original",
        "request_id": "request-original",
        "principal_id": "principal-original",
        "auth_epoch": 9,
        "status": "open",
        "nonce": "nonce-original",
        "decision_version": 4,
        "expires_at": 999.0,
        "domain_kind": "tool",
        "domain_id": "tool-domain",
        "call_id": "call-original",
        "effect_id": "effect-original",
        "tool_name": "filesystem_write",
        "args_hash": "a" * 64,
        "capability_hash": "b" * 64,
        "scope_hash": "c" * 64,
    }
    values.update(changes)
    return RecoveredCompanionDecision(**values)


_DEFAULT_BINDING = object()


def _service(
    *, binding=_DEFAULT_BINDING, recovered=None, wall_time=lambda: 100.0
):
    owner = OwnerRef("profile-a", 2)
    frozen = FrozenOwnerIdentity(owner, "owner-key", 8)
    gate = IdentityReadyGate()
    gate.bind(frozen)
    kernel = FakeKernelClient()
    service = CompanionActionDecisionService(
        identity_gate=gate,
        companion_store=FakeCompanionStore(
            (
                {
                    "request_id": "request-original",
                    "root_run_id": "root-original",
                }
                if binding is _DEFAULT_BINDING
                else binding
            )
        ),
        execution=FakeExecution(recovered or _recovered()),
        kernel_client=kernel,
        wall_time=wall_time,
    )
    return service, gate, kernel, TrustedCompanionControlIdentity.from_frozen(frozen)


@pytest.mark.asyncio
async def test_decision_service_recovers_all_fences_from_durable_state() -> None:
    service, _, kernel, control = _service()

    result = await service.decide(
        decision_id="decision-1", allow=True, control_identity=control
    )

    assert result == {"accepted": True}
    ref, actor, signal = kernel.calls[0]
    assert (ref.run_id, ref.expected_session_id) == ("run-1", "session-original")
    assert (
        actor.principal_id,
        actor.auth_epoch,
        actor.root_run_id,
    ) == ("principal-original", 9, "root-original")
    assert signal.nonce == "nonce-original"
    assert signal.expected_version == 4
    assert signal.call_id == "call-original"
    assert signal.effect_id == "effect-original"
    assert signal.args_hash == "a" * 64
    assert signal.capability_hash == "b" * 64
    assert signal.scope_hash == "c" * 64
    assert signal.response == {
        "allow": True,
        "provenance": "user_explicit_companion_action",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ("owner", "control_owner_mismatch"),
        ("generation", "control_owner_mismatch"),
        ("binding_epoch", "control_binding_stale"),
        ("logout", "identity_logged_out"),
    ],
)
async def test_decision_service_rejects_identity_a_b_generation_logout_and_stale(
    mutation, code
) -> None:
    service, gate, kernel, control = _service()
    if mutation == "owner":
        control = TrustedCompanionControlIdentity(
            OwnerRef("profile-b", 2), "owner-key", 8
        )
    elif mutation == "generation":
        control = TrustedCompanionControlIdentity(
            OwnerRef("profile-a", 3), "owner-key", 8
        )
    elif mutation == "binding_epoch":
        control = TrustedCompanionControlIdentity(control.owner, "owner-key", 7)
    else:
        gate.unbind(expected_binding_epoch=8)

    with pytest.raises(CompanionActionDecisionRejected) as caught:
        await service.decide(
            decision_id="decision-1", allow=True, control_identity=control
        )
    assert caught.value.code == code
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_decision_service_rejects_stale_run_binding_and_expiry() -> None:
    service, _, kernel, control = _service(binding=None)
    with pytest.raises(CompanionActionDecisionRejected) as caught:
        await service.decide(
            decision_id="decision-1", allow=False, control_identity=control
        )
    assert caught.value.code == "companion_run_binding_stale"

    service, _, kernel, control = _service(
        recovered=_recovered(expires_at=99.0)
    )
    with pytest.raises(CompanionActionDecisionRejected) as caught:
        await service.decide(
            decision_id="decision-1", allow=False, control_identity=control
        )
    assert caught.value.code == "decision_expired"
    assert kernel.calls == []


@pytest.mark.asyncio
async def test_sqlite_recovery_reads_decision_and_original_actor(tmp_path) -> None:
    path = tmp_path / "execution.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE execution_runs(
          run_id TEXT PRIMARY KEY,session_id TEXT,root_run_id TEXT,request_id TEXT,
          principal_id TEXT,auth_epoch INTEGER
        );
        CREATE TABLE execution_decisions(
          decision_id TEXT PRIMARY KEY,run_id TEXT,nonce TEXT,status TEXT,
          decision_version INTEGER,expires_at REAL,domain_kind TEXT,domain_id TEXT,
          call_id TEXT,effect_id TEXT,tool_name TEXT,args_hash TEXT,
          capability_hash TEXT,scope_hash TEXT
        );
        """
    )
    db.execute(
        "INSERT INTO execution_runs VALUES (?,?,?,?,?,?)",
        ("run-1", "session-1", "root-1", "request-1", "principal-1", 11),
    )
    db.execute(
        "INSERT INTO execution_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "decision-1",
            "run-1",
            "nonce-1",
            "open",
            2,
            500.0,
            None,
            None,
            "call-1",
            "effect-1",
            "tool-1",
            "a" * 64,
            "b" * 64,
            "c" * 64,
        ),
    )
    db.commit()
    db.close()

    recovered = await SqliteExecutionDecisionRecovery(
        path
    ).recover_companion_decision("decision-1")

    assert recovered.session_id == "session-1"
    assert recovered.principal_id == "principal-1"
    assert recovered.auth_epoch == 11
    assert recovered.call_id == "call-1"


@pytest.mark.asyncio
async def test_kernel_client_typed_decision_interface_preserves_recovered_fence() -> None:
    class Kernel:
        def __init__(self):
            self.calls = []

        async def signal(self, ref, actor, signal):
            self.calls.append((ref, actor, signal))
            return "accepted"

    kernel = Kernel()
    client = KernelRunClient(kernel)
    ref = RunRef("run-1", "session-1")
    actor = ActorContext("principal-1", "session-1", 3, "root-1")
    signal = DecisionSignal(
        decision_id="decision-1",
        run_id="run-1",
        expected_session_id="session-1",
        nonce="nonce-1",
        expected_version=6,
        allow=False,
        response_schema_version=1,
        response={"allow": False},
    )

    assert await client.signal_decision(ref, actor, signal) == "accepted"
    _, _, driver_signal = kernel.calls[0]
    assert driver_signal.kind == "decision"
    assert driver_signal.decision_id == "decision-1"
    assert driver_signal.nonce == "nonce-1"
    assert driver_signal.version == 6
