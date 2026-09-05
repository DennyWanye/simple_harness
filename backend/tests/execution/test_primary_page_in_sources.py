"""A real unscoped page-in result cannot escape the current-run source guard."""
import asyncio
import sqlite3
import pytest
from simple_harness import CallId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.tools.context_page_in_tools import ContextPageInStore
from tests.execution.test_primary_create_new_runtime import fixture
from tests.execution.test_primary_foreground_runtime import build, Provider
from tests.execution.test_scope_disclosure_runtime import install_guard


@pytest.mark.asyncio
async def test_unscoped_actual_page_in_without_source_proof_denies_next_provider(tmp_path):
    state, _, service, configured, authority = await fixture(tmp_path)
    store = ContextPageInStore()
    sdk_id = None
    class PageProvider(Provider):
        async def invoke(self, request, *, cancel):
            nonlocal sdk_id
            self.requests.append(request)
            current = await queue.current_snapshot(local_owner_auth().subject)
            sdk_id = current.sdk_run_id
            execution = runtime._tools._registry.resolve(sdk_id).execution_context()
            reference = store.put(kind='project', source='original-project-view',
                content='UNPROVED_PAGE_SOURCE', session_id=execution.session_id,
                request_id=execution.request_id, scope_id=execution.scope_id)
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, 'Read exact page'),
                tool_calls=(ProviderToolCall(CallId('page-source'), 'context_page_in',
                    {'reference_id':reference.reference_id, 'source_hash':reference.source_hash}),),
                model='model', usage=ProviderUsage(10,10,20))
    provider = PageProvider()
    await service.enqueue_turn(QueueTurnRequest(None, 'page-source', 'Read my prior project'))
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured, page_in_store=store)
    install_guard(provider, runtime, stack, queue)
    try:
        assert await asyncio.wait_for(runtime._drive_once(), 20)
        assert len(provider.requests) == 1
        with sqlite3.connect(state) as db:
            effect_id = db.execute("SELECT effect_id FROM primary_effect_identities WHERE sdk_run_id=? AND tool_name='context_page_in'", (sdk_id,)).fetchone()[0]
            assert db.execute("SELECT count(*) FROM harness_evidence_reservations WHERE run_id=?", (sdk_id,)).fetchone()[0] == 0
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts ORDER BY rowid DESC LIMIT 1").fetchone()[0] == 'FAILED'
        _, (effect,) = stack.read_primary_dependency_facts(sdk_id, (effect_id,))
        assert effect.terminal and effect.result.outcome.value == 'succeeded'
        assert thaw_json(effect.result.value)['content'] == 'UNPROVED_PAGE_SOURCE'
        assert not await runtime._drive_once()
        assert len(provider.requests) == 1
    finally:
        await runtime.close()
        await stack.close()
