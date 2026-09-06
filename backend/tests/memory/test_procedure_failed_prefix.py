"""A real failed physical prefix does not prove Procedure defect or success."""
import asyncio
import json
from dataclasses import replace
import pytest
import simple_harness as h
from simple_harness.providers import ProviderResponse, ProviderToolCall, ProviderUsage
from simple_harness.contracts.messages import Message, MessageRole
from deskpet.memory.human_memory_service import QueueTurnRequest, CreateTaskScopeRequest
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.short_indexing import PrimaryShortIndexingService
from deskpet.memory.procedure_applicability import ProcedureUseRejected
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.memory.test_procedure_scope_runtime import UseProvider
from tests.memory.test_procedure_recovery_runtime import session
from tests.sdk_adapters.s5b_effect_gate_harness import bind_scope_root

class FailedPrefixProvider(UseProvider):
    async def invoke(self,request,*,cancel):
        if self.stage==6:
            # The first physical write returned an error; do not invent step 2.
            self.stage=8
            self.requests.append(request)
            instruction=next(json.loads(m.content) for m in request.messages if m.role.value=='system'
                and isinstance(m.content,str) and '"task_scope_closure_required"' in m.content)
            return ProviderResponse(request.request_id,Message(MessageRole.ASSISTANT,'首步失败，保留实际错误事实'),
                tool_calls=(ProviderToolCall(h.CallId('close-failed-prefix'),'task_scope_update',{
                    'outcome':'no_mutation','base_revision':instruction['current_revision'],
                    'closure_reason':'The first write failed; step two was not attempted. Task metadata unchanged.',
                    'evidence_refs':instruction['allowed_evidence_refs'],'idempotency_key':'failed-prefix-close'}),),
                model='model',usage=ProviderUsage(10,10,20))
        if self.stage==8:
            self.stage=9
            self.requests.append(request)
            return ProviderResponse(request.request_id,Message(MessageRole.ASSISTANT,'首步写入失败，未执行备份。'),
                model='model',usage=ProviderUsage(10,10,20))
        return await super().invoke(request,cancel=cancel)


@pytest.mark.asyncio
async def test_real_failed_step_prefix_records_unattributed_failure_and_rejects_forged_cause(tmp_path):
    provider=FailedPrefixProvider()
    async with session(tmp_path,provider) as ctx:
        scope=await ctx.service.create_task_scope(CreateTaskScopeRequest('failed-scope','写记录','write','failed-create'))
        await bind_scope_root(ctx.state,scope['scope_ref'],ctx.root,tag='failed-root')
        (ctx.root/'record-1.txt').mkdir()  # Real filesystem conflict, no SDK row/receipt mutation.
        provider.configure(scope['scope_ref'],ctx.memory_id,ctx.revision,1)
        await ctx.service.enqueue_turn(QueueTurnRequest(None,'failed-turn','执行记录和备份两步。'))
        await ctx.runtime.after_enqueue(subject=local_owner_auth().subject)
        await asyncio.wait_for(ctx.runtime.drain(),30)
        assert ctx.runtime.last_error is None
        assert not (ctx.root/'backup-1.txt').exists()
        assert await MemoryIngestionOutboxWorker(ctx.state,ctx.memory.manager,owner_id='failed-outbox').run_once()=='delivered'
        authority=ctx.memory.conversation_evidence_authority
        ids=await authority.completed_run_ids(); assert len(ids)==1
        group=await authority.registrations_for_run(ids[0]); manager=await ctx.memory.manager()
        await PrimaryShortIndexingService(authority,manager=manager,principal=ctx.memory.principal()).register_group(group)
        runtime=ctx.memory.procedure_runtime
        use=await runtime.store.use_for_run(group.terminal_source[0].run_id)
        verified=await runtime._step_sources(use,group)
        assert len(verified)==1 and verified[0][1]['state']=='failed'
        await runtime.observe_group(group,manager)
        prepared=await runtime.store.prepared(use['use_id'])
        applied=await runtime.store.journal(use['use_id'],'applied')
        assert applied['result']['independent_successes']==0
        assert applied['result']['lifecycle_state']=='draft'
        original=h.ProcedureObservationAuthority.from_json(prepared['authority'])
        assert original.intent.outcome is h.ProcedureObservationOutcome.FAILURE
        assert original.intent.attributable is False
        forged=replace(original,intent=replace(original.intent,attributable=True,transition_to=h.ProcedureLifecycleState.REVISED))
        forged_prepared={**prepared,'authority':forged.to_json()}
        # Bypass no grant check: the real source verifier itself must reject a
        # falsely promoted attribution even if every actual terminal is retained.
        forged_prepared.pop('preparation')
        with pytest.raises(ProcedureUseRejected,match='attribution_unproved'):
            await runtime.verify_observation_source(use,forged_prepared,forged)
        await runtime.observe_group(group,manager)
        assert await runtime.store.journal(use['use_id'],'applied')==applied
