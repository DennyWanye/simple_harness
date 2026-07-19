from __future__ import annotations

import aiosqlite
import pytest

from deskpet.workflows.store.research_repository import ResearchWorkflowRepository
from deskpet.workflows.store.run_store import WorkflowRunStore


class _Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


async def _seed(path, run_id: str, *, wall_not_after: float) -> None:
    clock = _Clock(900.0)
    store = WorkflowRunStore(path, clock=clock)
    await store.create_run(
        request_key="start:" + run_id,
        session_id="session-1",
        request_id="request:" + run_id,
        turn_id="turn:" + run_id,
        workflow_name="deep_research",
        workflow_version="v6",
        manifest_hash="manifest-v6",
        implementation_hash="implementation-v6",
        capability_hash="capability-v6",
        capability_snapshot={"research": True},
        state_schema_version=6,
        run_id=run_id,
        trace_id="trace:" + run_id,
        thread_id="thread:" + run_id,
    )
    async with aiosqlite.connect(path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """UPDATE workflow_runs SET status='running',run_version=7,
            head_checkpoint_ns='',head_checkpoint_id='head',started_at=900,updated_at=900
            WHERE run_id=?""",
            (run_id,),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoints(
            thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,run_id,
            checkpoint_type,checkpoint_blob,metadata_blob,engine_kind,snapshot_version,created_at)
            VALUES(?,'','head',NULL,?,'native',X'7B7D',X'7B7D','deskpet-native',1,900)""",
            ("thread:" + run_id, run_id),
        )
        await db.execute(
            """INSERT INTO workflow_checkpoint_owners(
            run_id,thread_id,checkpoint_ns,checkpoint_id,parent_checkpoint_id,created_at)
            VALUES(?,?,'','head',NULL,900)""",
            (run_id, "thread:" + run_id),
        )
        await db.execute(
            """INSERT INTO workflow_research_deadlines(
            deadline_id,schema_version,run_id,parent_deadline_id,logical_scope,policy_hash,
            budget_ms,remaining_ms,created_at,last_observed_at,wall_not_after,offline_policy,
            rollback_tolerance_ms,revision,status,terminal_reason,terminal_at)
            VALUES(?,1,?,NULL,'run:automatic',?,900000,900000,900,900,?,'count',
            2000,0,'open',NULL,NULL)""",
            ("deadline:" + run_id, run_id, "a" * 64, wall_not_after),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_v6_generate_now_clamps_at_t895_and_duplicate_keeps_window(tmp_path) -> None:
    path = tmp_path / "workflow.db"
    await _seed(path, "run-v6", wall_not_after=1_800.0)
    clock = _Clock(1_795.0)
    repo = ResearchWorkflowRepository(path, clock=clock)
    opened, created = await repo.open_control(
        run_id="run-v6", idempotency_key="generate", action="generate_now",
        expected_run_version=7, expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head", payload={},
    )
    assert created is True
    accepted = await repo.accept_generate_now(
        opened["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="head"
    )
    assert accepted["status"] == "accepted"
    assert accepted["accepted_at"] == 1_795.0
    assert accepted["settle_deadline"] == 1_800.0
    clock.value = 1_799.0
    replay = await repo.accept_generate_now(
        opened["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="head"
    )
    assert replay["settle_deadline"] == 1_800.0


@pytest.mark.asyncio
async def test_v6_generate_now_rejects_at_t900_wall_guard(tmp_path) -> None:
    path = tmp_path / "workflow.db"
    await _seed(path, "run-v6", wall_not_after=1_800.0)
    repo = ResearchWorkflowRepository(path, clock=_Clock(1_800.0))
    opened, _ = await repo.open_control(
        run_id="run-v6", idempotency_key="late", action="generate_now",
        expected_run_version=7, expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head", payload={},
    )
    rejected = await repo.accept_generate_now(
        opened["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="head"
    )
    assert rejected["status"] == "rejected"
    assert rejected["payload"]["_result"] == {"reason": "wall_guard"}


@pytest.mark.asyncio
async def test_v6_generate_now_missing_deadline_is_terminally_rejected(tmp_path) -> None:
    path = tmp_path / "workflow.db"
    await _seed(path, "run-v6", wall_not_after=1_800.0)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "DELETE FROM workflow_research_deadlines WHERE run_id='run-v6'"
        )
        await db.commit()
    repo = ResearchWorkflowRepository(path, clock=_Clock(1_100.0))
    opened, _ = await repo.open_control(
        run_id="run-v6", idempotency_key="missing", action="generate_now",
        expected_run_version=7, expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head", payload={},
    )
    rejected = await repo.accept_generate_now(
        opened["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="head"
    )
    assert rejected["status"] == "rejected"
    assert rejected["payload"]["_result"] == {
        "reason": "automatic_deadline_missing"
    }
    assert await repo.active_control("run-v6") is None


@pytest.mark.asyncio
async def test_v6_reuses_accepted_observed_settled_consumed_journal(tmp_path) -> None:
    path = tmp_path / "workflow.db"
    await _seed(path, "run-v6", wall_not_after=1_800.0)
    repo = ResearchWorkflowRepository(path, clock=_Clock(1_100.0))
    opened, _ = await repo.open_control(
        run_id="run-v6", idempotency_key="journal", action="generate_now",
        expected_run_version=7, expected_head_checkpoint_ns="",
        expected_head_checkpoint_id="head", payload={},
    )
    accepted = await repo.accept_generate_now(
        opened["command_id"], brief_checkpoint_ns="", brief_checkpoint_id="head"
    )
    observed = await repo.transition_control(
        opened["command_id"], expected_status="accepted", new_status="observed",
        checkpoint_ns="", checkpoint_id="head",
    )
    settled = await repo.transition_control(
        opened["command_id"], expected_status="observed", new_status="settled",
        checkpoint_ns="", checkpoint_id="head", result={"answer_status": "partial"},
    )
    consumed = await repo.transition_control(
        opened["command_id"], expected_status="settled", new_status="consumed",
        checkpoint_ns="", checkpoint_id="head",
    )
    assert [accepted["status"], observed["status"], settled["status"], consumed["status"]] == [
        "accepted", "observed", "settled", "consumed"
    ]
    assert consumed["payload"]["_result"] == {"answer_status": "partial"}
