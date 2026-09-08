# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1

"""Incident F: a typed recall whose authority advances mid-flight must re-collect.

The race is reproduced against the **real installed Memory SDK**: the authority
is advanced through the SDK's own ``recall_authority_events``/head ledger at the
exact point the SDK leaves the collection phase, so ``execute_typed_recall``
raises its real ``RECALL_AUTHORITY_STALE`` fence.

Proven here: a re-collection succeeds, the budget is bounded, every attempt gets
its own payload-free Host audit row, the re-collections never mint a second
durable recall (identical replay identity), and an exhausted budget settles as a
``context_route`` tool rejection — never as a Run failure.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
import pytest_asyncio

from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.memory.recall_authority import (
    MAX_RECALL_AUTHORITY_RECOLLECTS,
    RecallAuthorityStale,
    RecallContextUseAuthorityStale,
    execute_typed_recall_recollecting,
    is_recall_authority_stale,
)


async def _advance_authority(backend, principal_id: str, *, source_ref: str, now: float) -> int:
    """Advance the real SDK recall authority (real event row + head CAS)."""

    async with backend._write_lock:
        await backend._db.execute("BEGIN IMMEDIATE")
        try:
            epoch, _policy = await backend._advance_recall_authority_unlocked(
                principal_id,
                event_kind="cognitive_memory_changed",
                source_ref=source_ref,
                now=now,
            )
            await backend._db.execute("COMMIT")
        except BaseException:
            await backend._db.execute("ROLLBACK")
            raise
    return epoch


class _Racer:
    """Advance the authority after the SDK finished collecting candidates."""

    def __init__(self, backend, principal_id: str, *, budget: int, now: float) -> None:
        self._backend = backend
        self._principal_id = principal_id
        self._original = backend._collect_typed_recall_candidates
        self.budget = budget
        self.collections = 0
        self.bumps = 0
        self._now = now
        backend._collect_typed_recall_candidates = self

    async def __call__(self, **kwargs):
        candidates = await self._original(**kwargs)
        self.collections += 1
        if self.bumps < self.budget:
            self.bumps += 1
            await _advance_authority(
                self._backend, self._principal_id,
                source_ref=f"incident-f-{self.bumps}", now=self._now,
            )
        return candidates

    def restore(self) -> None:
        self._backend._collect_typed_recall_candidates = self._original


def _attempts(db_path):
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(
            "SELECT caller,state,observation_status,started_at FROM memory_call_attempts "
            "ORDER BY started_at,attempt_ref"
        )]


def _findings(db_path):
    with sqlite3.connect(db_path) as db:
        return db.execute("SELECT reason FROM memory_call_findings").fetchall()


@pytest_asyncio.fixture
async def runtime(tmp_path):
    value = HumanMemoryV7Runtime(tmp_path / "memory.db")
    try:
        yield value
    finally:
        await value.close()


@pytest.mark.asyncio
async def test_sdk_fence_is_reachable_and_recognized(runtime, tmp_path):
    """The reproduction really trips the SDK's own stale fence."""

    manager = await runtime.manager()
    backend = manager.backend
    racer = _Racer(backend, runtime.principal().actor_id, budget=1, now=20.0)
    try:
        with pytest.raises(Exception) as raised:
            await runtime.operation_audit.execute_typed_recall(
                manager,
                principal=runtime.principal(),
                context=_plan_inputs(runtime)[0],
                plan=_plan_inputs(runtime)[1],
                now=20.0,
                caller="foreground_recall",
            )
    finally:
        racer.restore()
    assert is_recall_authority_stale(raised.value), raised.value
    assert str(raised.value) == "RECALL_AUTHORITY_STALE"
    assert racer.bumps == 1


def _plan_inputs(runtime, *, run_id="incident-f-fence", turn_ordinal=1, now=20.0):
    """A Host typed recall context/plan pair, built exactly like ``typed_recall``."""

    import hashlib
    import uuid

    from simple_harness.runtime import (
        DeliveryRecipient, DisclosureContext, DisclosureGeneration, DisclosurePurpose,
        DisclosureReasonCode, DisclosureSource, DisclosureTrust, EvidenceRef,
        IntendedAudience, LongTermMemoryType, RecallBudget, RecallContext, RecallPlan,
        RecallReasonCode, RecallRetrievalMode, RecallSelectorDomain,
    )

    from deskpet.memory.recall_selection import HOST_DEFAULT_MEMORY_TYPES

    subject = runtime.principal().actor_id
    disclosure = DisclosureContext(
        run_id, subject, DeliveryRecipient.USER_SELF, subject, IntendedAudience.USER_SELF,
        DisclosurePurpose.TASK_EXECUTION, DisclosureSource.AUTHENTICATED_HOST,
        DisclosureTrust.TRUSTED_AUTHORITY, DisclosureGeneration.CURRENT,
        "host:validated-control-channel:v1", (DisclosureReasonCode.MINIMUM_NECESSARY,),
    )
    query = "秋分资料整理的校对流程"
    evidence_ref = EvidenceRef(
        f"chat-turn:{run_id}:{turn_ordinal}",
        hashlib.sha256(f"{run_id}:{turn_ordinal}:{query}".encode()).hexdigest(), 1,
    )
    types = tuple(LongTermMemoryType(name) for name in HOST_DEFAULT_MEMORY_TYPES)
    context = RecallContext(
        run_id, subject, f"turn-{turn_ordinal}", turn_ordinal, now + 60.0, query, None,
        types, False, (RecallSelectorDomain.MEMORY_TYPE,),
        (RecallRetrievalMode.FULL_TEXT, RecallRetrievalMode.VECTOR),
        (), (), None, None, (), (), (), (), disclosure, (evidence_ref,),
        RecallBudget(8, 16_384, 2_048, 1_000),
    )
    plan = RecallPlan(
        str(uuid.uuid5(uuid.NAMESPACE_URL,
                       f"simple-harness:recall-plan:{run_id}:{turn_ordinal}")),
        context.run_id, context.subject, context.context_hash, context.context_revision,
        context.query, types, context.short_horizon_allowed,
        (RecallSelectorDomain.MEMORY_TYPE,), context.allowed_retrieval_modes, (),
        context.allowed_entity_constraints, context.earliest_occurred_at,
        context.latest_occurred_at, context.event_constraint_refs, (), (),
        context.disclosure_context, context.evidence_refs, context.budget,
        f"context-route:{run_id}:{turn_ordinal}", (RecallReasonCode.USER_FACT_DEPENDENCY,),
    )
    return context, plan


@pytest.mark.asyncio
async def test_recollection_succeeds_and_keeps_one_durable_recall(runtime, tmp_path):
    manager = await runtime.manager()
    racer = _Racer(manager.backend, runtime.principal().actor_id, budget=1, now=20.0)
    try:
        lanes = await runtime.typed_recall(
            query="秋分资料整理的校对流程", run_id="incident-f-retry",
            turn_ordinal=1, now=20.0,
        )
    finally:
        racer.restore()

    assert racer.collections == 2, "the recall must be collected again, not reused"
    assert racer.bumps == 1
    result = lanes.execution.result
    assert result.result_id and result.result_hash

    # One durable SDK recall: the identical request replays the same identity,
    # so the re-collection cannot have minted a second result/decision.
    replay = await runtime.typed_recall(
        query="秋分资料整理的校对流程", run_id="incident-f-retry", turn_ordinal=1, now=20.0,
    )
    assert replay.execution.result.result_id == result.result_id
    assert replay.execution.result.result_hash == result.result_hash
    assert replay.execution.decision.decision_id == lanes.execution.decision.decision_id

    rows = _attempts(tmp_path / "operation-audit.db")
    foreground = [row for row in rows if row["caller"] == "foreground_recall"]
    assert [row["state"] for row in foreground] == ["raised", "returned", "returned"]
    assert all(row["observation_status"] != "pending" for row in foreground)
    assert _findings(tmp_path / "operation-audit.db")  # the stale attempt owns a finding


@pytest.mark.asyncio
async def test_recollection_is_bounded_and_becomes_a_stable_rejection(runtime, tmp_path):
    manager = await runtime.manager()
    racer = _Racer(manager.backend, runtime.principal().actor_id, budget=99, now=20.0)
    try:
        with pytest.raises(RecallAuthorityStale) as raised:
            await runtime.typed_recall(
                query="秋分资料整理的校对流程", run_id="incident-f-exhausted",
                turn_ordinal=1, now=20.0,
            )
    finally:
        racer.restore()

    assert raised.value.code == "recall_authority_stale"
    assert raised.value.caller == "foreground_recall"
    assert raised.value.attempts == MAX_RECALL_AUTHORITY_RECOLLECTS + 1
    assert racer.collections == MAX_RECALL_AUTHORITY_RECOLLECTS + 1
    rows = [row for row in _attempts(tmp_path / "operation-audit.db")
            if row["caller"] == "foreground_recall"]
    assert len(rows) == MAX_RECALL_AUTHORITY_RECOLLECTS + 1
    assert {row["state"] for row in rows} == {"raised"}


@pytest.mark.asyncio
async def test_recollection_bound_is_configurable_and_validated(runtime):
    manager = await runtime.manager()
    principal = runtime.principal()
    context, plan = _plan_inputs(runtime, run_id="incident-f-bound")
    racer = _Racer(manager.backend, principal.actor_id, budget=99, now=20.0)
    try:
        with pytest.raises(RecallAuthorityStale) as raised:
            await execute_typed_recall_recollecting(
                runtime.operation_audit, manager, principal=principal, context=context,
                plan=plan, now=20.0, caller="foreground_recall", max_recollects=0,
            )
    finally:
        racer.restore()
    assert raised.value.attempts == 1
    assert racer.collections == 1
    with pytest.raises(ValueError, match="recall_authority_recollect_bound_invalid"):
        await execute_typed_recall_recollecting(
            runtime.operation_audit, manager, principal=principal, context=context,
            plan=plan, now=20.0, caller="foreground_recall", max_recollects=-1,
        )


@pytest.mark.asyncio
async def test_cancellation_is_never_recollected(runtime):
    manager = await runtime.manager()
    principal = runtime.principal()
    context, plan = _plan_inputs(runtime, run_id="incident-f-cancel")
    calls = []

    class _Journal:
        async def execute_typed_recall(self, _manager, **kwargs):
            calls.append(kwargs["caller"])
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await execute_typed_recall_recollecting(
            _Journal(), manager, principal=principal, context=context, plan=plan,
            now=20.0, caller="foreground_recall",
        )
    assert calls == ["foreground_recall"]


@pytest.mark.asyncio
async def test_exhausted_stale_is_a_tool_rejection_never_a_run_failure(runtime, tmp_path):
    """The real route service must settle an exhausted stale recall as a rejection."""

    from deskpet.memory.schema import initialize_human_memory_program_state_db
    from tests.sdk_adapters.test_context_route_tool import RUN, _service

    state_db = tmp_path / "state.db"
    await initialize_human_memory_program_state_db(state_db)
    tool = _service(state_db)
    tool._recall_executor = runtime.typed_recall
    manager = await runtime.manager()
    racer = _Racer(manager.backend, runtime.principal().actor_id, budget=99, now=20.0)
    try:
        result = await tool.handle_context_route(
            {"route": "memory_standalone", "query": "秋分资料整理的校对流程",
             "memory_types": ["semantic", "procedure"]}
        )
    finally:
        racer.restore()

    # A tool result, not an exception: the Run driver never sees the fence.
    assert "context_route_receipt" not in result
    assert result["error"]["code"] == "context_route_adjudication_failed"
    assert "RECALL_AUTHORITY_STALE" not in str(result)
    with sqlite3.connect(state_db) as db:
        rows = db.execute(
            "SELECT verdict FROM context_route_tool_invocations WHERE sdk_run_id=?", (RUN,),
        ).fetchall()
    assert [row[0] for row in rows] == ["rejected"]
    assert racer.collections == MAX_RECALL_AUTHORITY_RECOLLECTS + 1


@pytest.mark.parametrize(
    "raised,expected",
    [("RECALL_AUTHORITY_STALE", RecallContextUseAuthorityStale),
     ("typed_recall_context_use_binding_invalid", None)],
)
@pytest.mark.asyncio
async def test_context_use_fence_gets_a_stable_host_code(runtime, raised, expected):
    """The provider-use fence is renamed, never retried and never swallowed."""

    from simple_harness_memory import MemoryValidationError

    calls = []

    class _Manager:
        async def authorize_recall_context_use(self, **kwargs):
            calls.append(kwargs)
            raise MemoryValidationError(raised)

    class _Memory:
        semantic_clock = staticmethod(lambda: 20.0)

        async def manager(self):
            return _Manager()

        @staticmethod
        def principal():
            return runtime.principal()

    from deskpet.sdk_adapters.typed_context_use import ProductTypedContextUseAuthority

    authority = object.__new__(ProductTypedContextUseAuthority)
    authority._memory = _Memory()
    authority.clock = _Memory.semantic_clock
    authority.subject = runtime.principal().actor_id
    request = SimpleNamespace(subject=authority.subject, run_id="incident-f", turn_id="turn-15")

    with pytest.raises(expected or MemoryValidationError) as error:
        await authority.authorize_recall_context_use(request)
    if expected is not None:
        assert error.value.code == "recall_context_use_authority_stale"
        assert type(error.value.__cause__) is MemoryValidationError
    else:
        assert str(error.value) == raised
    assert len(calls) == 1, "the use fence must never be retried"
