from __future__ import annotations

import aiosqlite
import pytest

from deskpet.workflows.human import (
    DecisionStatus,
    GrantStatus,
    HumanDecisionError,
    HumanDecisionStore,
)
from deskpet.workflows.store import StaleRunFence, WorkflowRunStore


async def _claimed_run(path, *, clock):
    runs = WorkflowRunStore(path, clock=clock)
    run_id, _ = await runs.create_run(
        request_key="human-request",
        session_id="session",
        request_id="request",
        turn_id="turn",
        workflow_name="test-human",
        workflow_version="1",
        manifest_hash="manifest",
        implementation_hash="implementation",
        capability_hash="capability",
        capability_snapshot={},
        state_schema_version=1,
    )
    return runs, run_id, await runs.claim(run_id, "runner")


def _approval(response):
    if not isinstance(response, dict) or not isinstance(response.get("approved"), bool):
        raise ValueError("approval response requires an approved boolean")
    return {"approved": response["approved"]}


@pytest.mark.asyncio
async def test_prepared_is_hidden_and_open_survives_restart(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    runs, run_id, fence = await _claimed_run(path, clock=lambda: now[0])
    store = HumanDecisionStore(path, clock=lambda: now[0])

    prepared = await store.prepare_decision(
        fence=fence,
        decision_id="decision-1",
        kind="approval",
        prompt={"question": "continue?"},
        checkpoint_id="checkpoint-1",
        expires_at=200.0,
    )
    assert prepared.status is DecisionStatus.PREPARED
    assert prepared.interrupt_id is None
    assert await store.list_open() == []

    restarted = HumanDecisionStore(path, clock=lambda: now[0])
    persisted = await restarted.get_decision("decision-1")
    assert persisted == prepared
    opened = await restarted.open_decision(
        "decision-1", fence=fence, interrupt_id="interrupt-1", task_id="task-1"
    )
    assert opened.status is DecisionStatus.OPEN
    assert opened.version == 1
    assert (
        await restarted.open_interrupt(
            "decision-1", fence=fence, interrupt_id="interrupt-1", task_id="task-1"
        )
        == opened
    )

    after_restart = HumanDecisionStore(path, clock=lambda: now[0])
    assert await after_restart.list_open(run_id=run_id) == [opened]
    run = await runs.get_run(run_id)
    assert run is not None
    assert run["status"] == "waiting"
    assert run["head_checkpoint_id"] == "checkpoint-1"
    assert run["lease_owner"] is None
    with pytest.raises(StaleRunFence):
        await runs.heartbeat(fence)


@pytest.mark.asyncio
async def test_open_rolls_back_if_run_fence_was_invalidated(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    runs, run_id, fence = await _claimed_run(path, clock=lambda: now[0])
    store = HumanDecisionStore(path, clock=lambda: now[0])
    await store.prepare_decision(
        fence=fence,
        decision_id="decision-1",
        kind="approval",
        prompt={},
        checkpoint_id="checkpoint-1",
    )
    await runs.request_cancel(run_id, "concurrent cancel")

    with pytest.raises(StaleRunFence):
        await store.open_decision(
            "decision-1", fence=fence, interrupt_id="interrupt-1"
        )
    decision = await store.get_decision("decision-1")
    assert decision is not None
    assert decision.status is DecisionStatus.PREPARED
    assert decision.interrupt_id is None
    assert await store.list_open(run_id=run_id) == []


@pytest.mark.asyncio
async def test_resolve_validates_and_uses_nonce_version_cas(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    runs, run_id, fence = await _claimed_run(path, clock=lambda: now[0])
    store = HumanDecisionStore(
        path, clock=lambda: now[0], validators={"approval": _approval}
    )
    await store.prepare_decision(
        fence=fence,
        decision_id="decision-1",
        kind="approval",
        prompt={},
        checkpoint_id="checkpoint-1",
    )
    opened = await store.open_decision(
        "decision-1", fence=fence, interrupt_id="interrupt-1"
    )

    with pytest.raises(HumanDecisionError) as invalid:
        await store.resolve_decision(
            "decision-1",
            nonce=opened.nonce,
            expected_version=opened.version,
            response={"approved": "yes"},
        )
    assert invalid.value.code == "invalid_decision_response"

    with pytest.raises(HumanDecisionError) as stale_nonce:
        await store.resolve_decision(
            "decision-1",
            nonce="old-card",
            expected_version=opened.version,
            response={"approved": True},
        )
    assert stale_nonce.value.code == "stale_decision"
    with pytest.raises(HumanDecisionError) as stale_version:
        await store.resolve_decision(
            "decision-1",
            nonce=opened.nonce,
            expected_version=opened.version - 1,
            response={"approved": True},
        )
    assert stale_version.value.code == "stale_decision"

    restarted = HumanDecisionStore(
        path, clock=lambda: now[0], validators={"approval": _approval}
    )
    resolved = await restarted.resolve_decision(
        "decision-1",
        nonce=opened.nonce,
        expected_version=opened.version,
        response={"approved": True, "ignored": "normalized"},
    )
    assert resolved.status is DecisionStatus.RESOLVED
    assert resolved.response == {"approved": True}
    assert await restarted.resume_payload("decision-1") == {
        "interrupt-1": {"approved": True}
    }
    run = await runs.get_run(run_id)
    assert run is not None and run["status"] == "retryable"

    with pytest.raises(HumanDecisionError) as duplicate:
        await restarted.resolve_decision(
            "decision-1",
            nonce=opened.nonce,
            expected_version=opened.version,
            response={"approved": True},
        )
    assert duplicate.value.code == "decision_already_resolved"


@pytest.mark.asyncio
async def test_resolved_decision_is_consumed_once_by_checkpoint_commit(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    runs, run_id, fence = await _claimed_run(path, clock=lambda: now[0])
    store = HumanDecisionStore(path, clock=lambda: now[0])
    await store.prepare_decision(
        fence=fence,
        decision_id="decision-1",
        kind="approval",
        prompt={},
        checkpoint_id="waiting-checkpoint",
    )
    opened = await store.open_decision(
        "decision-1", fence=fence, interrupt_id="interrupt-1"
    )
    resolved = await store.resolve_decision(
        "decision-1",
        nonce=opened.nonce,
        expected_version=opened.version,
        response={"approved": True},
    )
    assert await store.build_run_resume_payload(run_id) == {
        "interrupt-1": {"approved": True}
    }

    db = await runs._connect()
    try:
        await db.execute("BEGIN IMMEDIATE")
        consumed = await HumanDecisionStore.consume_resolved_in_transaction(
            db,
            run_id=run_id,
            decision_id="decision-1",
            checkpoint_id="resume-checkpoint",
            expected_version=resolved.version,
            now=101.0,
        )
        duplicate = await HumanDecisionStore.consume_resolved_in_transaction(
            db,
            run_id=run_id,
            decision_id="decision-1",
            checkpoint_id="resume-checkpoint",
            expected_version=resolved.version,
            now=102.0,
        )
        await db.commit()
    finally:
        await db.close()

    assert consumed.status is DecisionStatus.CONSUMED
    assert consumed.consumed_checkpoint_id == "resume-checkpoint"
    assert duplicate == consumed
    assert await store.build_run_resume_payload(run_id) == {}
    with pytest.raises(HumanDecisionError) as not_resumable:
        await store.build_resume_payload("decision-1")
    assert not_resumable.value.code == "decision_not_resolved"


@pytest.mark.asyncio
async def test_expired_resolution_closes_decision_grant_and_blocks_run(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    runs, run_id, fence = await _claimed_run(path, clock=lambda: now[0])
    store = HumanDecisionStore(path, clock=lambda: now[0])
    await store.prepare_decision(
        fence=fence,
        decision_id="decision-1",
        kind="approval",
        prompt={},
        checkpoint_id="checkpoint-1",
        expires_at=110.0,
    )
    opened = await store.open_decision(
        "decision-1", fence=fence, interrupt_id="interrupt-1"
    )
    grant = await store.prepare_grant(
        decision_id="decision-1", grant_id="grant-1", scope={"tool": "write_file"}
    )
    assert grant.status is GrantStatus.OPEN

    now[0] = 111.0
    with pytest.raises(HumanDecisionError) as expired:
        await store.resolve_decision(
            "decision-1",
            nonce=opened.nonce,
            expected_version=opened.version,
            response=True,
        )
    assert expired.value.code == "decision_expired"
    assert await store.list_open(run_id=run_id) == []
    decision = await store.get_decision("decision-1")
    closed_grant = await store.get_grant("grant-1")
    assert decision is not None and decision.status is DecisionStatus.EXPIRED
    assert closed_grant is not None and closed_grant.status is GrantStatus.EXPIRED
    run = await runs.get_run(run_id)
    assert run is not None
    assert run["status"] == "blocked"
    assert run["recovery_action"] == "reopen_decision_or_cancel"


@pytest.mark.asyncio
async def test_expiry_sweeper_closes_prepared_decisions_without_exposing_them(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    _, _, fence = await _claimed_run(path, clock=lambda: now[0])
    store = HumanDecisionStore(path, clock=lambda: now[0])
    await store.prepare_decision(
        fence=fence,
        decision_id="prepared",
        kind="clarification",
        prompt={"question": "which file?"},
        checkpoint_id="checkpoint-1",
        expires_at=101.0,
    )
    await store.prepare_grant(
        decision_id="prepared", grant_id="prepared-grant", scope={}
    )
    now[0] = 102.0

    assert await store.expire_decisions() == 1
    decision = await store.get_decision("prepared")
    grant = await store.get_grant("prepared-grant")
    assert decision is not None and decision.status is DecisionStatus.EXPIRED
    assert grant is not None and grant.status is GrantStatus.EXPIRED
    assert await store.list_open() == []


@pytest.mark.asyncio
async def test_cancel_atomically_closes_open_decisions_and_grants(tmp_path):
    now = [100.0]
    path = tmp_path / "workflow.db"
    runs, run_id, fence = await _claimed_run(path, clock=lambda: now[0])
    store = HumanDecisionStore(path, clock=lambda: now[0])
    await store.prepare_decision(
        fence=fence,
        decision_id="decision-1",
        kind="permission",
        prompt={},
        checkpoint_id="checkpoint-1",
    )
    await store.open_decision(
        "decision-1", fence=fence, interrupt_id="interrupt-1"
    )
    await store.prepare_grant(
        decision_id="decision-1", grant_id="grant-1", scope={"args_hash": "abc"}
    )

    assert await store.cancel_run(run_id, reason="user stopped") == 1
    decision = await store.get_decision("decision-1")
    grant = await store.get_grant("grant-1")
    assert decision is not None and decision.status is DecisionStatus.CANCELLED
    assert grant is not None and grant.status is GrantStatus.CANCELLED
    assert await store.list_open(run_id=run_id) == []
    run = await runs.get_run(run_id)
    assert run is not None
    assert run["status"] == "cancel_requested"
    assert run["cancel_reason"] == "user stopped"

    async with aiosqlite.connect(path) as db:
        row = await (
            await db.execute(
                "SELECT lease_owner,lease_expires_at FROM workflow_runs WHERE run_id=?",
                (run_id,),
            )
        ).fetchone()
    assert row == (None, None)
