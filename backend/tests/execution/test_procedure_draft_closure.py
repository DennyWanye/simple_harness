"""Actual draft-only source changes before closure HTTP; unrelated proof stays visible."""
import asyncio
import json

import aiosqlite
import pytest
import simple_harness as h
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from simple_harness_memory import MemoryManager, SuppressionRequest, SuppressionScopeKind

from deskpet.execution.closure_request_guard import ClosurePhysicalRequestGuard
from deskpet.execution.primary_dependencies import dependencies
from deskpet.memory.procedure_recovery_schema import initialize_procedure_recovery_state_db
from deskpet.memory.runtime_composition import compose_human_memory_runtime
from deskpet.sdk_adapters.procedure_discovery import procedure_discovery_registration
from tests.execution import test_closure_request_guard as fixture
from tests.execution.test_primary_create_new_runtime import CreateProvider
from tests.memory.test_procedure_scope_runtime import create_draft


class DraftAnswerProvider(CreateProvider):
    selected = None

    async def invoke(self, request, *, cancel):
        if len(self.requests) < 5:
            return await super().invoke(request, cancel=cancel)
        self.requests.append(request)
        if len(self.requests) == 6:
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, '查找记录流程'),
                tool_calls=(ProviderToolCall(h.CallId('closure-draft-discover'), 'procedure_discover',
                    {'query': '记录', 'after': ''}),), model='model', usage=ProviderUsage(10, 10, 20))
        result = json.loads([m.content for m in request.messages if m.role.value == 'tool'][-1])['value']
        self.selected = result['candidates'][0]['candidate']
        return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT,
            '文件已写入，发现流程：' + '；'.join(self.selected['steps'])),
            model='model', usage=ProviderUsage(10, 10, 20))


@pytest.mark.asyncio
async def test_actual_draft_only_forget_before_closure_blocks_http_without_incidental_barrier(tmp_path, monkeypatch):
    original_build = fixture.build
    memory = None
    world = None
    target = None

    async def with_discovery(path, state, provider, **kwargs):
        nonlocal memory, target
        await initialize_procedure_recovery_state_db(state)
        async def builder(db_path, **options):
            return await MemoryManager.build_human_memory_v7(db_path, **options, allow_development_embedder=True)
        memory = compose_human_memory_runtime(state, path / 'discovery-memory.db',
            adapter_factory=lambda _: None, backend_factory=builder)
        # Separate admitted source, never a member of this queued turn/history.
        target = await create_draft(memory, state)
        return await original_build(path, state, provider, **kwargs,
            visibility_memory=memory, procedure_runtime=memory.procedure_runtime,
            extra_registrations=(procedure_discovery_registration(memory.procedure_runtime),))

    monkeypatch.setattr(fixture, 'build', with_discovery)
    monkeypatch.setattr(fixture, 'OmitClosureProvider', DraftAnswerProvider)
    physical_check = ClosurePhysicalRequestGuard.__call__
    reached = []
    diagnostics = []

    async def forget_at_physical(guard, request):
        prepared = world.prepared[0]
        proof = prepared['dependencies']
        # Read the real bound request and public candidate, not an injected proof.
        assert world.provider.selected['memory_id'] == target[0]
        manager = await memory.manager()
        decision = await manager.suppress(principal=memory.principal(), request=SuppressionRequest(
            'closure-draft-only-forget', memory.principal().actor_id,
            SuppressionScopeKind.MEMORY, target[0], 'user_forget', memory.semantic_clock()))
        assert decision.scope_ref == target[0] and decision.request_id == 'closure-draft-only-forget'
        ordinary = dependencies(proof['evidence'], proof['recall'], proof.get('short_horizon', ()))
        async with aiosqlite.connect(world.state) as db:
            db.row_factory = aiosqlite.Row
            # Real SDK/Host check: old lanes remain visible after forget. Thus
            # they cannot accidentally make the original missing-lane code red.
            assert await world.runtime.history_policy.check_dependencies(db=db,
                primary_ref=prepared['host']['run']['primary_conversation_id'],
                dependencies=ordinary,
                disclosure_context=h.DisclosureContext.from_json(prepared['disclosure']))
        reached.append(proof)
        return await physical_check(guard, request)

    async def diagnosed_guard(guard, request):
        try:
            return await forget_at_physical(guard, request)
        except Exception as error:
            import traceback
            diagnostics.append(traceback.format_exc())
            raise

    monkeypatch.setattr(ClosurePhysicalRequestGuard, '__call__', diagnosed_guard)
    try:
        world = await fixture.world(tmp_path, monkeypatch, 'draft_only_forget')
        await world.runtime.after_enqueue(subject=world.runtime.subject)
        await asyncio.wait_for(world.runtime.drain(), 30)
        assert world.runtime.last_error is None
        assert len(reached) == 1, diagnostics
        assert world.sent == []  # MockTransport handler is the physical HTTP boundary.
        assert reached[0]['schema_version'] == 3
        assert reached[0]['procedure_drafts'][0]['memory_id'] == target[0]
        attempts = fixture.attempt_rows(world)
        assert len(attempts) == 1 and attempts[0]['unknown_class'] == 'not_sent'
        assert world.outcomes[-1].reason_code == 'closure_request_disclosure_rejected'
        assert world.outcomes[-1].provider_calls == 0
    finally:
        if world is not None:
            await world.runtime.close()
            await world.stack.close()
            await world.client.aclose()
        if memory is not None:
            await memory.close()
