"""Model selection through actual typed SDK results and public source resolution."""
import asyncio
import json
import sqlite3

import aiosqlite
import pytest

from deskpet.memory.human_memory_v7 import project_recall_fragments
from deskpet.memory.primary_visibility import PrimaryHistoryPolicy
from tests.memory.test_selected_short_runtime import seed, actual
from tests.memory.test_selected_short_sources import suppress
from tests.execution.test_primary_foreground_runtime import history_disclosure
from tests.sdk_adapters.test_context_route_tool import _service, state_db


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [[], ["semantic"]])
async def test_model_short_selection_is_one_budgeted_typed_execution(actual, state_db, monkeypatch, selected):
    runtime, groups = actual
    manager = await runtime.manager()
    observed = []
    execute = manager.execute_typed_recall
    async def capture(**kwargs):
        observed.append(kwargs)
        return await execute(**kwargs)
    async def forbidden(**kwargs):
        pytest.fail("Model short request issued a second standalone recall")
    monkeypatch.setattr(manager, "execute_typed_recall", capture)
    monkeypatch.setattr(manager, "recall_short_horizon", forbidden)
    tool = _service(state_db)
    tool._recall_executor = runtime.typed_recall
    result = await tool.handle_context_route({"route": "memory_standalone", "query": "quartznebula",
        "memory_types": selected, "include_short_horizon": True})
    assert "context_route_receipt" in result, result
    assert len(observed) == 1
    plan = observed[0]["plan"]
    assert plan.include_short_horizon and plan.budget.max_items == 8
    assert [t.value for t in plan.requested_memory_types] == selected
    fragment, = result["fragments"]
    assert fragment["lane"] == "short_horizon_typed"
    assert fragment["memory_type"] == "short_horizon"
    assert "early real preference" in fragment["payload"]["content"]
    proof = fragment["history_source_dependencies"]
    assert proof["short_horizon"] == []
    assert proof["recall"] == [fragment["history_binding"]]
    assert {(r["evidence_id"], r["envelope_hash"]) for r in proof["evidence"]} == {
        (r.envelope.evidence_id, r.envelope.envelope_hash) for r in groups[0].registrations}
    with sqlite3.connect(state_db) as db:
        detail = json.loads(db.execute("SELECT detail_json FROM context_route_tool_invocations").fetchone()[0])
    assert detail["recall_selection"] == {"origin": "model_proposal", "requested_memory_types": selected,
                                          "include_short_horizon": True}


@pytest.mark.asyncio
async def test_mixed_nonempty_results_share_one_plan_and_keep_distinct_bindings(actual, monkeypatch):
    from deskpet.memory.primary_visibility import read_evidence_pair
    from tests.memory.test_primary_visibility import materialize
    runtime, groups = actual
    authority = runtime.conversation_evidence_authority
    manager = await runtime.manager()
    await authority.bind_primary()
    source_id = groups[0].registrations[0].envelope.evidence_id
    async with aiosqlite.connect(authority.db_path) as db:
        db.row_factory = aiosqlite.Row
        envelope, receipt = await read_evidence_pair(db=db, subject=authority.subject,
            primary_ref=authority.primary_ref, evidence_id=source_id)
    await manager.ingest_committed_evidence(envelope, receipt)
    memory_id = await materialize(manager, runtime.principal(), envelope, receipt,
                                  semantic_value="quartznebula")
    calls = []
    execute = manager.execute_typed_recall
    async def capture(**kwargs):
        calls.append(kwargs)
        return await execute(**kwargs)
    async def forbidden(**kwargs):
        pytest.fail("Mixed selection issued a second standalone short query")
    monkeypatch.setattr(manager, "execute_typed_recall", capture)
    monkeypatch.setattr(manager, "recall_short_horizon", forbidden)
    lanes = await runtime.typed_recall(query="quartznebula", run_id="mixed-nonempty",
        turn_ordinal=1, memory_types=("semantic",), include_short_horizon=True)
    rows = project_recall_fragments(lanes)
    assert len(calls) == 1 and len(rows) == 2
    assert {row["lane"] for row in rows} == {"long_term_typed", "short_horizon_typed"}
    assert any(item.selected_item.source_ref == memory_id for item in lanes.execution.result.items)
    assert len({row["history_binding"]["result_id"] for row in rows}) == 1
    assert len({row["history_binding"]["item_id"] for row in rows}) == 2
    short = next(row for row in rows if row["lane"] == "short_horizon_typed")
    assert short["history_source_dependencies"]["recall"] == [short["history_binding"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, 1, 0, "true", [], {}])
async def test_invalid_short_flag_never_initializes_memory(state_db, flag):
    tool = _service(state_db)
    async def forbidden(**kwargs):
        pytest.fail("Invalid short flag reached recall")
    tool._recall_executor = forbidden
    result = await tool.handle_context_route({"route": "memory_standalone", "query": "old fact",
        "memory_types": [], "include_short_horizon": flag})
    assert result["error"]["code"] == "context_route_short_horizon_invalid"


@pytest.mark.asyncio
async def test_typed_short_sources_survive_reopen_but_not_late_forget(actual):
    runtime, groups = actual
    async def recall(ordinal):
        return await runtime.typed_recall(query="quartznebula", run_id="typed-short-consumer",
            turn_ordinal=ordinal, memory_types=(), include_short_horizon=True)
    fragment, = project_recall_fragments(await recall(1))
    authority = runtime.conversation_evidence_authority
    proof = fragment["history_source_dependencies"]
    await runtime.close()
    manager = await runtime.manager()
    async def check(*, subject, **kwargs):
        assert subject == authority.subject
        return await manager.check_history_visibility(principal=runtime.principal(), **kwargs)
    policy = PrimaryHistoryPolicy(authority.db_path, authority.subject, check)
    async def visible():
        async with aiosqlite.connect(authority.db_path) as db:
            db.row_factory = aiosqlite.Row
            return await policy.check_dependencies(db=db, primary_ref=authority.primary_ref,
                dependencies=proof, disclosure_context=history_disclosure())
    assert await visible()
    await suppress(manager, authority, groups[0].registrations[1].envelope.evidence_refs[1].evidence_id)
    assert not await visible()
    assert project_recall_fragments(await recall(2)) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing_port", "incomplete", "cancel"])
async def test_typed_short_missing_sources_never_exposed(actual, monkeypatch, fault):
    from dataclasses import replace
    from simple_harness_memory import ShortHorizonSourceItem
    runtime, _ = actual
    manager = await runtime.manager()
    original = manager.resolve_typed_short_horizon_sources
    async def altered(**kwargs):
        if fault == "cancel":
            raise asyncio.CancelledError
        snapshot = await original(**kwargs)
        return replace(snapshot, items=tuple(ShortHorizonSourceItem(
            item.binding_hash, False, "history_source_stale", False, ()) for item in snapshot.items))
    monkeypatch.setattr(manager, "resolve_typed_short_horizon_sources", None if fault == "missing_port" else altered)
    request = runtime.typed_recall(query="quartznebula", run_id="source-fault", turn_ordinal=1,
                                  memory_types=(), include_short_horizon=True)
    if fault == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await request
    else:
        lanes = await request
        assert lanes.execution.result.items  # actual selection existed before Host proof rejection
        assert project_recall_fragments(lanes) == ()
        assert any(code.startswith("short_horizon_sources_") for code in lanes.degradation_codes)
