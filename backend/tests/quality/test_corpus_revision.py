"""New revision/suppression public controls; no old C01/graph green replay."""
from dataclasses import replace
import pytest
import simple_harness as h
import simple_harness_memory as m
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.quality.corpus_c01 import SETUPS
from deskpet.quality.corpus_revision import CorpusRevisionAuthority,apply_c01_06
from tests.memory.test_primary_read_api import setup,result,AUTH
from tests.memory.test_primary_visibility import pairs,classification_policy,FILTERS


@pytest.mark.asyncio
async def test_c01_06_public_revision_authority_reopen_and_memory_suppression(tmp_path):
    f=await setup(tmp_path/'host')
    result(await f.send('queue.enqueue',{'text':SETUPS['C01-06'][0]},key='revision-setup'))
    envelope,receipt=(await pairs(f))[0]
    principal=m.MemoryPrincipal('host','household',AUTH.subject,'fixture')
    clock=[1788660000.0]
    manager=None
    authority=CorpusRevisionAuthority(f.path,principal=principal,manager_getter=lambda:manager,clock=lambda:clock[0])
    kwargs=dict(classification_policy=classification_policy(),supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(f.path),memory_action_authority=authority,clock=lambda:clock[0])
    manager=await m.build_human_memory_v7(tmp_path/'memory.db',**kwargs)
    try:
        actual=await apply_c01_06(manager=manager,principal=principal,authority=authority,envelope=envelope,receipt=receipt)
        assert actual['labels']['A'].memory_id==actual['labels']['B'].memory_id
        assert (actual['labels']['B'].revision,actual['labels']['A'].revision)==(1,2)
        graph=await manager.get_twin_graph_view(principal=principal)
        assert [(n.memory_id,n.revision) for n in graph.nodes]==[(actual['labels']['A'].memory_id,2)]
        plan=actual['plan'];ref=plan.operations[0].action_authority_ref
        stored=await authority.resolve_memory_action_authority(ref)
        for changed in (replace(ref,issuer_ref='host:other'),replace(ref,authority_id='not-stored')):
            with pytest.raises(ValueError):await authority.resolve_memory_action_authority(changed)
        bad=replace(plan,operations=(replace(plan.operations[0],payload=h.SemanticMemoryPayload('user:self','preferred_name','另一值',())),))
        previous_ref=h.MemoryMutationApplyReceiptRef(actual['old_receipt'].receipt_id,actual['old_receipt'].receipt_hash)
        with pytest.raises(ValueError,match='exact_plan_differs'):
            await authority.authorize_c01_06(bad,previous_ref,envelope,receipt)
        from simple_harness_memory.core.errors import MemoryIdempotencyConflict
        with pytest.raises(MemoryIdempotencyConflict,match='mutation_idempotency_hash_conflict'):
            await manager.apply_memory_mutation_plan(principal=principal,scope=m.MemoryScope.personal(principal.actor_id),plan=bad)
        replay=await manager.apply_memory_mutation_plan(principal=principal,scope=m.MemoryScope.personal(principal.actor_id),plan=plan)
        assert replay==actual['result']
    finally:await manager.close()
    clock[0]+=1000
    authority=CorpusRevisionAuthority(f.path,principal=principal,manager_getter=lambda:manager,clock=lambda:clock[0])
    kwargs['memory_action_authority']=authority
    manager=await m.build_human_memory_v7(tmp_path/'memory.db',**kwargs)
    try:
        assert await authority.resolve_memory_action_authority(ref)==stored
        assert (await manager.get_memory_mutation_receipt_view(principal=principal,receipt_ref=actual['result'].receipt_ref))==actual['new_receipt']
        replay=await manager.apply_memory_mutation_plan(principal=principal,scope=m.MemoryScope.personal(principal.actor_id),plan=plan)
        assert replay==actual['result']  # consumed replay, not renewed expired authority
        binding=m.HistoryEvidenceBinding(envelope,receipt)
        before=await manager.check_history_visibility(principal=principal,disclosure_context=envelope.disclosure_context,
            bindings=(binding,))
        assert len(before.items)==1 and before.items[0].visible
        eid=actual['labels']['A'].memory_id
        await manager.suppress(principal=principal,request=m.SuppressionRequest('fixture-forget',principal.actor_id,
            m.SuppressionScopeKind.MEMORY,eid,'user_forget',clock[0]))
        assert (await manager.get_twin_graph_view(principal=principal)).nodes==()
        visibility=await manager.check_history_visibility(principal=principal,disclosure_context=envelope.disclosure_context,
            bindings=(binding,))
        assert len(visibility.items)==1 and not visibility.items[0].visible
    finally:await manager.close()
