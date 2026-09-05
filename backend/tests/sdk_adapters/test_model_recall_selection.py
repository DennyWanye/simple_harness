"""Actual route service and installed Memory plan must retain model selection."""
import json
import sqlite3

import pytest
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.sdk_adapters.context_authority import canonical_sha256
from tests.sdk_adapters.test_context_route_tool import _service, state_db


@pytest.mark.asyncio
@pytest.mark.parametrize('selected', [['semantic'], ['episode'], ['prospective'], ['semantic', 'procedure']])
async def test_route_passes_only_explicit_types_to_installed_memory(state_db, tmp_path, monkeypatch, selected):
    runtime = HumanMemoryV7Runtime(tmp_path / 'memory.db')
    tool = _service(state_db)
    tool._recall_executor = runtime.typed_recall
    observed = []
    manager = await runtime.manager()
    original = manager.execute_typed_recall

    async def capture(**kwargs):
        observed.append(kwargs)
        return await original(**kwargs)

    async def no_short(**kwargs):
        pytest.fail('Explicit long-term selection must not query short horizon')

    monkeypatch.setattr(manager, 'execute_typed_recall', capture)
    monkeypatch.setattr(manager, 'recall_short_horizon', no_short)
    try:
        proposal = {'route': 'memory_standalone', 'query': 'preference', 'memory_types': selected}
        result = await tool.handle_context_route(proposal)
        assert 'context_route_receipt' in result, result
        assert 'short_horizon_unavailable' not in result['degradation_codes']
        assert len(observed) == 1
        plan = observed[0]['plan']
        assert [t.value for t in plan.requested_memory_types] == selected
        assert plan.disclosure_context.subject == runtime.principal().actor_id
        assert plan.budget.max_items == 8
        with sqlite3.connect(state_db) as db:
            row = db.execute('SELECT proposal_hash,detail_json FROM context_route_tool_invocations').fetchone()
        assert row[0] == canonical_sha256(proposal)
        assert json.loads(row[1]) == {
            'route': 'memory_standalone', 'task_scope_id': None,
            'recall_selection': {'origin': 'model_proposal', 'requested_memory_types': selected},
        }
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [None, [], ['semantic','semantic'], ['task_scope'], ['semantic', 3], 'semantic', ['semantic'] * 5])
async def test_invalid_selection_rejected_before_memory_access(state_db, value):
    tool = _service(state_db)
    calls = []

    async def forbidden(**kwargs):
        calls.append(kwargs)
        pytest.fail('Invalid model selection reached Memory')

    tool._recall_executor = forbidden
    proposal = {'route': 'memory_standalone', 'query': 'preference'}
    if value is not None:
        proposal['memory_types'] = value
    result = await tool.handle_context_route(proposal)
    assert result['error']['code'] in {'context_route_memory_types_required', 'context_route_memory_types_invalid'}
    assert calls == []
    with sqlite3.connect(state_db) as db:
        assert db.execute('SELECT verdict FROM context_route_tool_invocations').fetchone()[0] == 'rejected'


@pytest.mark.asyncio
async def test_runtime_rejects_invalid_selection_before_manager_initialization(tmp_path, monkeypatch):
    runtime = HumanMemoryV7Runtime(tmp_path / 'unused-memory.db')
    async def forbidden():
        pytest.fail('Invalid direct selection initialized the memory manager')
    monkeypatch.setattr(runtime, 'manager', forbidden)
    with pytest.raises(ValueError, match='memory_types_invalid'):
        await runtime.typed_recall(query='x', run_id='run', turn_ordinal=1, memory_types=('unknown',))
    assert not (tmp_path / 'unused-memory.db').exists()


@pytest.mark.asyncio
async def test_changed_selection_cannot_reuse_same_plan_result(tmp_path):
    from simple_harness_memory import MemoryIdempotencyConflict
    runtime = HumanMemoryV7Runtime(tmp_path / 'memory.db')
    try:
        first = await runtime.typed_recall(query='x', run_id='same-run', turn_ordinal=1, now=20, memory_types=('semantic',))
        replay = await runtime.typed_recall(query='x', run_id='same-run', turn_ordinal=1, now=20, memory_types=('semantic',))
        assert first.execution.result.result_hash == replay.execution.result.result_hash
        with pytest.raises(MemoryIdempotencyConflict, match='IDEMPOTENCY_CONFLICT'):
            await runtime.typed_recall(query='x', run_id='same-run', turn_ordinal=1, now=20, memory_types=('episode',))
    finally:
        await runtime.close()
