"""New C06 multi-source controls only; main schedules installed execution."""
from hashlib import sha256

import pytest
import simple_harness_memory as m

from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.quality.corpus_c06 import SETUPS, compile_c06_setup
from deskpet.quality.corpus_c06_prepare import prepare_c06_setup, read_c06_preparation
from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority
from tests.memory.test_primary_read_api import AUTH, setup, result
from tests.memory.test_primary_visibility import classification_policy, FILTERS

EXTRA_QUOTES = {
    'C06-17': {'E': '旧项目跳过抽样导致出错'},
    'C06-18': {'P2': '仅财务项目先套专有账模板'},
    'C06-19': {'P2': '需联网插件的active程序', 'ENV': '当前设备离线'},
}


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', ('C06-17', 'C06-18', 'C06-19'))
async def test_public_c06_extra_sources_are_not_dropped_or_promoted_to_authority(tmp_path, case_id):
    batch = compile_c06_setup(case_id, SETUPS[case_id][0], scenario_clock='2026-09-06T10:00:00+08:00')
    host = await setup(tmp_path/'host')
    authority = SetupFixtureDeliveryAuthority()
    principal = m.MemoryPrincipal('host', 'household', AUTH.subject, 'corpus-fixture')
    kwargs = dict(classification_policy=classification_policy(), supported_filter_policies=FILTERS,
        evidence_authority=HostEvidenceAuthority(host.path), analysis_delivery_authority=authority,
        clock=lambda: batch.scenario_time)
    manager = await m.build_human_memory_v7(tmp_path/'memory.db', **kwargs)
    try:
        actual = await prepare_c06_setup(path=host.path, manager=manager, principal=principal,
            authority_ref=AUTH.authority_ref, batch=batch, delivery_authority=authority)
        assert actual['outcome'].value == 'applied' and actual['fixture_executions'] == 1
        operations = {op.operation_id: op for op in actual['plan'].operations}
        expected = {'S': 'semantic', 'P': 'procedure', 'E': 'episode'} if case_id == 'C06-17' else {
            'S': 'semantic', 'P1': 'procedure', 'P2': 'procedure'}
        if case_id == 'C06-19':
            expected['ENV'] = 'semantic'
        assert len(operations) == len(actual['plan'].operations) == len(expected)
        assert {k: op.memory_type.value for k, op in operations.items()} == expected
        assert set(actual['labels']) == set(expected)
        assert len({node.memory_id for node in actual['labels'].values()}) == len(expected)
        pair = actual['source_pair']
        for key, operation in operations.items():
            assert operation.target is None and operation.depends_on_operation_ids == ()
            assert operation.proposed_privacy_class.value == 'personal'
            assert operation.verification_state.value == 'source_bound'
            assert operation.epistemic_status.value == 'explicit_user'
            span, = operation.evidence_spans
            assert (span.evidence_id, span.envelope_hash, span.admission_receipt_hash) == (
                pair[0].evidence_id, pair[0].envelope_hash, pair[1].receipt_hash)
            ref, = actual['labels'][key].source_refs
            assert ref.evidence_ref_hash == sha256(pair[0].evidence_id.encode()).hexdigest()
            assert ref.span_ref_hash == sha256(span.span_id.encode()).hexdigest()
            if key in EXTRA_QUOTES[case_id]:
                assert span.exact_quote == EXTRA_QUOTES[case_id][key]
        if case_id == 'C06-17':
            episode = operations['E'].payload
            assert episode.actions == ('旧项目跳过抽样导致出错',)
            assert episode.occurred_start == batch.scenario_time - 86400
            assert episode.occurred_end is None
            assert 'synthetic_day=true' in episode.title and '非真实发生日' in episode.title
            assert operations['P'].payload.steps == ('先清单', '后抽样')
        elif case_id == 'C06-18':
            assert operations['P1'].payload.applicability == ('全局通用',)
            assert operations['P1'].payload.steps == ('先列来源', '后概括')
            assert operations['P2'].payload.applicability == ('仅财务项目',)
            assert operations['P2'].payload.steps == ('先套专有账模板',)
        else:
            assert operations['P1'].payload.applicability == ('通用手工',)
            assert operations['P2'].payload.applicability == ('需联网插件',)
            assert '原文未提供具体步骤' in operations['P2'].payload.name
            assert operations['P2'].payload.steps == ('需联网插件的active程序',)
            assert operations['ENV'].payload.subject_entity == 'device:current'
            assert operations['ENV'].payload.object_value == '离线'
            assert operations['ENV'].payload.qualifiers == ('setup时点声明，非运行时设备观测',)
            assert actual['source_limits'] == ('online_procedure_steps_unspecified',
                'setup_offline_declaration_is_not_runtime_capability_authority')
        page = result(await host.send('primary.messages.page', {'primary_ref': host.primary}, key='setup-not-history'))
        assert page['items'] == []
    finally:
        await manager.close()
    manager = await m.build_human_memory_v7(tmp_path/'memory.db', **kwargs)
    try:
        assert await read_c06_preparation(manager=manager, principal=principal, batch=batch, pair=pair) == actual['labels']
        assert authority.executor.executions == 1
    finally:
        await manager.close()
