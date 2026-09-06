"""New C03 setup-only public mutation controls; never a quality score."""
import pytest
import simple_harness_memory as m
from deskpet.quality.corpus_c03 import compile_c03_setup as compile_setup
from deskpet.quality.corpus_c03_prepare import prepare_c03_setup
from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority
from deskpet.quality.corpus_c03 import SETUPS,SPECS,REQUIRES_SPECIAL_MAPPING
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from tests.memory.test_primary_read_api import setup,result,AUTH
from tests.memory.test_primary_visibility import classification_policy,FILTERS

@pytest.mark.asyncio
@pytest.mark.parametrize('case_id',tuple(SPECS))
async def test_c03_public_seed_preserves_episode_semantic_source(tmp_path,case_id):
    batch=compile_setup(case_id,SETUPS[case_id][0],scenario_clock='2026-09-06T10:00:00+08:00')
    f=await setup(tmp_path/'host')
    authority=SetupFixtureDeliveryAuthority()
    principal=m.MemoryPrincipal('host','household',AUTH.subject,'corpus-fixture')
    manager=await m.build_human_memory_v7(tmp_path/'memory.db',
        classification_policy=classification_policy(),supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(f.path),analysis_delivery_authority=authority,clock=lambda:batch.scenario_time)
    try:
        actual=await prepare_c03_setup(path=f.path,manager=manager,principal=principal,batch=batch,authority_ref=AUTH.authority_ref,delivery_authority=authority)
        pair=actual['source_pair']
        assert actual['outcome'].value=='applied' and actual['fixture_executions']==1
        assert set(actual['labels'])=={spec[0] for spec in SPECS[case_id]}
        assert actual['setup_hash']==SETUPS[case_id][1]
        assert len({view.memory_id for view in actual['labels'].values()})==len(SPECS[case_id])
        for label,kind,subject,title,value,qualifiers in SPECS[case_id]:
            view=actual['labels'][label]
            assert view.memory_type==kind and view.revision==1
            operation=next(o for o in actual['plan'].operations if o.operation_id==label)
            assert len(operation.evidence_spans)==1
            span=operation.evidence_spans[0]
            assert (span.evidence_id,span.envelope_hash,span.admission_receipt_hash)==(
                pair[0].evidence_id,pair[0].envelope_hash,pair[1].receipt_hash)
            assert span.exact_quote==batch.setup_text
            assert view.source_refs and all(ref.quote_hash==span.quote_hash for ref in view.source_refs)
            if kind=='episode':
                assert operation.payload.occurred_start==batch.scenario_time-86400
                assert operation.payload.participants==(subject,)
                assert operation.payload.actions==(value,)
            else:
                assert operation.payload.subject_entity==subject
                assert operation.payload.object_value==value
                assert operation.payload.qualifiers==qualifiers
        page=result(await f.send('primary.messages.page',{'primary_ref':f.primary},key='not-history'))
        assert page['items']==[]
    finally:await manager.close()

@pytest.mark.parametrize('case_id',tuple(REQUIRES_SPECIAL_MAPPING))
def test_explicit_date_and_model_source_not_silently_defaulted(case_id):
    with pytest.raises(ValueError,match='corpus_c03_specific_mapping_required'):
        compile_setup(case_id,SETUPS[case_id][0],scenario_clock='2026-09-06T10:00:00+08:00')


def test_c03_modified_mapping_or_source_rejected_before_preparation():
    from dataclasses import replace
    from deskpet.quality.corpus_c03_prepare import validate_batch
    batch=compile_setup('C03-01',SETUPS['C03-01'][0],scenario_clock='2026-09-06T10:00:00+08:00')
    with pytest.raises(ValueError,match='corpus_c03_setup_manifest_differs'):
        validate_batch(replace(batch,specs=batch.specs[:-1]))
    with pytest.raises(ValueError,match='corpus_setup_source_changed'):
        validate_batch(replace(batch,setup_text=batch.setup_text+'伪造内容'))
