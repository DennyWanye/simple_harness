import asyncio,json,sqlite3,time
import pytest
from simple_harness import CallId
from simple_harness.contracts.messages import Message,MessageRole
from simple_harness.providers import ProviderResponse,ProviderToolCall,ProviderUsage
from simple_harness_memory import SuppressionRequest,SuppressionScopeKind
from deskpet.memory.human_memory_service import QueueTurnRequest
from deskpet.memory.human_memory_v7 import HumanMemoryV7Runtime
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.sdk_adapters.context_route import local_owner_auth
from deskpet.execution.primary_dependencies import check_runtime_dependencies
from tests.execution.test_primary_create_new_runtime import fixture,CreateProvider
from tests.execution.test_primary_foreground_runtime import build,Provider
from tests.execution.test_scope_disclosure_runtime import install_guard

@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["forget", "complete", "create"])
async def test_unscoped_search_rechecks_forgotten_source_before_second_delegate(tmp_path, mode):
    state,_,service,configured,authority=await fixture(tmp_path)
    creator=CreateProvider()
    await service.enqueue_turn(QueueTurnRequest(None,'create-origin','Create Fresh project and write output'))
    runtime,stack,queue=await build(tmp_path,state,creator,dynamic=True,binding_authority=authority,configured_root=configured)
    install_guard(creator,runtime,stack,queue)
    try:
        assert await runtime._drive_once()
        assert len(creator.requests)==7
    finally:
        await runtime.close();await stack.close()
    with sqlite3.connect(state) as db:
        source_id=db.execute('SELECT evidence_id FROM foreground_turns ORDER BY enqueue_sequence LIMIT 1').fetchone()[0]
    visibility=HumanMemoryV7Runtime(tmp_path/'visibility-memory.db',evidence_authority=HostEvidenceAuthority(state))
    manager=await visibility.manager()
    async def hide_terminal(index):
        with sqlite3.connect(state) as db:
            terminal_id=db.execute("SELECT evidence_id FROM human_memory_evidence WHERE source_ref LIKE 'primary-runtime:%' ORDER BY committed_at DESC LIMIT 1").fetchone()[0]
        await manager.backend.suppress(SuppressionRequest(f'hide-assistant-{index}',local_owner_auth().subject,SuppressionScopeKind.EVIDENCE,terminal_id,'user_forget',time.time()),principal=visibility.principal())
    await hide_terminal(0)
    # Legitimately forget generated responses, while independent USER history remains.
    # Ten later real turns put the creator USER outside the current latest-ten window.
    filler=Provider()
    runtime,stack,queue=await build(tmp_path,state,filler,visibility_memory=visibility)
    install_guard(filler,runtime,stack,queue)
    try:
        for n in range(10):
            await service.enqueue_turn(QueueTurnRequest(None,f'ordinary-{n}',f'Independent current fact {n}'))
            assert await runtime._drive_once()
            assert runtime.last_error is None
            await hide_terminal(n+1)
        assert len(filler.requests)==10
    finally:
        await runtime.close();await stack.close()
    created = CreateProvider()
    class SearchProvider(Provider):
        async def invoke(self,request,*,cancel):
            self.requests.append(request)
            if len(self.requests)==1:
                return ProviderResponse(request.request_id,Message(MessageRole.ASSISTANT,'Search prior scope'),tool_calls=(ProviderToolCall(CallId('old-scope-search'),'task_scope_search',{'query':'Fresh'}),),model='model',usage=ProviderUsage(10,10,20))
            if mode == 'create':
                return await created.invoke(request, cancel=cancel)
            return ProviderResponse(request.request_id,Message(MessageRole.ASSISTANT,'Found the prior scope'),model='model',usage=ProviderUsage(10,10,20))
    consumer=SearchProvider();candidate_visible=False;search_reservations=None;sdk_id=None
    await service.enqueue_turn(QueueTurnRequest(None,'search-old-scope','Find my prior project'))
    runtime,stack,queue=await build(tmp_path,state,consumer,dynamic=True,binding_authority=authority,configured_root=configured,visibility_memory=visibility)
    original=consumer.invoke
    async def guarded(request,*,cancel):
        nonlocal candidate_visible,search_reservations,sdk_id
        current=await queue.current_snapshot(local_owner_auth().subject)
        sdk_id=current.sdk_run_id
        if consumer.requests:
            candidate_visible=any(m.role.value=='tool' and m.name=='task_scope_search' and 'Fresh project' in m.content for m in request.messages)
            with sqlite3.connect(state) as db:
                search_reservations=db.execute("SELECT count(*) FROM harness_evidence_reservations WHERE run_id=? AND tool_name='task_scope_search'",(current.sdk_run_id,)).fetchone()[0]
            # Current source revocation occurs after search returned but before final outbound check.
            if mode == 'forget':
                await manager.backend.suppress(SuppressionRequest('forget-searched-source',local_owner_auth().subject,SuppressionScopeKind.EVIDENCE,source_id,'user_forget',time.time()),principal=visibility.principal())
        await check_runtime_dependencies(db_path=state,stack=stack,sdk_run_id=current.sdk_run_id,request=request,policy_factory=lambda _:runtime.history_policy)
        return await original(request,cancel=cancel)
    consumer.invoke=guarded
    try:
        assert await asyncio.wait_for(runtime._drive_once(),30)
        assert candidate_visible
        print(json.dumps({'source_revoked_before_final_guard':True,'actual_search_returned_source_text':candidate_visible,'search_reservations':search_reservations,'consumer_delegate_calls':len(consumer.requests)}))
        assert len(consumer.requests) == {"forget":1,"complete":2,"create":8}[mode]
        with sqlite3.connect(state) as db:
            assert db.execute("SELECT terminal_state FROM foreground_terminal_receipts ORDER BY rowid DESC LIMIT 1").fetchone()[0] == ("FAILED" if mode == "forget" else "COMPLETED")
            indexed = db.execute("SELECT effect_id FROM primary_effect_identities WHERE sdk_run_id=? AND tool_name='task_scope_search'", (sdk_id,)).fetchall()
            assert len(indexed) == 1
        assert search_reservations == 0 or mode == "create"
        if mode == "create":
            assert len(list(configured.glob("*/fresh.txt"))) == 2
            with sqlite3.connect(state) as db:
                raw = json.loads(db.execute("SELECT receipt_json FROM context_route_decisions WHERE sdk_run_id=? AND route='create_new'", (sdk_id,)).fetchone()[0])
            _, (effect,) = stack.read_primary_dependency_facts(sdk_id, (raw['effect_id'],))
            from simple_harness import thaw_json
            proof = thaw_json(effect.result.value)['producer_dependencies']
            assert source_id in {row['evidence_id'] for row in proof['evidence']}
        assert not await runtime._drive_once()
        assert len(consumer.requests) == {"forget":1,"complete":2,"create":8}[mode]
    finally:
        await runtime.close();await stack.close();await visibility.close()
