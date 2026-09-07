"""Setup-only new C01 cases; old C01-10 and graph controls are not rerun."""
import pytest
import simple_harness_memory as m
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.quality.corpus_c01 import SETUPS,SPECS,compile_setup,apply_setup
from tests.memory.test_primary_read_api import setup,result,AUTH
from tests.memory.test_primary_visibility import pairs,classification_policy,FILTERS

CLOCK='2026-09-06T10:00:00+08:00'

@pytest.mark.asyncio
@pytest.mark.parametrize('case_id',[k for k in SPECS if k!='C01-10'])
async def test_new_c01_public_atomic_setup_and_all_label_readback(tmp_path,case_id):
    batch=compile_setup(case_id,SETUPS[case_id][0],scenario_clock=CLOCK)
    f=await setup(tmp_path/'host')
    result(await f.send('queue.enqueue',{'text':batch.setup_text},key='setup-only'))
    envelope,receipt=(await pairs(f))[0]
    principal=m.MemoryPrincipal('host','household',AUTH.subject,'corpus-fixture')
    manager=await m.build_human_memory_v7(tmp_path/'memory.db',
        classification_policy=classification_policy(),supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(f.path),clock=lambda:batch.scenario_time)
    try:
        actual=await apply_setup(manager=manager,principal=principal,batch=batch,envelope=envelope,receipt=receipt)
        assert set(actual['labels'])=={s[0] for s in batch.specs}
        assert len({x.memory_id for x in actual['labels'].values()})==len(batch.specs)
        graph=await manager.get_twin_graph_view(principal=principal)
        assert {n.memory_id for n in graph.nodes}=={r.memory_id for r in actual['labels'].values()}
        assert graph.edges==()
        assert actual['setup_hash']==SETUPS[case_id][1]
        if any(s[1]=='episode' for s in batch.specs):
            assert 'undated_past_episode=clock-24h' in actual['fixture_defaults']
        if any(s[1]=='prospective' for s in batch.specs):
            assert 'undated_unexpired_prospective=clock+24h' in actual['fixture_defaults']
    finally:await manager.close()
