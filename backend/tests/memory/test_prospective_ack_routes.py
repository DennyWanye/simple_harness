"""A7 ACK remains reachable after each real production Context route.

Actual Host tools + SQLite Harness + installed public Memory; HTTP transport is
deterministic. This does not claim a native UI reminder action or external event.
"""
import asyncio
import json
from functools import partial
from types import SimpleNamespace

import httpx
import pytest

from deskpet.execution.primary_dependencies import check_runtime_dependencies
from deskpet.memory.human_memory_service import CreateTaskScopeRequest, QueueTurnRequest
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime, HOST_SUPPORTED_FILTER_POLICIES
from deskpet.memory.history_source_authority import HostHistorySourceAuthority
from deskpet.memory.prospective_current_reader import PublicOccurrenceCurrentReader
from deskpet.memory.prospective_occurrence import ProspectiveOccurrenceCoordinator
from deskpet.memory.prospective_source_dependencies import ProspectiveSourceDependencies
from deskpet.memory.prospective_signal_store import ProspectiveSignalStore
from deskpet.memory.prospective_scheduler import ProspectiveScheduler
from deskpet.memory.prospective_time_source import PublicTimeAuthoritySource
from deskpet.memory.s5c_store import S5cStore
from deskpet.operation_audit.memory_attempts import MemoryAttemptJournal
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.sdk_adapters.prospective_ack import prospective_ack_registration
from deskpet.sdk_adapters.prospective_request_guard import ProspectiveRequestGuard
from deskpet.sdk_adapters.provider import ProductProviderAdapter
from tests.execution.test_primary_create_new_runtime import fixture
from tests.execution.test_primary_foreground_runtime import build, assert_exact_sdk_terminal_identity
from tests.memory import test_prospective_consumer_m617 as seed
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root
from tests.sdk_adapters.test_product_host_ports import Registry


@pytest.mark.asyncio
@pytest.mark.parametrize('route', ['direct_standalone','memory_standalone','continue_active','resume_existing','create_new'])
async def test_real_context_route_preserves_ack_through_terminal(tmp_path,monkeypatch,route):
    state,factory,service,configured,binding_authority=await fixture(tmp_path)
    primary=(await service.open_primary())['primary_ref']
    identity=HumanMemoryV7Runtime(tmp_path/'identity.db')
    principal=identity.principal()
    monkeypatch.setattr(seed,'P',principal)
    w=seed.World(tmp_path)
    w.filter_policies=HOST_SUPPORTED_FILTER_POLICIES
    w.history_options=dict(history_source_authority=HostHistorySourceAuthority(state))
    await w.setup();await w.mutate('a7-route-source');await w.consumer().run_once()
    w.clock[0]=30.
    store=S5cStore(state,principal);signals=ProspectiveSignalStore(state,principal)
    assert await ProspectiveScheduler(store=signals,source=PublicTimeAuthoritySource(registrations=store,signals=signals),
        memory=w,clock=lambda:w.clock[0]).tick(claim_owner='a7-route-timer')==1
    scope=None
    if route in {'continue_active','resume_existing'}:
        created=await service.create_task_scope(CreateTaskScopeRequest('a7-project','Project','Acknowledge reminder','a7-scope'))
        scope=created['scope_ref'];root=configured/'existing-task';root.mkdir()
        await bind_scope_root(state,scope,root)
    async def manager():return w.manager
    async def close():pass
    memory_runtime=SimpleNamespace(principal=lambda:principal,manager=manager,close=close,
        semantic_clock=lambda:w.clock[0],_clock=lambda:w.clock[0],
        operation_audit=MemoryAttemptJournal(tmp_path/'a7-memory-audit.db'))
    coordinator=ProspectiveOccurrenceCoordinator(store=store,clock=lambda:w.clock[0],
        read_current=PublicOccurrenceCurrentReader(store=store,runtime_getter=lambda:memory_runtime),
        source_dependencies=ProspectiveSourceDependencies(store=store,runtime_getter=lambda:memory_runtime))
    sends=[];route_results=[];ack_results=[];closed=False
    def physical(request):
        nonlocal closed
        body=json.loads(request.content);sends.append(body)
        assert 'prospective_ack' in {tool['function']['name'] for tool in body['tools']}
        values=[json.loads(item['content']) for item in body['messages'] if item['role']=='tool']
        groups=[json.loads(item['content']) for item in body['messages'] if isinstance(item.get('content'),str)
            and 'pending_prospective_occurrences' in item['content'] and item['role']=='system']
        name=None;args={}
        if len(sends)==1:
            name='context_route';args={'route':route}
            if route=='resume_existing':args['task_scope_id']=scope
            elif route=='create_new':args.update(title='New A7 project',goal='Acknowledge the actual reminder')
            elif route=='memory_standalone':args.update(query='unrelated semantic query',memory_types=['semantic'])
        elif len(sends)==2:
            value=values[-1]['value']
            assert value['context_route_receipt']['route']==route, value
            route_results.append(value)
            assert len(groups)==1 and groups[0]['count']==1
            name='prospective_ack';args={'occurrence_key':groups[0]['entries'][0]['occurrence_key']}
        else:
            if not ack_results:
                value=values[-1]['value'];assert value['state']=='acknowledged';ack_results.append(value)
            instructions=[json.loads(item['content']) for item in body['messages'] if item['role']=='system'
                and isinstance(item.get('content'),str) and '"task_scope_closure_required"' in item['content']]
            if instructions and not closed:
                instruction=instructions[-1];closed=True;name='task_scope_update'
                args=dict(outcome='no_mutation',base_revision=instruction['current_revision'],
                    closure_reason='Acknowledged reminder; task metadata unchanged.',
                    evidence_refs=instruction['allowed_evidence_refs'],idempotency_key='a7-route-close')
        message={'role':'assistant','content':'Reminder acknowledgement complete.'}
        if name:
            message['tool_calls']=[{'id':f'a7-route-call-{len(sends)}','type':'function',
                'function':{'name':name,'arguments':json.dumps(args)}}]
        return httpx.Response(200,json={'id':f'a7-route-{len(sends)}','model':'model-a',
            'choices':[{'message':message,'finish_reason':'tool_calls' if name else 'stop'}],
            'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
    client=httpx.AsyncClient(transport=httpx.MockTransport(physical))
    provider=ProductProviderAdapter(Registry('fixture-secret'),provider_id='relay',client=client,
        price_resolver=lambda *_:(1,1,'fixture-prices'))
    import main
    runtime=stack=None
    async def guard(request):
        current=await queue.current_snapshot(principal.actor_id)
        await check_runtime_dependencies(db_path=state,stack=stack,sdk_run_id=current.sdk_run_id,
            request=request,policy_factory=lambda _:runtime.history_policy,typed_use_authority=runtime.typed_use_authority)
        await ProspectiveRequestGuard(sdk_run_id=current.sdk_run_id,coordinator=coordinator,
            read_provider_context_use=stack.read_provider_context_use)(request)
        if route=='direct_standalone' and len(sends)==1:
            from dataclasses import replace
            from deskpet.memory.trusted_disclosure import TrustedDisclosureError
            foreign=replace(principal,actor_id='foreign-owner')
            foreign_store=S5cStore(state,foreign)
            foreign_runtime=SimpleNamespace(principal=lambda:foreign,manager=manager)
            foreign_coordinator=ProspectiveOccurrenceCoordinator(store=foreign_store,clock=lambda:w.clock[0],
                read_current=PublicOccurrenceCurrentReader(store=foreign_store,runtime_getter=lambda:foreign_runtime))
            entry=(await w.manager.read_occurrence_inbox(principal=principal)).entries[0]
            tool=prospective_ack_registration(coordinator=foreign_coordinator,
                context_getter=lambda:SimpleNamespace(run_id=SimpleNamespace(value=current.sdk_run_id)))
            async with store._transaction() as db:
                before=(await (await db.execute('SELECT COUNT(*) FROM prospective_occurrences')).fetchone())[0]
            with pytest.raises(TrustedDisclosureError,match='host_disclosure_turn_subject_mismatch'):
                await tool.handler({'occurrence_key':entry.occurrence_key},None)
            async with store._transaction() as db:
                after=(await (await db.execute('SELECT COUNT(*) FROM prospective_occurrences')).fetchone())[0]
            assert before==after
    provider._pre_invoke_guard=guard
    try:
        runtime,stack,queue=await build(tmp_path,state,provider,dynamic=True,binding_authority=binding_authority,
            configured_root=configured,visibility_memory=memory_runtime,context_use_memory=memory_runtime,
            recall_executor=partial(HumanMemoryV7Runtime.typed_recall,memory_runtime),
            occurrence_coordinator=coordinator,extra_registrations=(prospective_ack_registration(coordinator=coordinator),),
            candidate_identity=main.build_candidate_identity())
        await service.enqueue_turn(QueueTurnRequest(scope if route=='continue_active' else None,'a7-route-turn','Please handle the pending reminder.'))
        await runtime.after_enqueue(subject=principal.actor_id)
        await asyncio.wait_for(runtime.drain(),30)
        assert runtime.last_error is None
        assert len(route_results)==len(ack_results)==1
        assert await queue.current_snapshot(principal.actor_id) is None
        async with store._transaction() as db:
            counts=dict(await (await db.execute('SELECT phase,COUNT(*) FROM prospective_occurrences GROUP BY phase')).fetchall())
            row=await (await db.execute("SELECT sdk_run_id FROM prospective_occurrences WHERE phase='settled'")).fetchone()
        assert counts=={'claimed':1,'presented':1,'acknowledged':1,'settled':1}
        assert stack.read_run_terminal_evidence(row[0]).state=='completed'
        await assert_exact_sdk_terminal_identity(state,stack,primary)
    finally:
        if runtime is not None:await runtime.close()
        if stack is not None:await stack.close()
        await w.manager.close();await identity.close();await client.aclose()
