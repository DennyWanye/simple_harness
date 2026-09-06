"""A7 real Harness/SQLite Runs and public Memory inbox, deterministic provider only.

NOT native/user-visible acknowledgement evidence. No SDK SQL or fake terminal IDs.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from simple_harness import CallId
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from deskpet.memory.human_memory_service import HumanMemoryHostServiceFactory, QueueTurnRequest
from deskpet.memory.schema import dispatch_startup_epoch
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.memory.s5c_store import S5cStore
from deskpet.memory.prospective_signal_store import ProspectiveSignalStore
from deskpet.memory.prospective_scheduler import ProspectiveScheduler
from deskpet.memory.prospective_time_source import PublicTimeAuthoritySource
from deskpet.memory.prospective_current_reader import PublicOccurrenceCurrentReader
from deskpet.memory.prospective_occurrence import ProspectiveOccurrenceCoordinator
from deskpet.sdk_adapters.prospective_ack import prospective_ack_registration
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_foreground_runtime import build, Provider, assert_exact_sdk_terminal_identity
from tests.memory import test_prospective_consumer_m617 as seed


@pytest.mark.asyncio
@pytest.mark.parametrize("late_forget", [False, True])
async def test_real_four_runs_present_ack_terminal_reopen(tmp_path, monkeypatch, late_forget):
    await _run_occurrence_case(tmp_path,monkeypatch,late_forget=late_forget)


@pytest.mark.asyncio
@pytest.mark.parametrize("point", ['terminal.before_commit','terminal.after_commit'])
async def test_real_ack_terminal_commit_recovery(tmp_path,monkeypatch,point):
    await _run_occurrence_case(tmp_path,monkeypatch,terminal_fault=point)


@pytest.mark.asyncio
async def test_real_ack_history_inheritance_withdrawal(tmp_path,monkeypatch):
    await _run_occurrence_case(tmp_path,monkeypatch,inheritance=True)


@pytest.mark.asyncio
async def test_real_occurrence_source_withdrawal_and_foreign_authority(tmp_path,monkeypatch):
    await _run_occurrence_case(tmp_path,monkeypatch,late_forget='source')


async def _run_occurrence_case(tmp_path,monkeypatch,*,late_forget=False,terminal_fault=None,inheritance=False):
    state=tmp_path/'state.db'
    startup=await dispatch_startup_epoch(state,approved_fresh_lane=True)
    service=HumanMemoryHostServiceFactory(state,startup).bind(local_owner_auth())
    primary=(await service.open_primary())['primary_ref']
    visibility=HumanMemoryV7Runtime(tmp_path/'visibility-memory.db')
    principal=visibility.principal()
    # Only the explicit SDK seed fixture principal changes; production owner,
    # Run/disclosure reader and ACK ToolContext are not substituted.
    monkeypatch.setattr(seed,'P',principal)
    w=seed.World(tmp_path)
    from deskpet.memory.human_memory_v7 import HOST_SUPPORTED_FILTER_POLICIES
    from deskpet.memory.history_source_authority import HostHistorySourceAuthority
    w.filter_policies=HOST_SUPPORTED_FILTER_POLICIES
    w.history_options=dict(history_source_authority=HostHistorySourceAuthority(state))
    await w.setup();await w.mutate('a7-real-create');await w.consumer().run_once()
    w.clock[0]=30.
    signals=ProspectiveSignalStore(state,principal)
    store=S5cStore(state,principal)
    assert await ProspectiveScheduler(store=signals,
        source=PublicTimeAuthoritySource(registrations=store,signals=signals),
        memory=w,clock=lambda:w.clock[0]).tick(claim_owner='real-a7')==1
    async def manager():return w.manager
    current_runtime=SimpleNamespace(principal=lambda:principal,manager=manager,semantic_clock=time.time)
    if inheritance:
        await visibility.close()
        async def no_close():pass
        visibility=SimpleNamespace(principal=lambda:principal,manager=manager,close=no_close)
    from deskpet.memory.prospective_source_dependencies import ProspectiveSourceDependencies
    source_dependencies=ProspectiveSourceDependencies(store=store,runtime_getter=lambda:current_runtime)
    coordinator=ProspectiveOccurrenceCoordinator(store=store,
        read_current=PublicOccurrenceCurrentReader(store=store,runtime_getter=lambda:current_runtime),
        clock=lambda:w.clock[0],source_dependencies=source_dependencies)

    import httpx
    from deskpet.sdk_adapters.provider import ProductProviderAdapter
    from deskpet.sdk_adapters.prospective_request_guard import ProspectiveRequestGuard, ProspectiveRequestRejected
    from tests.sdk_adapters.test_product_host_ports import Registry
    sends=[]
    controls=SimpleNamespace(turn=0,ack_sent=False)
    def physical(request):
        body=json.loads(request.content);sends.append(body)
        groups=[json.loads(m['content']) for m in body['messages']
                if isinstance(m.get('content'),str) and 'pending_prospective_occurrences' in m['content']]
        message={'role':'assistant','content':('A7_DERIVED_REPORT_CANARY: send the report'
            if inheritance and controls.turn<=5 else 'Reminder noted')}
        if controls.turn<=3:
            assert len(groups)==1 and groups[0]['count']==1
            assert groups[0]['entries'][0]['overdue']==(controls.turn==3)
        elif not controls.ack_sent:
            assert groups[0]['entries'][0]['overdue'] is True
            controls.ack_sent=True
            message['tool_calls']=[{'id':'actual-a7-ack','type':'function','function':{
                'name':'prospective_ack','arguments':json.dumps({
                    'occurrence_key':groups[0]['entries'][0]['occurrence_key']})}}]
        return httpx.Response(200,json={'id':f'a7-{len(sends)}','model':'model-a',
            'choices':[{'message':message,'finish_reason':'tool_calls' if 'tool_calls' in message else 'stop'}],
            'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
    client=httpx.AsyncClient(transport=httpx.MockTransport(physical))
    provider=ProductProviderAdapter(Registry('fixture-secret'),provider_id='relay',client=client,
        price_resolver=lambda *_:(1,1,'fixture-prices'))
    guarded=[]
    late_denials=[]
    pre_forget_passed=[]
    async def guard(request):
        current=await queue.current_snapshot(principal.actor_id)
        if inheritance:
            from deskpet.execution.primary_dependencies import check_runtime_dependencies
            await check_runtime_dependencies(db_path=state,stack=stack,sdk_run_id=current.sdk_run_id,
                request=request,policy_factory=lambda _:runtime.history_policy,
                typed_use_authority=runtime.typed_use_authority)
        guarded.append((current.sdk_run_id,request.request_id.value))
        assert 'prospective_ack' in {tool.name for tool in request.tools}
        actual_guard=ProspectiveRequestGuard(sdk_run_id=current.sdk_run_id,coordinator=coordinator,
            read_provider_context_use=stack.read_provider_context_use)
        if late_forget:
            # Prove the same actual SDK handoff/group reaches the current-source
            # gate before changing only public Memory suppression.
            await actual_guard(request)
            pre_forget_passed.append(request.request_id.value)
            import simple_harness_memory as memory
            entry=(await w.manager.read_occurrence_inbox(principal=principal)).entries[0]
            if late_forget=='source':
                from dataclasses import replace
                from deskpet.memory.s5c_store import S5cConflict
                view=stack.read_provider_context_use(current.sdk_run_id,request.request_id.value)
                async with store._transaction() as db:
                    row=await (await db.execute('SELECT * FROM run_context_snapshot_receipts WHERE snapshot_id=?',
                        (view.context_snapshot_id,))).fetchone()
                group=await coordinator.restore_snapshot(sdk_run_id=current.sdk_run_id,
                    provider_turn_ordinal=row['provider_turn_ordinal'],prior_context_revision=row['prior_context_revision'],
                    snapshot_id=view.context_snapshot_id)
                bound=await source_dependencies.for_group(group,check_current=True)
                assert len(bound)==1
                wrong_runtime=SimpleNamespace(principal=lambda:replace(principal,actor_id='foreign-owner'),manager=manager)
                wrong=ProspectiveSourceDependencies(store=store,runtime_getter=lambda:wrong_runtime)
                with pytest.raises(S5cConflict,match='source_principal_differs'):
                    await wrong.for_group(group,check_current=True)
                with pytest.raises(S5cConflict,match='source_owner_differs'):
                    await source_dependencies.for_group(replace(group,owner='foreign'),check_current=True)
                # The correct actual source reached this exact receipt read.
                # A counterfeit public DTO must fail at receipt binding, not
                # merely at an unrelated missing capability or early owner gate.
                real_view=w.manager.get_memory_mutation_receipt_view
                reached=[]
                async def wrong_receipt(**kwargs):
                    result=await real_view(**kwargs);reached.append(result.receipt_id)
                    return replace(result,receipt_id='foreign-receipt')
                with monkeypatch.context() as patch:
                    patch.setattr(w.manager,'get_memory_mutation_receipt_view',wrong_receipt)
                    with pytest.raises(S5cConflict,match='source_receipt_differs'):
                        await source_dependencies.for_group(group,check_current=True)
                assert len(reached)==1
                await w.manager.suppress(principal=principal,request=memory.SuppressionRequest(
                    'a7-source-forget',principal.actor_id,memory.SuppressionScopeKind.EVIDENCE,
                    bound[0]['evidence_id'],'user_forget',w.clock[0]))
            else:
                await w.manager.suppress(principal=principal,request=memory.SuppressionRequest(
                    'a7-late-forget',principal.actor_id,memory.SuppressionScopeKind.MEMORY,
                    entry.memory_id,'user_forget',w.clock[0]))
        try:
            await actual_guard(request)
        except ProspectiveRequestRejected as error:
            late_denials.append(str(error.__cause__))
            raise
    provider._pre_invoke_guard=guard
    from deskpet.sdk_adapters.context_authority import ProductRuntimeDecisionSink, NoRecallBlockedError
    no_recall_denials=[]
    async def reconcile(keys):
        # Call the unchanged production predicate against the actual Manager.
        return await HumanMemoryV7Runtime.pending_occurrences(current_runtime,keys)
    class ObservedDecisionSink(ProductRuntimeDecisionSink):
        async def record_no_recall(self,**kwargs):
            try:return await super().record_no_recall(**kwargs)
            except NoRecallBlockedError as error:
                no_recall_denials.append((kwargs['run_id'].value,error.code,error.pending_count))
                raise
    def decision_sink(ledger):return ObservedDecisionSink(ledger=ledger,reconcile=reconcile)
    # The bounded installed launcher supplies the exact reviewed identity to
    # main's candidate builder; its real verifier checks bytes/version/origin.
    import main
    runtime=stack=None
    fault_fired=[]
    def fault(point):
        if controls.turn==4 and point==terminal_fault and not fault_fired:
            fault_fired.append(point)
            raise ConnectionError('a7 actual terminal commit interruption')
    try:
        for turn in range(1,2 if late_forget else 5):
            if runtime is None:
                runtime,stack,queue=await build(tmp_path,state,provider,
                    visibility_memory=visibility,occurrence_coordinator=coordinator,
                    extra_registrations=(prospective_ack_registration(coordinator=coordinator),),
                    candidate_identity=main.build_candidate_identity(),decision_sink_factory=decision_sink,context_use_memory=current_runtime,
                    fault=fault if terminal_fault else None)
            controls.turn=turn
            await service.enqueue_turn(QueueTurnRequest(None,f'a7-real-{turn}',f'Actual user turn {turn}'))
            await runtime.after_enqueue(subject=principal.actor_id)
            await asyncio.wait_for(runtime.drain(),20)
            if terminal_fault and turn==4:
                assert fault_fired==[terminal_fault]
                assert isinstance(runtime.last_error,ConnectionError)
                assert str(runtime.last_error)=='a7 actual terminal commit interruption'
                before_recovery=len(sends)
                raw_terminal=stack.read_run_terminal_evidence(guarded[-1][0])
                assert raw_terminal.state=='completed'
                async with store._transaction() as db:
                    phases=dict(await (await db.execute(
                        'SELECT phase,COUNT(*) FROM prospective_occurrences GROUP BY phase')).fetchall())
                    ack_before=await (await db.execute(
                        "SELECT record_id,record_hash,sdk_run_id FROM prospective_occurrences WHERE phase='acknowledged'")).fetchone()
                assert phases['acknowledged']==1
                assert phases.get('settled',0)==int(terminal_fault.endswith('after_commit'))
                await runtime.close();await stack.close();runtime=stack=None
                runtime,stack,queue=await build(tmp_path,state,provider,visibility_memory=visibility,
                    occurrence_coordinator=coordinator,
                    extra_registrations=(prospective_ack_registration(coordinator=coordinator),),
                    candidate_identity=main.build_candidate_identity(),decision_sink_factory=decision_sink,
                    context_use_memory=current_runtime)
                await runtime.after_enqueue(subject=principal.actor_id)
                await asyncio.wait_for(runtime.drain(),20)
                assert len(sends)==before_recovery
                recovered=stack.read_run_terminal_evidence(guarded[-1][0])
                assert (recovered.run_id,recovered.event_id,recovered.event_hash)==(
                    raw_terminal.run_id,raw_terminal.event_id,raw_terminal.event_hash)
                async with store._transaction() as db:
                    ack_after=await (await db.execute(
                        "SELECT record_id,record_hash,sdk_run_id FROM prospective_occurrences WHERE phase='acknowledged'")).fetchone()
                assert tuple(ack_after)==tuple(ack_before)
            if late_forget:
                assert len(guarded)==1 and sends==[]
                actual=stack.read_run_terminal_evidence(guarded[0][0])
                assert actual.state=='failed'
                assert pre_forget_passed==[guarded[0][1]]
                assert late_denials==[('s5c_occurrence_source_not_visible' if late_forget=='source'
                                      else 's5c_occurrence_current_read_changed')]
                assert runtime.last_error is None
                await runtime.after_enqueue(subject=principal.actor_id)
                await asyncio.wait_for(runtime.drain(),20)
                assert len(guarded)==1 and sends==[]
                async with store._transaction() as db:
                    assert (await (await db.execute('SELECT COUNT(*) FROM occurrence_presented')).fetchone())[0]==0
                return
            assert runtime.last_error is None
            assert await queue.current_snapshot(principal.actor_id) is None
            async with store._transaction() as db:
                rows=await (await db.execute('SELECT phase,COUNT(*) FROM prospective_occurrences GROUP BY phase')).fetchall()
                counts=dict(rows)
                exits=(await (await db.execute('SELECT COUNT(*) FROM occurrence_presented')).fetchone())[0]
            actual=stack.read_run_terminal_evidence(guarded[-1][0])
            assert actual.state==('failed' if turn<4 else 'completed')
            assert len(no_recall_denials)==min(turn,3)
            if turn<4:
                assert no_recall_denials[-1]==(guarded[-1][0],
                    'sdk_no_recall_blocked_pending_occurrence',1)
            assert counts['presented']==turn
            assert counts.get('overdue',0)==int(turn>=3)
            assert exits==int(turn==4)
            if turn==3:
                await runtime.close();await stack.close();runtime=stack=None
        assert counts=={'claimed':1,'presented':4,'overdue':1,'acknowledged':1,'settled':1}
        assert len(sends)==5
        await assert_exact_sdk_terminal_identity(state,stack,primary)
        async with store._transaction() as db:
            ack=await (await db.execute("SELECT sdk_run_id,inbox_json FROM prospective_occurrences WHERE phase='acknowledged'")).fetchone()
            terminal=await (await db.execute("SELECT sdk_run_id,inbox_json FROM prospective_occurrences WHERE phase='settled'")).fetchone()
        assert ack['sdk_run_id']==terminal['sdk_run_id']
        before=len(sends)
        receipt=await coordinator.ack(sdk_run_id=ack['sdk_run_id'],
            occurrence_key=json.loads(ack['inbox_json'])['entry']['occurrence_key'])
        assert receipt['state']=='acknowledged' and len(sends)==before
        await runtime.close();await stack.close();runtime=stack=None
        await w.manager.close();await w.open()
        coordinator=ProspectiveOccurrenceCoordinator(store=S5cStore(state,principal),
            read_current=PublicOccurrenceCurrentReader(store=S5cStore(state,principal),
                runtime_getter=lambda:current_runtime),clock=lambda:w.clock[0],source_dependencies=source_dependencies)
        runtime,stack,queue=await build(tmp_path,state,provider,visibility_memory=visibility,
            occurrence_coordinator=coordinator,
            extra_registrations=(prospective_ack_registration(coordinator=coordinator),),
            candidate_identity=main.build_candidate_identity(),decision_sink_factory=decision_sink,context_use_memory=current_runtime,
                    fault=fault if terminal_fault else None)
        await runtime.after_enqueue(subject=principal.actor_id)
        await asyncio.wait_for(runtime.drain(),20)
        assert runtime.last_error is None and len(sends)==before
        assert await coordinator.ack(sdk_run_id=ack['sdk_run_id'],
            occurrence_key=receipt['occurrence_key'])==receipt
        assert len(sends)==before

        if inheritance:
            from deskpet.execution.primary_history import PrimaryHistoryStore
            from deskpet.execution.primary_dependencies import current_disclosure
            import simple_harness_memory as memory
            canary='A7_DERIVED_REPORT_CANARY'
            controls.turn=5
            await service.enqueue_turn(QueueTurnRequest(None,'a7-derived-5','Continue the previous discussion.'))
            await runtime.after_enqueue(subject=principal.actor_id)
            await asyncio.wait_for(runtime.drain(),20)
            assert runtime.last_error is None
            assert len(sends)==before+1
            assert canary in json.dumps(sends[-1]['messages'])
            history=PrimaryHistoryStore(state,settled_run_reader=stack.read_settled_primary_run,policy=runtime.history_policy)
            async def read_history():
                return await history.read(subject=principal.actor_id,primary_ref=primary,before_sequence=100,
                    disclosure_context=current_disclosure(run_id='a7-history',subject=principal.actor_id,request_id='a7-history'))
            visible=await read_history()
            assert sum(canary in json.dumps(group) for group in visible)>=2
            memory_id=(await w.manager.read_occurrence_inbox(principal=principal)).entries[0].memory_id
            await w.manager.suppress(principal=principal,request=memory.SuppressionRequest(
                'a7-derived-forget',principal.actor_id,memory.SuppressionScopeKind.MEMORY,
                memory_id,'user_forget',w.clock[0]))
            assert canary not in json.dumps(await read_history())
            await w.manager.close();await w.open()
            controls.turn=6
            await service.enqueue_turn(QueueTurnRequest(None,'a7-derived-6','A fresh unrelated request.'))
            await runtime.after_enqueue(subject=principal.actor_id)
            await asyncio.wait_for(runtime.drain(),20)
            assert runtime.last_error is None and len(sends)==before+2
            assert canary not in json.dumps(sends[-1]['messages'])
            assert 'A fresh unrelated request.' in json.dumps(sends[-1]['messages'])

    finally:
        if runtime is not None:await runtime.close()
        if stack is not None:await stack.close()
        await visibility.close();await w.manager.close();await client.aclose()
