"""New real tool discovery and next Provider visibility boundary; no model calls."""
import asyncio
import json
import pytest
import simple_harness as h
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from simple_harness.contracts.messages import Message, MessageRole
from simple_harness_memory import SuppressionRequest, SuppressionScopeKind, HistoryProcedureDraftBinding, ProcedureDraftCandidate
from deskpet.execution.foreground_queue import ForegroundQueueStore
from deskpet.execution.primary_dependencies import check_runtime_dependencies, PrimaryHistoryDisclosureRejected
from deskpet.memory.trusted_disclosure import resolve_current_disclosure
from deskpet.memory.human_memory_service import QueueTurnRequest, CreateTaskScopeRequest
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.memory.test_procedure_scope_runtime import UseProvider
from tests.memory.test_procedure_recovery_runtime import session
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root


class DiscoveryProvider(UseProvider):
    discovered=False
    selected=None
    async def invoke(self,request,*,cancel):
        if self.stage==1 and not self.discovered:
            self.discovered=True
            self.requests.append(request)
            assert self.memory_id==''  # No fixture target id reaches this provider.
            return ProviderResponse(request.request_id,Message(MessageRole.ASSISTANT,'查找待验证的记录流程'),
                tool_calls=(ProviderToolCall(h.CallId('discover-first-draft'),'procedure_discover',{'query':'记录','after':''}),),
                model='model',usage=ProviderUsage(10,10,20))
        if self.stage==1 and self.discovered:
            result=json.loads([m.content for m in request.messages if m.role.value=='tool'][-1])['value']
            assert result['execution_authorized'] is False
            self.selected=result['candidates'][0]['candidate']
            self.memory_id,self.revision=self.selected['memory_id'],self.selected['revision']
        if self.stage==4:
            self.stage+=1
            self.requests.append(request)
            # Bind returned verbatim steps, not the fixture's hidden STEPS constant.
            arguments=[{'path':'record-1.txt','content':'actual record'},
                       {'path':'backup-1.txt','content':'actual record'}]
            return ProviderResponse(request.request_id,Message(MessageRole.ASSISTANT,'绑定发现的原步骤'),
                tool_calls=(ProviderToolCall(h.CallId('use-discovered-draft'),'procedure_use',{
                    'memory_id':self.memory_id,'revision':self.revision,
                    'steps':[{'text':text,'tool':'write_file','arguments_json':json.dumps(args)}
                             for text,args in zip(self.selected['steps'],arguments,strict=True)]}),),
                model='model',usage=ProviderUsage(10,10,20))
        return await super().invoke(request,cancel=cancel)


@pytest.mark.asyncio
@pytest.mark.parametrize('forget_before_followup',[False,True])
async def test_first_draft_is_discovered_without_known_id_and_next_send_rechecks_source(tmp_path,monkeypatch,forget_before_followup):
    provider=DiscoveryProvider()
    forget_decisions=[]
    source_rejections=[]
    async with session(tmp_path,provider,with_discovery=True) as ctx:
        scope=await ctx.service.create_task_scope(CreateTaskScopeRequest('discover-scope','写记录','write','discover-create'))
        await bind_scope_root(ctx.state,scope['scope_ref'],ctx.root,tag='discover-root')
        provider.configure(scope['scope_ref'],'',0,1)
        if forget_before_followup:
            invoke=provider.invoke
            async def guarded_invoke(request, *, cancel):
                current=await ForegroundQueueStore(ctx.state).current_snapshot(local_owner_auth().subject)
                try:
                    await check_runtime_dependencies(db_path=ctx.state,stack=ctx.stack,
                        sdk_run_id=current.sdk_run_id,request=request,policy_factory=lambda _:ctx.runtime.history_policy)
                except PrimaryHistoryDisclosureRejected as error:
                    source_rejections.append(error.error_code)
                    raise
                return await invoke(request,cancel=cancel)
            monkeypatch.setattr(provider,'invoke',guarded_invoke)
            discover=ctx.memory.procedure_runtime.discover
            async def then_forget(arguments,context):
                result=await discover(arguments,context)
                assert len(result['candidates'])==1
                candidate=result['candidates'][0]['candidate']
                manager=await ctx.memory.manager()
                decision=await manager.suppress(principal=ctx.memory.principal(),request=SuppressionRequest('forget-before-send',
                    ctx.memory.principal().actor_id,SuppressionScopeKind.MEMORY,candidate['memory_id'],'user_forget',ctx.memory.semantic_clock()))
                assert decision.scope_ref==candidate['memory_id'] and decision.request_id=='forget-before-send'
                disclosure=await resolve_current_disclosure(db_path=ctx.state,subject=ctx.memory.principal().actor_id,
                    run_id=context.run_id.value,request_id=context.request_id.value)
                selected=ProcedureDraftCandidate.from_json(candidate)
                visible=await manager.check_history_visibility(principal=ctx.memory.principal(),
                    disclosure_context=disclosure,bindings=(HistoryProcedureDraftBinding(
                        selected.memory_id,selected.revision,selected.source_hash),))
                assert visible.items[0].visible is False
                forget_decisions.append(decision)
                return result
            monkeypatch.setattr(ctx.memory.procedure_runtime,'discover',then_forget)
        await ctx.service.enqueue_turn(QueueTurnRequest(None,'discover-turn','查找记录流程并执行两步。'))
        await ctx.runtime.after_enqueue(subject=local_owner_auth().subject)
        await asyncio.wait_for(ctx.runtime.drain(),30)
        if forget_before_followup:
            assert len(forget_decisions)==1
            assert source_rejections==['primary_history_disclosure_rejected']
            assert len(provider.requests)==2 and provider.selected is None
            assert not (ctx.root/'record-1.txt').exists()
            return
        assert ctx.runtime.last_error is None
        assert provider.selected['memory_id']==ctx.memory_id
        assert (ctx.root/'record-1.txt').read_text()=='actual record'
        assert (ctx.root/'backup-1.txt').read_text()=='actual record'
        assert await MemoryIngestionOutboxWorker(ctx.state,ctx.memory.manager,owner_id='discovery-outbox').run_once()=='delivered'
        authority=ctx.memory.conversation_evidence_authority
        groups=await authority.completed_run_ids(); assert len(groups)==1
        group=await authority.registrations_for_run(groups[0])
        proof=group.terminal_source[0].sanitized_payload['visibility_dependencies']
        assert proof['schema_version']==3
        selected=ProcedureDraftCandidate.from_json(provider.selected)
        assert list(proof['procedure_drafts'])==[{
            'memory_id':selected.memory_id,'revision':selected.revision,'candidate_hash':selected.source_hash}]
        manager=await ctx.memory.manager()
        await PrimaryShortIndexingService(authority,manager=manager,principal=ctx.memory.principal()).register_group(group)
        await ctx.memory.procedure_runtime.observe_group(group,manager)
        use=await ctx.memory.procedure_runtime.store.use_for_run(group.terminal_source[0].run_id)
        result=(await ctx.memory.procedure_runtime.store.journal(use['use_id'],'applied'))['result']
        assert result['independent_successes']==1 and result['lifecycle_state']=='draft'
