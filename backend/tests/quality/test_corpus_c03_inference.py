"""Actual Host/SDK source-only inference, not a model quality measurement."""
import pytest
import simple_harness_memory as m
from simple_harness.contracts.messages import Message,MessageRole
from simple_harness.providers import ProviderResponse,ProviderUsage
from deskpet.quality.corpus_c03 import SETUPS
from deskpet.quality.corpus_c03_inference import EXPECTED_OUTPUT,prepare_c03_20_setup,read_c03_20_inference
from deskpet.memory.human_memory_service import QueueTurnRequest,build_foreground_turn_evidence
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.memory_ingestion_outbox import MemoryIngestionOutboxWorker
from deskpet.memory.conversation_registration import PrimaryConversationAuthority
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.execution.test_primary_create_new_runtime import fixture
from tests.memory.test_primary_visibility import classification_policy,FILTERS
import tests.execution.test_primary_foreground_runtime as runtime_fixture

class InferenceProvider(runtime_fixture.Provider):
    async def invoke(self,request,*,cancel):
        self.requests.append(request)
        return ProviderResponse(request.request_id,Message(MessageRole.ASSISTANT,EXPECTED_OUTPUT),
            model='model',usage=ProviderUsage(10,10,20))

@pytest.mark.asyncio
async def test_c03_20_actual_original_source_and_candidate_public_mutation(tmp_path):
    auth=local_owner_auth()
    state,_,service,_,_=await fixture(tmp_path/'source')
    text=SETUPS['C03-20'][0]
    await service.enqueue_turn(QueueTurnRequest(None,'c03-original-source',text))
    expected,_=build_foreground_turn_evidence(subject=auth.subject,authority_ref=auth.authority_ref,
        delivery_key='c03-original-source',text=text)
    original=await HostEvidenceAuthority(state).read_admitted(expected.evidence_id)
    provider=InferenceProvider()
    runtime,stack,_=await runtime_fixture.build(tmp_path/'source',state,provider)
    try:
        await runtime.after_enqueue(subject=auth.subject)
        await runtime.drain()
        assert runtime.last_error is None and len(provider.requests)==1
        source_memory=runtime.history_memory
        manager=await source_memory.manager()
        await manager.register_principal_owner(source_memory.principal(),m.MemoryScope.personal(auth.subject))
        worker=MemoryIngestionOutboxWorker(state,source_memory.manager,owner_id='c03-source-ingestion')
        assert await worker.run_once()=='delivered'
        run_ids=await PrimaryConversationAuthority(state,subject=auth.subject).completed_run_ids()
        assert len(run_ids)==1
    finally:
        await runtime.close()
        await stack.close()
    scoring,_,scoring_service,_,_=await fixture(tmp_path/'scoring')
    principal=m.MemoryPrincipal('host','household',auth.subject,'corpus-fixture')
    config=dict(classification_policy=classification_policy(),supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(scoring),clock=lambda:1788660000.0)
    manager=await m.build_human_memory_v7(tmp_path/'scoring-memory.db',**config)
    try:
        args=dict(source_path=state,scoring_path=scoring,manager=manager,principal=principal,
            host_run_id=run_ids[0],original_envelope=original[0],original_receipt=original[1],scenario_time=1788660000.0)
        actual=await prepare_c03_20_setup(**args)
        assert set(actual['labels'])=={'E','S','D'}
        assert actual['labels']['D'].epistemic_status=='llm_inference'
        assert actual['analysis_jobs']=='not_drained_multi_source'
        operation=next(op for op in actual['plan'].operations if op.operation_id=='D')
        assert operation.lifecycle_state.value=='candidate' and operation.verification_state.value=='unverified'
        assert operation.evidence_spans[0].actor_role.value=='assistant'
        assert operation.evidence_spans[0].item_ordinal==1
        assert actual['source_group'].registrations[1].metadata.item_ordinal==2
        graph=await manager.get_twin_graph_view(principal=principal)
        by_id={node.memory_id:node for node in graph.nodes}
        assert by_id[actual['labels']['D'].memory_id].status=='inferred'
        assert graph.edges==()
        assert await PrimaryConversationAuthority(scoring,subject=auth.subject).completed_run_ids()==()
        # Same authored text in a different actual USER pair cannot borrow Run1.
        await service.enqueue_turn(QueueTurnRequest(None,'same-text-other-source',text))
        other,_=build_foreground_turn_evidence(subject=auth.subject,authority_ref=auth.authority_ref,
            delivery_key='same-text-other-source',text=text)
        other_pair=await HostEvidenceAuthority(state).read_admitted(other.evidence_id)
        with pytest.raises(ValueError,match='c03_20_original_pair_differs'):
            await read_c03_20_inference(path=state,subject=auth.subject,host_run_id=run_ids[0],
                original_envelope=other_pair[0],original_receipt=other_pair[1])
        assert await manager.get_twin_graph_view(principal=principal)==graph
    finally:await manager.close()
    reopened=await m.build_human_memory_v7(tmp_path/'scoring-memory.db',**config)
    try:
        restored=await reopened.get_twin_graph_view(principal=principal)
        assert restored.nodes==graph.nodes
        view=await reopened.get_memory_mutation_receipt_view(principal=principal,receipt_ref=actual['applied'].receipt_ref)
        assert view==actual['receipt']
    finally:await reopened.close()
