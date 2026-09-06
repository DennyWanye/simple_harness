from dataclasses import replace
import pytest
import simple_harness_memory as m
from deskpet.quality.corpus_seed import GraphSeed, apply_graph_seed
from deskpet.quality.corpus_graph_drain import drain_graph_seed_analysis, GraphFixtureDeliveryAuthority
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from tests.memory.test_primary_read_api import setup, result, AUTH
from tests.memory.test_primary_visibility import pairs, classification_policy, FILTERS

@pytest.mark.asyncio
async def test_committed_graph_drain_reopen_no_second_analysis(tmp_path):
    text='我偏好简洁答复。我定义答复步骤：先给结论，再省略不必要的细节。简洁答复偏好适用于这套答复步骤。'
    seed=GraphSeed(text,'我偏好简洁答复。','我定义答复步骤：先给结论，再省略不必要的细节。',
        '简洁答复偏好适用于这套答复步骤。','response_style','简洁','简洁答复步骤',('向本人答复',),
        ('先给结论','省略不必要的细节'))
    f=await setup(tmp_path/'host')
    result(await f.send('queue.enqueue',{'text':text},key='graph-source'))
    envelope,receipt=(await pairs(f))[0]
    principal=m.MemoryPrincipal('host','household',AUTH.subject,'graph-fixture')
    authority=GraphFixtureDeliveryAuthority()
    config=dict(analysis_delivery_authority=authority,classification_policy=classification_policy(),supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(f.path),clock=lambda:200.0)
    manager=await m.build_human_memory_v7(tmp_path/'memory.db',**config)
    try:
        plan,applied,view,graph=await apply_graph_seed(manager=manager,principal=principal,seed=seed,
            envelope=envelope,receipt=receipt,plan_id='graph-fixture',base_revision=1)
        args=dict(path=f.path,principal=principal,seed=seed,envelope=envelope,receipt=receipt,
            plan=plan,applied=applied,clock=lambda:200.0)
        # A caller-supplied, rehashed plan cannot acknowledge this real receipt.
        wrong=dict(args,plan=replace(plan,plan_id='foreign-plan'))
        with pytest.raises(ValueError,match='graph_fixture_commit_differs'):
            await drain_graph_seed_analysis(manager=manager,delivery_authority=authority,**wrong)
        assert authority.executor is None
        outcome,count=await drain_graph_seed_analysis(manager=manager,delivery_authority=authority,**args)
        assert outcome.value=='applied' and count==1
        assert await manager.get_twin_graph_view(principal=principal)==graph
    finally:await manager.close()
    authority=GraphFixtureDeliveryAuthority()
    config['analysis_delivery_authority']=authority
    reopened=await m.build_human_memory_v7(tmp_path/'memory.db',**config)
    try:
        with pytest.raises(ValueError,match='graph_fixture_settlement_unconfirmed'):
            await drain_graph_seed_analysis(manager=reopened,delivery_authority=authority,**args)
        assert authority.executor.executions==0
        restored=await reopened.get_twin_graph_view(principal=principal)
        assert restored.nodes==graph.nodes and restored.edges==graph.edges
    finally:await reopened.close()


class FailDeliveryOnce(GraphFixtureDeliveryAuthority):
    async def verify_analysis_delivery(self, request, envelope):
        await super().verify_analysis_delivery(request,envelope)
        from simple_harness_memory.core.jobs import AnalysisDeliveryAuthorityTransientError
        raise AnalysisDeliveryAuthorityTransientError('fixture_actual_delivery_fault')


@pytest.mark.asyncio
async def test_retry_backoff_reopen_idle_is_not_settlement(tmp_path):
    text='我偏好简洁答复。我定义答复步骤：先给结论，再省略不必要的细节。简洁答复偏好适用于这套答复步骤。'
    seed=GraphSeed(text,'我偏好简洁答复。','我定义答复步骤：先给结论，再省略不必要的细节。',
        '简洁答复偏好适用于这套答复步骤。','response_style','简洁','简洁答复步骤',('向本人答复',),
        ('先给结论','省略不必要的细节'))
    f=await setup(tmp_path/'host')
    result(await f.send('queue.enqueue',{'text':text},key='graph-source'))
    envelope,receipt=(await pairs(f))[0]
    principal=m.MemoryPrincipal('host','household',AUTH.subject,'graph-fixture')
    authority=FailDeliveryOnce()
    config=dict(analysis_delivery_authority=authority,classification_policy=classification_policy(),supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(f.path),clock=lambda:200.0)
    manager=await m.build_human_memory_v7(tmp_path/'memory.db',**config)
    try:
        plan,applied,view,graph=await apply_graph_seed(manager=manager,principal=principal,seed=seed,
            envelope=envelope,receipt=receipt,plan_id='graph-fixture',base_revision=1)
        args=dict(path=f.path,principal=principal,seed=seed,envelope=envelope,receipt=receipt,
            plan=plan,applied=applied,clock=lambda:200.0)
        with pytest.raises(ValueError,match='graph_fixture_drain_failed:retry_scheduled'):
            await drain_graph_seed_analysis(manager=manager,delivery_authority=authority,**args)
        assert authority.executor.executions==1
        assert await manager.get_twin_graph_view(principal=principal)==graph
    finally:await manager.close()
    authority=GraphFixtureDeliveryAuthority()
    config['analysis_delivery_authority']=authority
    reopened=await m.build_human_memory_v7(tmp_path/'memory.db',**config)
    try:
        with pytest.raises(ValueError,match='graph_fixture_settlement_unconfirmed'):
            await drain_graph_seed_analysis(manager=reopened,delivery_authority=authority,**args)
        assert authority.executor.executions==0
        restored=await reopened.get_twin_graph_view(principal=principal)
        assert restored.nodes==graph.nodes and restored.edges==graph.edges
    finally:await reopened.close()
