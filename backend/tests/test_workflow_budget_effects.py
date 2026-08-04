from __future__ import annotations

import asyncio

import aiosqlite
import pytest

from deskpet.workflows.contracts import EffectKind, EffectPolicy
from deskpet.workflows.effects import (
    BudgetReservationExceeded,
    EffectAction,
    EffectJournal,
    EffectStateConflict,
    EffectStatus,
    NormalizedToolOutcome,
    PreparedToolCall,
)
from deskpet.workflows.store import StaleRunFence, WorkflowRunStore


def _prepared(key: str) -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name="research_llm",
        stable_call_id=key,
        final_params={"payload_ref": f"blob:{key}"},
        tool_spec_version="v2",
        schema_hash="schema-v2",
        permission_policy_version="policy-v1",
        effect_type="research_llm",
    )


async def _run(tmp_path, *, limits=(100, 100, 1_000), clock=None):
    path = tmp_path / "workflow.db"
    store = WorkflowRunStore(path, **({"clock": clock} if clock is not None else {}))
    snapshot = {
        "research_llm_budget": {
            "max_input_tokens": limits[0],
            "max_output_tokens": limits[1],
            "max_cost_micros": limits[2],
        }
    }
    run_id, _ = await store.create_run(
        request_key="budget-run",
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="deep_research",
        workflow_version="5",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="budget-capability",
        capability_snapshot=snapshot,
        state_schema_version=5,
    )
    return path, store, run_id, await store.claim(run_id, "runner")


async def _begin(journal, fence, key, reserved=(40, 40, 400)):
    return await journal.begin_with_budget(
        fence,
        node_execution_id=f"node-{key}",
        workflow_name="deep_research",
        workflow_version="5",
        node_id="research_call",
        logical_effect_key=key,
        prepared=_prepared(key),
        policy=EffectPolicy("llm-at-most-once", "v1", EffectKind.OPAQUE_MANUAL),
        ledger_kind="llm",
        input_reserved=reserved[0],
        output_reserved=reserved[1],
        cost_reserved_micros=reserved[2],
    )


@pytest.mark.asyncio
async def test_concurrent_reservation_is_capped_in_the_begin_transaction(tmp_path):
    path, _, _, fence = await _run(tmp_path)
    journal = EffectJournal(path)

    results = await asyncio.gather(
        _begin(journal, fence, "first", (60, 60, 600)),
        _begin(journal, fence, "second", (60, 60, 600)),
        return_exceptions=True,
    )

    assert sum(getattr(item, "action", None) is EffectAction.EXECUTE for item in results) == 1
    assert sum(isinstance(item, BudgetReservationExceeded) for item in results) == 1
    ledger = await journal.rebuild_budget_ledger(fence, ledger_kind="llm")
    assert ledger.charged_input_tokens == 60
    assert len(ledger.reservations) == 1


@pytest.mark.asyncio
async def test_dispatch_cas_and_schema_check_are_idempotent(tmp_path):
    path, _, _, fence = await _run(tmp_path)
    journal = EffectJournal(path)
    begun = await _begin(journal, fence, "dispatch")

    first = await journal.mark_upstream_started(fence, begun.effect.effect_id)
    second = await journal.mark_upstream_started(fence, begun.effect.effect_id)
    assert first.dispatch_state == second.dispatch_state == "started"
    assert first.upstream_started_at == second.upstream_started_at

    async with aiosqlite.connect(path) as db:
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                """UPDATE workflow_effect_budget_reservations
                SET dispatch_state='not_started' WHERE effect_id=?""",
                (begun.effect.effect_id,),
            )


@pytest.mark.asyncio
async def test_known_usage_commits_actuals_and_rebuilds_authoritative_ledger(tmp_path):
    path, _, _, fence = await _run(tmp_path)
    journal = EffectJournal(path)
    begun = await _begin(journal, fence, "known", (80, 70, 700))
    await journal.mark_upstream_started(fence, begun.effect.effect_id)

    record = await journal.commit_or_hold(
        fence,
        begun.effect.effect_id,
        NormalizedToolOutcome.success({"result_ref": "blob:result"}),
        input_actual=30,
        output_actual=20,
        cost_actual_micros=250,
    )

    assert record.status is EffectStatus.COMMITTED
    ledger = await journal.rebuild_budget_ledger(fence, ledger_kind="llm")
    assert (
        ledger.charged_input_tokens,
        ledger.charged_output_tokens,
        ledger.charged_cost_micros,
    ) == (30, 20, 250)
    assert ledger.reservations[0].status == "committed"
    assert ledger.reservations[0].input_actual == 30


@pytest.mark.asyncio
async def test_unknown_usage_releases_before_dispatch_and_holds_after_dispatch(tmp_path):
    path, _, _, fence = await _run(tmp_path)
    journal = EffectJournal(path)
    before = await _begin(journal, fence, "before", (40, 40, 400))
    released = await journal.commit_or_hold(
        fence,
        before.effect.effect_id,
        NormalizedToolOutcome.failure("validation_failed", "not sent"),
    )
    assert released.status is EffectStatus.FAILED

    after = await _begin(journal, fence, "after", (40, 40, 400))
    await journal.mark_upstream_started(fence, after.effect.effect_id)
    held = await journal.commit_or_hold(
        fence,
        after.effect.effect_id,
        NormalizedToolOutcome.malformed("provider timed out without usage"),
    )
    assert held.status is EffectStatus.UNCERTAIN

    ledger = await journal.rebuild_budget_ledger(fence, ledger_kind="llm")
    states = {item.effect_id: item.status for item in ledger.reservations}
    assert states[before.effect.effect_id] == "released"
    assert states[after.effect.effect_id] == "held_uncertain"
    assert ledger.charged_input_tokens == 40


@pytest.mark.asyncio
async def test_started_call_after_owner_change_is_reconcile_not_reexecute(tmp_path):
    now = [100.0]
    path, store, run_id, first_fence = await _run(tmp_path, clock=lambda: now[0])
    journal = EffectJournal(path, clock=lambda: now[0])
    begun = await _begin(journal, first_fence, "crash", (50, 50, 500))
    await journal.mark_upstream_started(first_fence, begun.effect.effect_id)

    now[0] = 200.0
    replacement = await store.claim(run_id, "replacement")
    resumed = await _begin(journal, replacement, "crash", (50, 50, 500))
    assert resumed.action is EffectAction.RECONCILE
    assert resumed.effect.status is EffectStatus.UNCERTAIN
    with pytest.raises(StaleRunFence):
        await journal.commit_or_hold(
            first_fence,
            begun.effect.effect_id,
            NormalizedToolOutcome.success({"result_ref": "late"}),
            input_actual=1,
            output_actual=1,
            cost_actual_micros=1,
        )
    await journal.commit_or_hold(
        replacement,
        begun.effect.effect_id,
        NormalizedToolOutcome.malformed("crash after upstream start"),
    )
    ledger = await journal.rebuild_budget_ledger(replacement, ledger_kind="llm")
    assert ledger.reservations[0].status == "held_uncertain"


@pytest.mark.asyncio
async def test_budget_begin_fails_closed_without_capability_snapshot(tmp_path):
    path, store, run_id, fence = await _run(tmp_path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE workflow_capabilities SET snapshot_json='{}' WHERE capability_hash='budget-capability'"
        )
        await db.commit()
    journal = EffectJournal(path)

    with pytest.raises(EffectStateConflict, match="missing budget key"):
        await _begin(journal, fence, "missing-capability")
    async with aiosqlite.connect(path) as db:
        count = await (await db.execute("SELECT COUNT(*) FROM workflow_effects")).fetchone()
    assert count == (0,)
    assert await store.get_capability_snapshot(run_id) == {}
