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
                timing=actual['date_semantics'][label]
                assert operation.payload.occurred_start==timing['occurred_start']
                assert timing['synthetic_day'] is True
                assert timing['authored_precision'] in operation.payload.title
                assert 'synthetic_day=true' in operation.payload.title
                assert '不可作为日期答案' in operation.payload.title
                assert operation.payload.occurred_end is None
                assert operation.payload.participants==(subject,)
                assert operation.payload.actions==(value,)
            else:
                assert operation.payload.subject_entity==subject
                assert operation.payload.object_value==value
                assert operation.payload.qualifiers==qualifiers
        if case_id=='C03-08':
            inferred_report=next(o for o in actual['plan'].operations if o.operation_id=='D')
            assert '他人转述' in inferred_report.payload.actions[0]
            assert inferred_report.epistemic_status.value=='explicit_user'
            assert inferred_report.verification_state.value=='source_bound'
            actual_report=actual['labels']['D']
            assert actual_report.epistemic_status=='explicit_user'
            assert actual_report.verification_state=='source_bound'
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


@pytest.mark.parametrize('clock,year',[
 ('2026-09-06T10:00:00+08:00',2026),
 ('2026-07-01T10:00:00+08:00',2025),
 ('2027-01-01T10:00:00+08:00',2026),
])
def test_month_group_preserves_order_and_does_not_claim_exact_source_day(clock,year):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from deskpet.quality.corpus_c03_dates import c03_payload,date_semantics
    batch=compile_setup('C03-17',SETUPS['C03-17'][0],scenario_clock=clock)
    events=[c03_payload(batch,s) for s in batch.specs if s[1]=='episode']
    assert [datetime.fromtimestamp(e.occurred_start,ZoneInfo('Asia/Shanghai')).isoformat() for e in events]==[
        f'{year}-06-15T12:00:00+08:00',f'{year}-08-15T12:00:00+08:00']
    assert events[0].occurred_start<events[1].occurred_start<batch.scenario_time
    assert all('synthetic_day=true' in e.title and e.occurred_end is None for e in events)


def test_spring_precision_retained_in_public_payload():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from deskpet.quality.corpus_c03_dates import c03_payload
    batch=compile_setup('C03-02',SETUPS['C03-02'][0],scenario_clock='2026-09-06T10:00:00+08:00')
    episode=c03_payload(batch,batch.specs[0])
    assert datetime.fromtimestamp(episode.occurred_start,ZoneInfo('Asia/Shanghai')).isoformat()=='2026-04-15T12:00:00+08:00'
    assert 'season:spring' in episode.title and '非真实发生日' in episode.title
    assert episode.occurred_end is None
