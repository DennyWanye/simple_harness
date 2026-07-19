from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone

import aiosqlite
import pytest

from deskpet.workflows.contracts import canonical_json
from deskpet.workflows.effects import EffectAction, EffectStateConflict, PreparedToolCall
from deskpet.workflows.store import RunFence
from deskpet.workflows.adapters.deep_research_v6_retrieval_runtime import (
    v6_read_logical_effect_id,
)
from tests.test_workflow_v6_read_effects import _runtime


def _prepared(logical: str, *, attempt: int) -> PreparedToolCall:
    return PreparedToolCall.prepare(
        tool_name="official_source_search",
        stable_call_id=logical,
        final_params={"attempt": attempt},
        tool_spec_version="official-source-search-v1",
        schema_hash="deep-research-v6-official_search-result-v1",
        permission_policy_version="research-readonly-v1",
        effect_type="deep_research_v6_official_search",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault_point", "committed"),
    [
        ("after_deadline_before_effect", False),
        ("before_commit", False),
        ("after_commit", True),
    ],
)
async def test_deadline_observation_effect_head_and_budget_share_one_transaction(
    tmp_path, fault_point, committed
):
    database, fence, identity, deadlines, reads = await _runtime(tmp_path)
    route_id = "route_" + "a" * 24
    policy_hash = "b" * 64
    await deadlines.prepare_route(
        identity=identity,
        route_id=route_id,
        policy_hash=policy_hash,
        budgets={"query": 2, "fetch": 1, "browser": 0, "llm": 0, "lane": 1},
    )
    deadline, _ = await deadlines.resume_root(
        identity=identity,
        route_id=route_id,
        policy_hash=policy_hash,
        budget_ms=120_000,
    )
    logical = v6_read_logical_effect_id(
        run_id=identity.run_id,
        route_id=route_id,
        operation_kind="official_search",
        target_or_page_id="target-atomic",
        ordinal=0,
    )

    def fault(stage: str) -> None:
        if stage == fault_point:
            raise RuntimeError(stage)

    with pytest.raises(RuntimeError, match=fault_point):
        await reads.journal.begin_idempotent_read_attempt(
            fence,
            node_execution_id="node-atomic",
            node_id=identity.node_id,
            logical_effect_id=logical,
            attempt_no=1,
            deadline_id=deadline.deadline_id,
            deadline_revision=deadline.revision,
            deadline_now_wall=datetime.now(timezone.utc),
            deadline_now_monotonic_ns=10_000_000_000,
            resource_kind="query",
            resource_hard_limit=2,
            resource_policy_hash=policy_hash,
            prepared=_prepared(logical, attempt=1),
            fault_injector=fault,
        )

    async with aiosqlite.connect(database) as db:
        effect_count = (await (await db.execute(
            "SELECT COUNT(*) FROM workflow_effects WHERE logical_effect_id=?", (logical,)
        )).fetchone())[0]
        head_count = (await (await db.execute(
            "SELECT COUNT(*) FROM workflow_effect_attempt_heads WHERE logical_effect_id=?",
            (logical,),
        )).fetchone())[0]
        reserved = (await (await db.execute(
            "SELECT reserved FROM workflow_research_resource_budgets "
            "WHERE run_id=? AND resource_kind='query'", (identity.run_id,)
        )).fetchone())[0]
        stored_revision = (await (await db.execute(
            "SELECT revision FROM workflow_research_deadlines WHERE deadline_id=?",
            (deadline.deadline_id,),
        )).fetchone())[0]
    assert (effect_count, head_count, reserved) == ((1, 1, 1) if committed else (0, 0, 0))
    assert stored_revision == deadline.revision + (1 if committed else 0)


@pytest.mark.asyncio
async def test_stale_attempt_reconcile_concurrency_attempt_two_and_late_result(tmp_path):
    database, fence, identity, deadlines, reads = await _runtime(tmp_path)
    route_id = "route_" + "c" * 24
    policy_hash = "d" * 64
    await deadlines.prepare_route(
        identity=identity,
        route_id=route_id,
        policy_hash=policy_hash,
        budgets={"query": 2, "fetch": 1, "browser": 0, "llm": 0, "lane": 1},
    )
    deadline, _ = await deadlines.resume_root(
        identity=identity,
        route_id=route_id,
        policy_hash=policy_hash,
        budget_ms=120_000,
    )
    logical = v6_read_logical_effect_id(
        run_id=identity.run_id,
        route_id=route_id,
        operation_kind="official_search",
        target_or_page_id="target-retry",
        ordinal=0,
    )
    first = await reads.journal.begin_idempotent_read_attempt(
        fence,
        node_execution_id="node-attempt-1",
        node_id=identity.node_id,
        logical_effect_id=logical,
        attempt_no=1,
        deadline_id=deadline.deadline_id,
        deadline_revision=deadline.revision,
        resource_kind="query",
        resource_hard_limit=2,
        resource_policy_hash=policy_hash,
        prepared=_prepared(logical, attempt=1),
    )
    assert first.action is EffectAction.EXECUTE

    async with aiosqlite.connect(database) as db:
        await db.execute(
            """UPDATE workflow_runs SET lease_owner='takeover',lease_epoch=lease_epoch+1,
            run_version=run_version+1 WHERE run_id=?""",
            (identity.run_id,),
        )
        await db.commit()
        row = await (await db.execute(
            "SELECT lease_epoch,run_version FROM workflow_runs WHERE run_id=?",
            (identity.run_id,),
        )).fetchone()
    takeover = RunFence(identity.run_id, "takeover", int(row[0]), int(row[1]))

    reconciled = await asyncio.gather(
        *(
            reads.journal.reconcile_idempotent_read_for_retry(
                takeover, logical_effect_id=logical, attempt_no=1
            )
            for _ in range(2)
        )
    )
    assert {item.status.value for item in reconciled} == {"failed"}

    begins = await asyncio.gather(
        *(
            reads.journal.begin_idempotent_read_attempt(
                takeover,
                node_execution_id=f"node-attempt-2-{index}",
                node_id=identity.node_id,
                logical_effect_id=logical,
                attempt_no=2,
                deadline_id=deadline.deadline_id,
                deadline_revision=deadline.revision,
                resource_kind="query",
                resource_hard_limit=2,
                resource_policy_hash=policy_hash,
                prepared=_prepared(logical, attempt=2),
            )
            for index in range(2)
        )
    )
    assert sorted(item.action.value for item in begins) == ["execute", "in_flight"]
    second = next(item for item in begins if item.action is EffectAction.EXECUTE)
    result_blob = await reads.blobs.put(
        canonical_json({"schema_version": 1, "result": "attempt-2"}).encode(),
        identity,
        media_type="application/vnd.deskpet.official-search-result+json",
    )
    result_ref = f"sha256:{result_blob.sha256}"
    await reads.journal.commit_idempotent_read_attempt(
        takeover,
        effect_id=second.effect_id,
        canonical_result_ref=result_ref,
        dependency_refs=(result_ref,),
        result_kind="official_search",
        deadline_now_wall=datetime.now(timezone.utc),
        deadline_now_monotonic_ns=20_000_000_000,
    )
    with pytest.raises(EffectStateConflict):
        await reads.journal.commit_idempotent_read_attempt(
            takeover,
            effect_id=first.effect_id,
            canonical_result_ref=result_ref,
            dependency_refs=(result_ref,),
            result_kind="official_search",
        )

    async with aiosqlite.connect(database) as db:
        head = await (await db.execute(
            """SELECT latest_attempt_no,canonical_effect_id
            FROM workflow_effect_attempt_heads WHERE logical_effect_id=?""",
            (logical,),
        )).fetchone()
        effects = await (await db.execute(
            "SELECT attempt_no,status FROM workflow_effects WHERE logical_effect_id=? "
            "ORDER BY attempt_no", (logical,)
        )).fetchall()
        budget = await (await db.execute(
            "SELECT reserved,consumed FROM workflow_research_resource_budgets "
            "WHERE run_id=? AND resource_kind='query'", (identity.run_id,)
        )).fetchone()
        committed_deadline_revision = (await (await db.execute(
            "SELECT revision FROM workflow_research_deadlines WHERE deadline_id=?",
            (deadline.deadline_id,),
        )).fetchone())[0]
        await db.execute(
            "UPDATE workflow_research_deadlines SET status='expired',terminal_reason='wall_guard' "
            "WHERE deadline_id=?", (deadline.deadline_id,)
        )
        await db.commit()
    assert tuple(head) == (2, second.effect_id)
    assert [tuple(row) for row in effects] == [(1, "failed"), (2, "committed")]
    assert tuple(budget) == (0, 2)
    assert committed_deadline_revision == deadline.revision + 1

    replay = await reads.journal.begin_idempotent_read_attempt(
        takeover,
        node_execution_id="node-attempt-2-replay",
        node_id=identity.node_id,
        logical_effect_id=logical,
        attempt_no=2,
        deadline_id=deadline.deadline_id,
        deadline_revision=deadline.revision,
        resource_kind="query",
        resource_hard_limit=2,
        resource_policy_hash=policy_hash,
        prepared=_prepared(logical, attempt=2),
    )
    assert replay.action is EffectAction.REUSE
    assert replay.canonical_result_ref == result_ref
