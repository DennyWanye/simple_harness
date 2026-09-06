"""Prepared control for real fixture compilation inside SDK analysis job lifecycle."""
import pytest
import simple_harness_memory as m
from deskpet.quality.corpus_c01 import SETUPS,compile_setup
from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority,prepare_runtime_seed
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from tests.memory.test_primary_read_api import setup,result,AUTH
from tests.memory.test_primary_visibility import classification_policy,FILTERS

@pytest.mark.asyncio
async def test_real_setup_job_materializes_without_foreground_source(tmp_path):
    f=await setup(tmp_path/'host')
    batch=compile_setup('C01-08',SETUPS['C01-08'][0],scenario_clock='2026-09-06T10:00:00+08:00')
    authority=SetupFixtureDeliveryAuthority()
    principal=m.MemoryPrincipal('host','household',AUTH.subject,'corpus-fixture')
    manager=await m.build_human_memory_v7(tmp_path/'memory.db',
        classification_policy=classification_policy(),supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(f.path),analysis_delivery_authority=authority,
        clock=lambda:batch.scenario_time)
    try:
        actual=await prepare_runtime_seed(path=f.path,manager=manager,principal=principal,
            authority_ref=AUTH.authority_ref,batch=batch,delivery_authority=authority)
        assert actual['outcome'].value=='applied' and actual['fixture_executions']==1
        assert set(actual['labels'])=={spec[0] for spec in batch.specs}
        # This IDLE is only an observation after actual APPLIED, not settlement proof.
        assert (await actual['runner'].run_once()).value=='idle'
        assert actual['executor'].executions==1
        page=result(await f.send('primary.messages.page',{'primary_ref':f.primary},key='no-setup-history'))
        assert page['items']==[]
    finally:await manager.close()
