"""New, NOT_RUN controls: real installed pending/effect facts, no model or HTTP.

Unavailable-reader negatives wrap an actual public fact; no SDK database writes.
Actual-main HTTP/dispatcher consumption remains main's separate first batch.
"""
import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace

import aiosqlite
import pytest
from simple_harness import CallId, thaw_json
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from deskpet.quality.corpus_c05 import SETUPS, compile_c05_setup, operational_text
from deskpet.quality.corpus_c05_prepare import TaskSetupProvider
from deskpet.quality.corpus_c05_approval import C05SetupApproval, verify_pending_call
from deskpet.quality.corpus_c05_runtime import read_candidate_events
from deskpet.quality.corpus_approval import CorpusApprovalBlocked
from deskpet.quality.corpus_trace import digest
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.sdk_adapters.context_authority import ContextRouteLedgerStore
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.task_scope.workspace_bindings import WorkspaceBindingAuthorityStore
from tests.execution.test_primary_create_new_runtime import fixture
from tests.execution.test_primary_foreground_runtime import Provider, build
from tests.quality.test_corpus_c05_prepare import installed_candidate_identity


@pytest.mark.asyncio
async def test_actual_pending_approval_rejects_foreign_turn_and_wrong_args_before_allow(tmp_path, installed_candidate_identity):
    from deskpet.memory.control_binding import HumanMemoryControlBinding
    from deskpet.permissions.runtime import PreparedAuthorizationRuntime
    from deskpet.product_state.authorization_saga import AuthorizationSagaRepository
    from deskpet.product_state.database import ProductStateDatabase
    from deskpet.product_state.task_grants import DurableTaskGrantAuthority
    from deskpet.sdk_adapters.authorization import ProductAuthorizationAdapter
    from deskpet.sdk_adapters.tool_authority import SdkPreparedAuthorizationPolicy
    from tests.companion.test_window_control_credentials import _ingress
    from tests.memory.test_primary_control_binding import _bind
    state, factory, service, configured, authority = await fixture(tmp_path)
    product = ProductStateDatabase(tmp_path / 'product.db')
    product.initialize()
    mode = await authority._policy.get_policy_state()

    def authorization(registry):
        policy = SdkPreparedAuthorizationPolicy(PreparedAuthorizationRuntime(authority._policy),
            registry, initial_policy_generation=mode.generation, clock=time.time)
        return ProductAuthorizationAdapter(AuthorizationSagaRepository(product, owner_id='c05-control'),
            policy=policy, identity_factory=policy.identity_factory,
            grant_authority=DurableTaskGrantAuthority(product,
                policy_generation_provider=policy.current_policy_generation), grant_factory=policy.grant_factory)

    transport = SimpleNamespace(queued=None, planned_calls={})
    class RecordingSetup(TaskSetupProvider):
        async def invoke(self, request, *, cancel):
            result = await super().invoke(request, cancel=cancel)
            # Record the actual deterministic Provider's emitted raw calls,
            # independently of later SDK decision/internal IDs. No fake wire.
            for call in result.tool_calls:
                transport.planned_calls[call.call_id.value] = dict(name=call.name,
                    arguments=thaw_json(call.arguments), turn_ref=transport.queued['turn_ref'],
                    wire_request_hash=digest(dict(request_id=request.request_id.value,
                        messages=[m.to_dict() for m in request.messages])))
            return result
    provider = RecordingSetup(target=Provider.target)
    runtime, stack, queue = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured, authorization_factory=authorization,
        candidate_identity=installed_candidate_identity)
    try:
        private, _, _, control = _ingress(tmp_path / 'control')
        connection = HumanMemoryControlBinding()
        challenge = await _bind(private, control, connection)
        auth = connection.authenticate(control, challenge)
        manager = await runtime.history_memory.manager()
        factory = replace(factory, decision_ingress_getter=lambda: runtime._ingress,
            history_visibility_checker=runtime.history_policy.checker,
            run_binding_reader=lambda rid: stack.read_closure_run_facts(rid).binding_record,
            suppression_resolver=manager.backend.resolve_suppression)
        batch = compile_c05_setup('C05-20', SETUPS['C05-20'][0])
        provider.arm(batch, 'A')
        queued = await service.enqueue_turn(QueueTurnRequest(None, 'c05-pending-control', operational_text(batch, 'A')))
        transport.queued = queued
        await asyncio.wait_for(runtime._drive_once(), 10)
        current = await queue.current_snapshot(auth.subject)
        assert runtime._ingress.query(current.sdk_run_id).state.value == 'waiting'
        records = []
        approval = C05SetupApproval(ingress=runtime._ingress, stack=stack, transport=transport,
            ledger=ContextRouteLedgerStore(state),
            binding_store=WorkspaceBindingAuthorityStore(state, configured_workspace_root=configured),
            expected_configured_root=configured, persist=lambda name, fact: records.append((name, fact)))
        with connection.request_scope(control, challenge):
            bound = factory.bind(auth)
            target = dict(primary_ref=current.primary_conversation_id,
                expected_run_ref=current.host_run_id, expected_generation=current.generation)
            pending = (await bound.list_primary_decisions(request_id='c05-original-pending', **target))['pending']
            assert len(pending) == 1
            decision_id = pending[0]['decision_id']
            original = runtime._ingress.read_authorization_decision(run_id=current.sdk_run_id, decision_id=decision_id)
            _, (effect,) = stack.read_primary_dependency_facts(current.sdk_run_id,
                (thaw_json(original.request)['effect_id'],))
            assert effect is None  # real REQUIRE_USER precedes prepare_effect
            proof = await verify_pending_call(stack=stack, ingress=runtime._ingress,
                sdk_run_id=current.sdk_run_id, decision_id=decision_id, request=thaw_json(original.request))
            assert proof.internal_call_id != proof.raw_call_id
            assert proof.raw_call_id in transport.planned_calls
            # Wrong queue cannot borrow this still-open decision.
            foreign_queue = dict(queued, turn_ref='foreign-turn')
            transport.queued = foreign_queue
            with pytest.raises(CorpusApprovalBlocked, match='c05_setup_exact_identity_differs'):
                await approval(service=bound, queued=foreign_queue)
            transport.queued = queued
            raw = proof.raw_call_id
            actual_plan = transport.planned_calls[raw]
            transport.planned_calls[raw] = dict(actual_plan, arguments=dict(actual_plan['arguments'], title='unemitted title'))
            with pytest.raises(CorpusApprovalBlocked, match='c05_setup_exact_action_differs'):
                await approval(service=bound, queued=queued)
            assert records == []
            assert runtime._ingress.read_authorization_decision(run_id=current.sdk_run_id, decision_id=decision_id) == original
            transport.planned_calls[raw] = actual_plan
            assert await approval(service=bound, queued=queued) is True
            assert len(records) == 2
            assert records[-1][1]['response']['outcome'] == 'allowed'
            assert records[-1][1]['internal_call_id'] == proof.internal_call_id
            assert records[-1][1]['raw_call_id'] == proof.raw_call_id
    finally:
        await runtime.close()
        await stack.close()
        product.connection.close()


@pytest.mark.asyncio
async def test_real_empty_search_differs_from_missing_effect_and_terminal_result(tmp_path, installed_candidate_identity):
    class EmptySearch(Provider):
        async def invoke(self, request, *, cancel):
            self.requests.append(request)
            if len(self.requests) == 1:
                return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, 'search'),
                    tool_calls=(ProviderToolCall(CallId('actual-empty-search'), 'task_scope_search',
                        {'query': 'synthetic-absent-task'}),), model='model', usage=ProviderUsage(0, 0, 0))
            return ProviderResponse(request.request_id, Message(MessageRole.ASSISTANT, 'No matches.'),
                model='model', usage=ProviderUsage(0, 0, 0))
    state, _, service, configured, authority = await fixture(tmp_path)
    provider = EmptySearch()
    runtime, stack, _ = await build(tmp_path, state, provider, dynamic=True,
        binding_authority=authority, configured_root=configured, candidate_identity=installed_candidate_identity)
    try:
        queued = await service.enqueue_turn(QueueTurnRequest(None, 'c05-empty-source-control', 'Search for the absent task.'))
        await asyncio.wait_for(runtime._drive_once(), 10)
        async with aiosqlite.connect(state) as db:
            async with db.execute('SELECT b.sdk_run_id FROM foreground_run_sdk_bindings b JOIN foreground_runs r '
                                  'ON r.host_run_id=b.host_run_id WHERE r.turn_id=?', (queued['turn_ref'],)) as cursor:
                sdk = (await cursor.fetchone())[0]
            async with db.execute("SELECT effect_id FROM primary_effect_identities WHERE sdk_run_id=? AND tool_name='task_scope_search'", (sdk,)) as cursor:
                ids = [row[0] for row in await cursor.fetchall()]
        assert len(ids) == 1  # no-index zero must not mask the counterexamples
        start, (actual,) = stack.read_primary_dependency_facts(sdk, ids)
        assert actual.terminal and actual.result.outcome.value == 'succeeded'
        assert thaw_json(actual.result.value)['candidates'] == []
        common = dict(path=state, subject=local_owner_auth().subject, sdk_run_id=sdk, policy=runtime.history_policy)
        assert await read_candidate_events(stack=stack, **common) == ()
        class MissingResult:
            result = None
            def __getattr__(self, key):
                return getattr(actual, key)
        for replacement, code in ((None, 'c05_indexed_effect_missing'),
                                  (MissingResult(), 'c05_terminal_effect_result_missing')):
            class UnavailableRead:
                def read_primary_dependency_facts(self, run_id, effect_ids=()):
                    assert run_id == sdk and tuple(effect_ids) == tuple(ids)
                    return start, (replacement,)
            with pytest.raises(ValueError, match=code):
                await read_candidate_events(stack=UnavailableRead(), **common)
        # Original durable public record remains intact; only the read port was unavailable.
        assert stack.read_primary_dependency_facts(sdk, ids) == (start, (actual,))
    finally:
        await runtime.close()
        await stack.close()
