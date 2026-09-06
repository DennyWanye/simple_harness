"""Real Host admitted S1 + installed MemoryManager mutation; no Provider."""
from dataclasses import replace

import pytest
import simple_harness_memory as m
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.quality.corpus_seed import C01_10_SETUP, c01_10_seed, apply_semantic_seed
from tests.memory.test_primary_read_api import setup, result, AUTH
from tests.memory.test_primary_visibility import pairs, classification_policy, FILTERS


@pytest.mark.asyncio
async def test_c01_10_public_seed_receipt_reopen_and_source_rejection(tmp_path):
    seed=c01_10_seed(C01_10_SETUP)
    f=await setup(tmp_path/'host')
    result(await f.send('queue.enqueue',{'text':seed.source_text},key='fixture-seed-source'))
    envelope,receipt=(await pairs(f))[0]
    principal=m.MemoryPrincipal('host','household',AUTH.subject,'corpus-fixture')
    kwargs=dict(classification_policy=classification_policy(),supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(f.path),clock=lambda:200.0)
    manager=await m.build_human_memory_v7(tmp_path/'memory.db',**kwargs)
    try:
        applied=await apply_semantic_seed(manager=manager,principal=principal,seed=seed,
            envelope=envelope,receipt=receipt,plan_id='c01-10-setup',base_revision=1)
        assert applied.label=='A'
        assert applied.committed_view.operations[0].memory_id
        # Same actual source/plan replays the same public receipt, not a second seed.
        replay=await apply_semantic_seed(manager=manager,principal=principal,seed=seed,
            envelope=envelope,receipt=receipt,plan_id='c01-10-setup',base_revision=1)
        assert replay.committed_view==applied.committed_view
        with pytest.raises(ValueError,match='admitted_source_differs'):
            await apply_semantic_seed(manager=manager,principal=principal,
                seed=replace(seed,source_text='invented source'),envelope=envelope,receipt=receipt,
                plan_id='rejected',base_revision=2)
    finally:await manager.close()
    reopened=await m.build_human_memory_v7(tmp_path/'memory.db',**kwargs)
    try:
        view=await reopened.get_memory_mutation_receipt_view(principal=principal,
            receipt_ref=applied.apply_result.receipt_ref)
        assert view==applied.committed_view
    finally:await reopened.close()


def test_setup_mapping_does_not_accept_case_or_changed_text():
    for wrong in ({'setup':C01_10_SETUP,'gold':'answer'},C01_10_SETUP+'gold'):
        with pytest.raises(ValueError,match='setup_source_changed'):c01_10_seed(wrong)


@pytest.mark.asyncio
async def test_public_atomic_claim_procedure_relation_is_two_nodes_one_edge(tmp_path):
    from deskpet.quality.corpus_seed import GraphSeed, apply_graph_seed
    text='我偏好简洁答复。我定义答复步骤：先给结论，再省略不必要的细节。简洁答复偏好适用于这套答复步骤。'
    seed=GraphSeed(text,'我偏好简洁答复。','我定义答复步骤：先给结论，再省略不必要的细节。',
        '简洁答复偏好适用于这套答复步骤。','response_style','简洁','简洁答复步骤',('向本人答复',),
        ('先给结论','省略不必要的细节'))
    f=await setup(tmp_path/'host')
    result(await f.send('queue.enqueue',{'text':text},key='explicit-graph-fixture'))
    envelope,receipt=(await pairs(f))[0]
    principal=m.MemoryPrincipal('host','household',AUTH.subject,'graph-fixture')
    kwargs=dict(classification_policy=classification_policy(),supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(f.path),clock=lambda:200.0)
    manager=await m.build_human_memory_v7(tmp_path/'memory.db',**kwargs)
    try:
        plan,applied,view,graph=await apply_graph_seed(manager=manager,principal=principal,seed=seed,
            envelope=envelope,receipt=receipt,plan_id='graph-fixture',base_revision=1)
        assert len(view.operations)==3 and len(graph.nodes)==2 and len(graph.edges)==1
    finally:await manager.close()
    reopened=await m.build_human_memory_v7(tmp_path/'memory.db',**kwargs)
    try:
        restored=await reopened.get_twin_graph_view(principal=principal)
        assert restored.nodes==graph.nodes and restored.edges==graph.edges
        actual=await reopened.get_memory_mutation_receipt_view(principal=principal,receipt_ref=applied.receipt_ref)
        assert actual==view
    finally:await reopened.close()
