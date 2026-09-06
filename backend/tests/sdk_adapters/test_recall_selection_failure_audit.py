"""Real route ledger persistence for failed/cancelled model recall proposals."""
import asyncio
import json
import sqlite3

import pytest

from deskpet.sdk_adapters.context_authority import canonical_sha256
from deskpet.sdk_adapters.context_route import _CompositionUnavailable
from tests.sdk_adapters.test_context_route_tool import _service, state_db


PROPOSAL = {"route": "memory_standalone", "query": "PRIVATE_QUERY_CANARY",
            "memory_types": ["semantic"], "include_short_horizon": True}
SELECTION = {"origin": "model_proposal", "requested_memory_types": ["semantic"],
             "include_short_horizon": True}


def durable_row(path):
    # Open a new connection after the tool exits, rather than inspecting mocks.
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT sdk_run_id,raw_call_id,effect_id,proposal_hash,"
                          "verdict,decision_id,detail_json FROM context_route_tool_invocations").fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert row[:3] == ("run-route-1", "raw-1", "effect-1")
        assert row[4:6] == ("rejected", None)
        assert db.execute("SELECT count(*) FROM context_route_decisions").fetchone()[0] == 0
    return row[3], json.loads(row[6])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,code", [
    (RuntimeError("PRIVATE_EXCEPTION_CANARY"), "context_route_adjudication_failed"),
    (TimeoutError("PRIVATE_EXCEPTION_CANARY"), "context_route_recall_timeout"),
    (_CompositionUnavailable(), "context_route_composition_unavailable"),
])
async def test_failure_has_safe_selection_and_no_committed_route(state_db, failure, code):
    tool = _service(state_db)
    async def fail(**kwargs):
        assert kwargs["memory_types"] == ("semantic",)
        assert kwargs["include_short_horizon"] is True
        raise failure
    tool._recall_executor = fail
    result = await tool.handle_context_route(PROPOSAL)
    assert result == {"ok": False, "error": {"code": code}}
    proposal_hash, detail = durable_row(state_db)
    assert proposal_hash == canonical_sha256(PROPOSAL)
    assert detail == {"code": code, "recall_selection": SELECTION}
    assert "PRIVATE_" not in json.dumps([result, detail])


@pytest.mark.asyncio
async def test_invalid_selection_is_hashed_not_copied_into_audit(state_db):
    tool = _service(state_db)
    async def forbidden(**kwargs):
        pytest.fail("invalid selection reached memory")
    tool._recall_executor = forbidden
    proposal = {**PROPOSAL, "memory_types": ["PRIVATE_INVALID_CANARY"]}
    result = await tool.handle_context_route(proposal)
    proposal_hash, detail = durable_row(state_db)
    assert proposal_hash == canonical_sha256(proposal)
    assert result["error"]["code"] == "context_route_memory_types_invalid"
    assert detail["recall_selection"] == {
        "origin": "model_proposal", "selection_status": "invalid",
        "selection_error": "context_route_memory_types_invalid",
    }
    assert "PRIVATE_" not in json.dumps([result, detail])


@pytest.mark.asyncio
async def test_actual_task_cancellation_is_persisted_then_propagated(state_db):
    tool = _service(state_db)
    entered = asyncio.Event()
    async def wait(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    tool._recall_executor = wait
    task = asyncio.create_task(tool.handle_context_route(PROPOSAL))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        proposal_hash, detail = durable_row(state_db)
        assert proposal_hash == canonical_sha256(PROPOSAL)
        assert detail == {"code": "context_route_recall_cancelled", "recall_selection": SELECTION}
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["storage_error", "audit_timeout", "second_cancel"])
async def test_audit_failure_cannot_swallow_cancellation(state_db, monkeypatch, caplog, failure_mode):
    tool = _service(state_db)
    entered = asyncio.Event()
    audit_entered = asyncio.Event()
    audit_finished = asyncio.Event()
    async def wait(**kwargs):
        entered.set()
        await asyncio.Event().wait()
    async def audit(**kwargs):
        audit_entered.set()
        try:
            if failure_mode == "storage_error":
                raise RuntimeError("PRIVATE_AUDIT_CANARY")
            await asyncio.Event().wait()
        finally:
            audit_finished.set()
    monkeypatch.setattr("deskpet.sdk_adapters.context_route._AUDIT_CANCEL_SECONDS", 0.05)
    tool._recall_executor = wait
    monkeypatch.setattr(tool._ledger, "record_tool_invocation", audit)
    task = asyncio.create_task(tool.handle_context_route(PROPOSAL))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        await asyncio.wait_for(audit_entered.wait(), 2)
        if failure_mode == "second_cancel":
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert audit_finished.is_set()
        assert "context_route_cancel_audit_unavailable" in caplog.text
        assert "PRIVATE_" not in caplog.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_writer_lock_does_not_delay_cancel_audit_cleanup(state_db, monkeypatch, caplog):
    tool = _service(state_db)
    entered = asyncio.Event()
    closed = asyncio.Event()
    statements = []
    original_connect = tool._ledger._connect

    async def tracked_connect():
        db = await original_connect()
        await db.set_trace_callback(statements.append)
        original_close = db.close
        async def close():
            await original_close()
            closed.set()
        db.close = close
        return db

    async def wait(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    tool._recall_executor = wait
    monkeypatch.setattr(tool._ledger, "_connect", tracked_connect)
    blocker = sqlite3.connect(state_db)
    blocker.execute("BEGIN IMMEDIATE")
    task = asyncio.create_task(tool.handle_context_route(PROPOSAL))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert closed.is_set(), "cancel returned before its ledger connection closed"
        assert statements.index("PRAGMA busy_timeout=0") < statements.index("BEGIN IMMEDIATE")
        assert "context_route_cancel_audit_unavailable" in caplog.text
        assert "PRIVATE_" not in caplog.text
        assert blocker.execute("SELECT count(*) FROM context_route_tool_invocations").fetchone()[0] == 0
        assert blocker.execute("SELECT count(*) FROM context_route_decisions").fetchone()[0] == 0
    finally:
        blocker.rollback()
        blocker.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_postcommit_audit_failure_does_not_prove_route_absent(state_db, monkeypatch):
    from tests.sdk_adapters.test_context_route_tool import _decision_rows
    tool = _service(state_db)
    original = tool._ledger.record_tool_invocation
    async def fail_accepted(**kwargs):
        if kwargs["verdict"] == "accepted":
            raise RuntimeError("audit writer failed after route decision")
        return await original(**kwargs)
    monkeypatch.setattr(tool._ledger, "record_tool_invocation", fail_accepted)
    result = await tool.handle_context_route({"route": "direct_standalone"})
    assert result["ok"] is False
    # Existing two-transaction boundary: a tool failure can follow a durable
    # route decision. This test prevents claiming atomicity from precommit cases.
    assert len(_decision_rows(state_db)) == 1
    with sqlite3.connect(state_db) as db:
        assert db.execute("SELECT verdict FROM context_route_tool_invocations").fetchone()[0] == "rejected"
