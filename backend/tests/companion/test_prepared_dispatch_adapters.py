from __future__ import annotations

import hashlib
import time

import pytest

from deskpet.execution.dispatch import (
    PreparedToolDispatch,
    PreparedToolDispatchAdapter,
    PreparedToolDispatchIdentity,
    PreparedToolDispatchNotStarted,
    PreparedToolDispatchStartedAck,
    PreparedToolDispatchStartUnknown,
)
from deskpet.execution.ports import (
    CurrentExecutionScopeLease,
    CurrentExecutionScopeLeasePort,
)


class FakePreparedToolDispatch:
    def __init__(self, outcome: str = "started") -> None:
        self.identity = PreparedToolDispatchIdentity(
            run_id="run-1",
            call_id="call-1",
            effect_id="effect-1",
            tool_name="tool-1",
            adapter_id="fake",
            runtime_identity="fake-runtime",
        )
        self.request_hash = "a" * 64
        self.outcome = outcome
        self.physical_starts = 0
        self.completions = 0
        self.aborts = 0

    async def start(self, *, deadline: float):
        assert deadline > 0
        if self.outcome == "not_started":
            return PreparedToolDispatchNotStarted(self.identity, "adapter_rejected")
        if self.outcome == "unknown":
            return PreparedToolDispatchStartUnknown(self.identity, "ack_timeout")
        self.physical_starts += 1
        ack_ref = "tool-dispatch:run-1:call-1:effect-1"
        return PreparedToolDispatchStartedAck(
            self.identity,
            ack_ref,
            hashlib.sha256(ack_ref.encode()).hexdigest(),
            time.time(),
        )

    async def completion(self):
        self.completions += 1
        return {"ok": True}

    async def abort_unstarted(self):
        self.aborts += 1


class FakePreparedToolDispatchAdapter:
    adapter_id = "fake"
    adapter_version = "1"
    adapter_fingerprint = "f" * 64

    def __init__(self, dispatch):
        self.dispatch = dispatch
        self.prepares = 0

    async def begin_prepared(self, *, spec, prepared, context):
        self.prepares += 1
        assert spec["effect_id"] == "effect-1"
        assert prepared["authorization_id"] == "grant-1"
        assert context["scope_hash"] == "c" * 64
        return self.dispatch


@pytest.mark.asyncio
async def test_prepare_start_ack_and_completion_are_three_distinct_stages() -> None:
    dispatch = FakePreparedToolDispatch()
    adapter = FakePreparedToolDispatchAdapter(dispatch)

    prepared = await adapter.begin_prepared(
        spec={"effect_id": "effect-1"},
        prepared={"authorization_id": "grant-1"},
        context={"scope_hash": "c" * 64},
    )
    assert adapter.prepares == 1
    assert dispatch.physical_starts == 0
    assert dispatch.completions == 0

    ack = await prepared.start(deadline=time.time() + 5)
    assert isinstance(ack, PreparedToolDispatchStartedAck)
    assert dispatch.physical_starts == 1
    assert dispatch.completions == 0

    outcome = await prepared.completion()
    assert outcome == {"ok": True}
    assert dispatch.completions == 1
    assert isinstance(prepared, PreparedToolDispatch)
    assert isinstance(adapter, PreparedToolDispatchAdapter)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "outcome_type"),
    [
        ("not_started", PreparedToolDispatchNotStarted),
        ("unknown", PreparedToolDispatchStartUnknown),
    ],
)
async def test_start_failure_states_do_not_claim_completion(outcome, outcome_type):
    dispatch = FakePreparedToolDispatch(outcome)
    result = await dispatch.start(deadline=time.time() + 5)
    assert isinstance(result, outcome_type)
    assert dispatch.physical_starts == 0
    assert dispatch.completions == 0
    if isinstance(result, PreparedToolDispatchNotStarted):
        await dispatch.abort_unstarted()
        assert dispatch.aborts == 1


def test_scope_lease_ports_are_runtime_checkable_without_task7_adapter() -> None:
    class Lease:
        owner_key = "owner"
        profile_generation = 1
        binding_epoch = 2
        capability_hash = "a" * 64
        scope_hash = "b" * 64

        async def release(self):
            return None

    class Port:
        async def acquire_current_execution_scope(self, **kwargs):
            return Lease()

    assert isinstance(Lease(), CurrentExecutionScopeLease)
    assert isinstance(Port(), CurrentExecutionScopeLeasePort)
