from __future__ import annotations

import aiosqlite
import pytest

from deskpet.execution.contracts import (
    PersistenceLevel,
    RunContext,
    RunCreate,
    RunStatus,
    fingerprint_json,
)
from deskpet.harness.reconciler import HarnessReconciler
from deskpet.workflows.store.execution_uow import SqliteExecutionUnitOfWork


class Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def _root(uow: SqliteExecutionUnitOfWork, run_id: str = "budget-root"):
    context = RunContext(
        session_id="budget-session",
        root_run_id=run_id,
        parent_run_id=None,
        request_id="budget-request",
        turn_id="budget-turn",
        venue="text",
        workspace={},
        capability_hash="c" * 64,
        provider_plan={},
        trace_id="budget-trace",
        principal_id="budget-principal",
        auth_epoch=1,
    )
    return (
        await uow.create(
            RunCreate(
                run_id=run_id,
                idempotency_key="root:budget-session:budget-request:budget-turn",
                context=context,
                payload_fingerprint=fingerprint_json({"text": "budget"}),
                capability_fingerprint=context.capability_hash,
                driver_kind="react",
                profile_key="agent.general",
                persistence_level=PersistenceLevel.DURABLE,
            )
        )
    ).record


async def _set_status(
    uow: SqliteExecutionUnitOfWork,
    run_id: str,
    status: RunStatus,
    now: float,
) -> None:
    async with uow._write_transaction() as db:
        await db.execute(
            """UPDATE execution_runs
            SET status=?,version=version+1,updated_at=? WHERE run_id=?""",
            (status.value, now, run_id),
        )
        await db.commit()


async def _budget_row(path, run_id: str):
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        return await (
            await db.execute(
                "SELECT * FROM execution_run_active_budgets WHERE run_id=?",
                (run_id,),
            )
        ).fetchone()


@pytest.mark.asyncio
async def test_waiting_time_is_not_counted_and_budget_survives_resume(tmp_path):
    clock = Clock()
    path = tmp_path / "workflow.db"
    uow = SqliteExecutionUnitOfWork(path, clock=clock)
    await uow.initialize()
    record = await _root(uow)
    await uow.configure_run_active_budget(record.run_id, limit_seconds=10.0)
    # A retry/recovery may configure the same root again. The original budget
    # is frozen and must not be reset or enlarged by the later caller.
    await uow.configure_run_active_budget(record.run_id, limit_seconds=60.0)
    configured = await _budget_row(path, record.run_id)
    assert configured["limit_seconds"] == pytest.approx(10.0)
    assert configured["configured"] == 1

    clock.advance(4.0)
    await _set_status(uow, record.run_id, RunStatus.WAITING, clock.now)
    paused = await _budget_row(path, record.run_id)
    assert paused["budget_state"] == "paused"
    assert paused["active_since"] is None
    assert paused["consumed_seconds"] == pytest.approx(4.0)

    clock.advance(3600.0)
    assert await uow.claim_expired_active_budget_runs() == ()
    assert await uow.next_active_budget_delay() is None

    await _set_status(uow, record.run_id, RunStatus.RUNNING, clock.now)
    assert await uow.next_active_budget_delay() == pytest.approx(6.0)
    clock.advance(5.9)
    assert await uow.claim_expired_active_budget_runs() == ()
    clock.advance(0.2)
    claimed = await uow.claim_expired_active_budget_runs()
    assert [item.run_id for item in claimed] == [record.run_id]
    expired = await _budget_row(path, record.run_id)
    assert expired["budget_state"] == "expired"
    assert expired["active_since"] is None
    assert expired["consumed_seconds"] == pytest.approx(10.1)
    await uow.close()


@pytest.mark.asyncio
async def test_active_budget_claim_is_replayed_until_kernel_cancels(tmp_path):
    clock = Clock()
    uow = SqliteExecutionUnitOfWork(tmp_path / "workflow.db", clock=clock)
    await uow.initialize()
    record = await _root(uow, "replayed-budget-root")
    await uow.configure_run_active_budget(record.run_id, limit_seconds=1.0)
    clock.advance(1.1)

    cancelled: list[tuple[str, str]] = []

    class Kernel:
        async def cancel(self, ref, actor, reason):
            assert ref.run_id == record.run_id
            assert actor.session_id == record.context.session_id
            cancelled.append((ref.run_id, reason))

    reconciler = HarnessReconciler(uow, Kernel())
    assert await reconciler.reconcile_active_budgets_once() is False
    assert cancelled == [
        (record.run_id, "active_execution_budget_exhausted")
    ]

    # The fake Kernel deliberately did not terminalize the Run. The durable
    # expired claim remains visible so a restarted reconciler retries it.
    assert await reconciler.reconcile_active_budgets_once() is False
    assert cancelled == [
        (record.run_id, "active_execution_budget_exhausted"),
        (record.run_id, "active_execution_budget_exhausted"),
    ]
    await uow.close()
