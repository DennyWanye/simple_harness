"""Real public typed recall -> Host source proof -> fragments under the original budget."""
import json
import math
from dataclasses import replace
from functools import wraps

import pytest
from simple_harness.contracts import canonical_json
from simple_harness.runtime import RecallRetrievalMode

from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.human_memory_v7 import project_recall_fragments
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.memory.short_index_worker import PrimaryShortIndexWorker
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_foreground_runtime import Provider, build
from tests.memory.test_short_index_generation import TinyEmbedder


QUERY = 'quartzneedle'
PREFERENCE = '测试名称是白鹭；我的口味偏好是无糖茉莉茶。'


class PreferenceEmbedder(TinyEmbedder):
    """Test-only semantic relation: the query maps to the small original preference."""
    async def embed(self, text):
        return [1.0, 0.0] if text == QUERY or PREFERENCE in text else [0.0, 1.0]


def whole_group_cost(group):
    # Actual registered public text, not a SQL edit or a guessed token threshold.
    from simple_harness import thaw_json
    messages = []
    for registration in group.registrations:
        payload = thaw_json(registration.envelope.sanitized_payload)
        pointer = registration.metadata.public_text_json_pointer
        value = payload
        for component in pointer.split('/')[1:]:
            value = value[component]
        messages.append(f'{registration.metadata.role.value}: {value}')
    content = '\n'.join(messages)
    value = [{'source_kind': 'short_horizon', 'memory_type': None, 'payload': {
        'content': content, 'occurred_at': max(r.metadata.occurred_at for r in group.registrations)}}]
    encoded = canonical_json(value)
    size = len(encoded.encode('utf-8'))
    return content, size, max(1, len(encoded), math.ceil(size / 3))


@pytest.mark.asyncio
async def test_large_fts_small_vector_public_typed_fragments_and_long_only(tmp_path, monkeypatch):
    state = tmp_path / 'state.db'
    service = HumanMemoryHostServiceFactory(state,
        await dispatch_startup_epoch(state, approved_fresh_lane=True)).bind(local_owner_auth())
    await service.open_primary()
    foreground, stack, _ = await build(tmp_path, state, Provider())
    # Both eligible groups are real Host turns; ten later groups create the
    # normal recent-window exclusion. No edits to Memory or SDK table contents.
    inputs = [PREFERENCE, QUERY + ' ' + ('完整操作记录 ' * 600),
              *(f'这轮记录编号{i}，请简短确认。' for i in range(10))]
    try:
        for index, text in enumerate(inputs):
            await service.enqueue_turn(QueueTurnRequest(None, f'vector-budget-{index}', text))
            assert await foreground._drive_once() and foreground.last_error is None
    finally:
        await foreground.close()
        await stack.close()
    embedder = PreferenceEmbedder()
    runtime = compose_human_memory_runtime(state, tmp_path / 'memory.db',
        embedder_getter=lambda: embedder,
        adapter_factory=lambda *_: pytest.fail('no analysis/model call'))
    worker = PrimaryShortIndexWorker(runtime)
    try:
        delivery = MemoryIngestionOutboxWorker(state, runtime.manager, owner_id='vector-budget-test')
        for _ in inputs:
            assert await delivery.run_once() == 'delivered'
        built = await worker.step()
        assert built.generation.activated and built.generation.vector_count == 2
        authority = runtime.conversation_evidence_authority
        groups = [await authority.registrations_for_run(r) for r in await authority.completed_run_ids()]
        groups.sort(key=lambda g: g.registrations[0].metadata.causal_group_sequence)
        small, small_bytes, small_tokens = whole_group_cost(groups[0])
        large, large_bytes, large_tokens = whole_group_cost(groups[1])
        assert QUERY not in small and QUERY in large
        assert small_bytes < 16384 and small_tokens < 2048
        assert large_tokens > 2048  # actual whole-payload cost, no hardcoded outcome seed
        assert large_bytes < 16384  # isolate token budget from the byte limit
        manager = await runtime.manager()
        observed = []
        actual_execute = manager.execute_typed_recall
        @wraps(actual_execute)
        async def capture(**kwargs):
            observed.append(kwargs)
            return await actual_execute(**kwargs)
        monkeypatch.setattr(manager, 'execute_typed_recall', capture)
        lanes = await runtime.typed_recall(query=QUERY, run_id='public-vector-budget',
            turn_ordinal=1, memory_types=(), include_short_horizon=True)
        request = observed[-1]
        context, plan = request['context'], request['plan']
        assert context.allowed_retrieval_modes == plan.retrieval_modes == (
            RecallRetrievalMode.FULL_TEXT, RecallRetrievalMode.VECTOR)
        assert (plan.budget.max_items, plan.budget.max_bytes, plan.budget.max_tokens,
                plan.budget.deadline_ms) == (8, 16384, 2048, 1000)
        assert len(lanes.result.items) == 1 and lanes.result.truncated
        assert lanes.selected_typed_short_sources is not None
        assert all(item.visible for item in lanes.selected_typed_short_sources.items)
        fragments = project_recall_fragments(lanes)
        assert len(fragments) == 1 and fragments[0]['payload']['content'] == small
        assert len(fragments[0]['history_source_dependencies']['evidence']) >= 2
        assert PREFERENCE in json.dumps(fragments, ensure_ascii=False)
        # Public counterfactual with the same legitimate source/budget: the old
        # full_text-only plan has just the oversized group and selects nothing.
        fts_only = replace(plan, plan_id='public-vector-budget:fts-control',
            idempotency_key='public-vector-budget:fts-control',
            retrieval_modes=(RecallRetrievalMode.FULL_TEXT,))
        control_request = {**request, 'plan': fts_only}
        # A distinct public request must not reuse the Host observation identity.
        control_request.pop('observation_context', None)
        control = await actual_execute(**control_request)
        assert not control.result.items and control.result.truncated
        assert [code.value for code in control.result.reason_codes] == ['recall_budget_exhausted']
        await runtime.typed_recall(query=QUERY, run_id='public-vector-long-only',
            turn_ordinal=1, memory_types=('semantic',), include_short_horizon=False)
        assert observed[-1]['context'].allowed_retrieval_modes == (
            RecallRetrievalMode.FULL_TEXT,)
        assert observed[-1]['plan'].retrieval_modes == (RecallRetrievalMode.FULL_TEXT,)
    finally:
        await worker.close()
        await runtime.close()
