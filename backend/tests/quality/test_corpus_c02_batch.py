"""C02 setup-only public integration; no model invocation or score oracle."""
import pytest
import simple_harness_memory as m
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.quality.corpus_c01 import compile_setup,apply_setup
from deskpet.quality.corpus_c02 import SETUPS,SPECS
from tests.memory.test_primary_read_api import setup,result,AUTH
from tests.memory.test_primary_visibility import pairs,classification_policy,FILTERS

CLOCK='2026-09-06T10:00:00+08:00'

@pytest.mark.asyncio
@pytest.mark.parametrize('case_id',list(SPECS))
async def test_c02_public_setup_preserves_distractors_and_epistemic_status(tmp_path,case_id,monkeypatch):
    batch=compile_setup(case_id,SETUPS[case_id][0],scenario_clock=CLOCK)
    subject=AUTH.subject
    if case_id=='C02-19':
        from types import SimpleNamespace
        from tests.execution.test_primary_create_new_runtime import fixture
        from deskpet.sdk_adapters.context_route import local_owner_auth
        from deskpet.memory.human_memory_service import QueueTurnRequest,build_foreground_turn_evidence
        state,_,service,_,_=await fixture(tmp_path/'host')
        auth=local_owner_auth();subject=auth.subject
        await service.enqueue_turn(QueueTurnRequest(None,'setup-only',batch.setup_text))
        expected,_=build_foreground_turn_evidence(subject=subject,authority_ref=auth.authority_ref,
            delivery_key='setup-only',text=batch.setup_text)
        envelope,receipt=await HostEvidenceAuthority(state).read_admitted(expected.evidence_id)
        f=SimpleNamespace(path=state)
    else:
        f=await setup(tmp_path/'host')
        result(await f.send('queue.enqueue',{'text':batch.setup_text},key='setup-only'))
        envelope,receipt=(await pairs(f))[0]
    inference={}
    if case_id=='C02-19':
        import simple_harness as h
        from simple_harness.contracts.messages import Message,MessageRole
        from simple_harness.providers import ProviderResponse,ProviderUsage
        import tests.execution.test_primary_foreground_runtime as runtime_fixture
        from deskpet.memory.conversation_registration import PrimaryConversationAuthority
        class InferenceProvider(runtime_fixture.Provider):
            async def invoke(self,request,*,cancel):
                self.requests.append(request)
                return ProviderResponse(request.request_id,Message(MessageRole.ASSISTANT,
                    '我推测你偏好云端；尚未得到确认。'),model='model',usage=ProviderUsage(10,10,20))
        provider=InferenceProvider()
        runtime,stack,_=await runtime_fixture.build(tmp_path/'host',f.path,provider)
        try:
            assert await runtime._drive_once() and runtime.last_error is None
            assert len(provider.requests)==1
            runs=await PrimaryConversationAuthority(f.path,subject=subject).completed_run_ids()
            assert len(runs)==1
            inference=dict(inference_path=f.path,inference_host_run_id=runs[0])
        finally:
            await runtime.close()
            await stack.close()
    principal=m.MemoryPrincipal('host','household',subject,'corpus-fixture')
    manager=await m.build_human_memory_v7(tmp_path/'memory.db',
        classification_policy=classification_policy(),supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(f.path),clock=lambda:batch.scenario_time)
    try:
        if case_id=='C02-19':
            from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
            await manager.register_principal_owner(principal,m.MemoryScope.personal(subject))
            async def actual_manager():return manager
            worker=MemoryIngestionOutboxWorker(f.path,actual_manager,owner_id='corpus-inference-ingestion')
            assert await worker.run_once()=='delivered'
        actual=await apply_setup(manager=manager,principal=principal,batch=batch,envelope=envelope,receipt=receipt,**inference)
        assert set(actual['labels'])=={s[0] for s in batch.specs}
        assert len({x.memory_id for x in actual['labels'].values()})==len(batch.specs)
        graph=await manager.get_twin_graph_view(principal=principal)
        assert {n.memory_id for n in graph.nodes}=={r.memory_id for r in actual['labels'].values()}
        assert graph.edges==()
        assert actual['setup_hash']==SETUPS[case_id][1]
        for label, record in actual['labels'].items():
            expected='llm_inference' if (case_id,label)==('C02-19','B') else 'explicit_user'
            assert record.epistemic_status==expected
        if case_id=='C02-19':
            by_id={n.memory_id:n for n in graph.nodes}
            node=by_id[actual['labels']['B'].memory_id]
            assert node.status=='inferred'
        if case_id=='C02-20':
            assert len(actual['labels'])==1
            assert '颜色' not in actual['plan'].operations[0].payload.object_value
        if any(s[1]=='episode' for s in batch.specs):
            assert 'undated_past_episode=clock-24h' in actual['fixture_defaults']
        if any(s[1]=='prospective' for s in batch.specs):
            assert 'undated_unexpired_prospective=clock+24h' in actual['fixture_defaults']
    finally:await manager.close()
