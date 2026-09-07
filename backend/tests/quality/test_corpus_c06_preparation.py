"""NOT_RUN: C06 public preparation controls, not cross-task quality claims."""
from dataclasses import replace
from hashlib import sha256

import pytest
import simple_harness_memory as m

from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_foreground_turn_evidence
from deskpet.quality.corpus_c06 import SETUPS, SPECS, compile_c06_setup, validate_batch
from deskpet.quality.corpus_c06_prepare import prepare_c06_setup, read_c06_preparation
from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority
from tests.memory.test_primary_read_api import AUTH, setup, result
from tests.memory.test_primary_visibility import classification_policy, FILTERS

CLOCK = '2026-09-06T10:00:00+08:00'


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', ('C06-02', 'C06-03', 'C06-04'))
async def test_public_c06_mixed_job_exact_source_reopen_and_foreign_owner(tmp_path, case_id):
    batch = compile_c06_setup(case_id, SETUPS[case_id][0], scenario_clock=CLOCK)
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
        assert set(actual['labels']) == {'S', 'P'}
        operations = {op.operation_id: op for op in actual['plan'].operations}
        assert len(actual['plan'].operations) == len(operations) == 2
        assert {key: op.memory_type.value for key, op in operations.items()} == {
            'S': 'semantic', 'P': 'procedure'}
        assert all(op.lifecycle_state.value == 'active' for op in actual['plan'].operations)
        pair = actual['source_pair']
        assert pair[0].subject == principal.actor_id
        for op in actual['plan'].operations:
            span = op.evidence_spans[0]
            assert (span.evidence_id, span.envelope_hash, span.admission_receipt_hash) == (
                pair[0].evidence_id, pair[0].envelope_hash, pair[1].receipt_hash)
            assert span.exact_quote == SPECS[case_id][0 if op.operation_id == 'S' else 3]
            node = actual['labels'][op.operation_id]
            assert node.source_refs[0].evidence_ref_hash == sha256(pair[0].evidence_id.encode()).hexdigest()
            assert node.revision == 1
        assert operations['P'].payload.steps == SPECS[case_id][6]
        assert operations['P'].payload.applicability == SPECS[case_id][5]
        page = result(await host.send('primary.messages.page', {'primary_ref': host.primary}, key='no-setup-history'))
        assert page['items'] == []  # No fake foreground source Run to copy into scoring.
        # Same setup bytes on another genuinely admitted Host S1 must not bind
        # the original memories merely because the quote/content hashes match.
        other_pair = build_foreground_turn_evidence(subject=principal.actor_id, authority_ref=AUTH.authority_ref,
            delivery_key='different-c06-source:'+case_id, text=batch.setup_text)
        await HumanMemoryProgramStore(host.path).append_evidence(*other_pair)
        with pytest.raises(ValueError, match='corpus_c06_public_readback_missing_or_ambiguous'):
            await read_c06_preparation(manager=manager, principal=principal, batch=batch, pair=other_pair)
        foreign = m.MemoryPrincipal('host', 'household', 'c06-other-owner', 'corpus-fixture')
        await manager.register_principal_owner(foreign, m.MemoryScope.personal(foreign.actor_id))
        foreign_graph = await manager.get_twin_graph_view(principal=foreign)
        assert not {n.memory_id for n in foreign_graph.nodes} & {n.memory_id for n in actual['labels'].values()}
        with pytest.raises(ValueError, match='corpus_c06_source_owner_differs'):
            await read_c06_preparation(manager=manager, principal=foreign, batch=batch, pair=pair)
    finally:
        await manager.close()
    manager = await m.build_human_memory_v7(tmp_path/'memory.db', **kwargs)
    try:
        reopened = await read_c06_preparation(manager=manager, principal=principal, batch=batch, pair=pair)
        assert reopened == actual['labels']
        assert authority.executor.executions == 1  # Reopen/readback does not re-run setup analysis.
    finally:
        await manager.close()


def test_c06_exact_setup_and_twenty_case_denominator():
    assert set(SETUPS) == {f'C06-{i:02}' for i in range(1, 21)}
    batch = compile_c06_setup('C06-02', SETUPS['C06-02'][0], scenario_clock=CLOCK)
    with pytest.raises(ValueError, match='corpus_c06_setup_source_changed'):
        validate_batch(replace(batch, setup_text=batch.setup_text+'外部授权'))
    with pytest.raises(ValueError, match='corpus_c06_manifest_differs'):
        validate_batch(replace(batch, setup_hash='0'*64))
    for case_id in SETUPS.keys()-SPECS.keys():
        with pytest.raises(ValueError, match='corpus_c06_setup_mapping_pending'):
            compile_c06_setup(case_id, SETUPS[case_id][0], scenario_clock=CLOCK)


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', (
    'C06-06', 'C06-08', 'C06-09', 'C06-11', 'C06-12', 'C06-15', 'C06-16',
))
async def test_public_c06_next_scalar_procedure_sources(tmp_path, case_id):
    # Reuse the real public chain and all original source/owner/reopen oracles.
    # A separate selector keeps the first fixed three controls out of this batch.
    await test_public_c06_mixed_job_exact_source_reopen_and_foreign_owner(tmp_path, case_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', (
    'C06-05', 'C06-07', 'C06-10', 'C06-13', 'C06-14', 'C06-20',
))
async def test_public_c06_conditional_descriptions_preserve_limits(tmp_path, case_id):
    # Same real mixed job/source/readback oracle, without executing the procedure.
    await test_public_c06_mixed_job_exact_source_reopen_and_foreign_owner(tmp_path, case_id)
    # Independent authored limits: these are descriptions, never an admission
    # receipt for sending, deleting, material use or execution.
    conditions, steps = SPECS[case_id][5:7]
    if case_id == 'C06-05':
        assert '只描述不做' in conditions
        assert steps == ('核原件副本', '再核可读性')
    elif case_id == 'C06-07':
        assert '确认对应再执行' in conditions
        assert steps == ('清单预览', '确认对应', '再执行')
    elif case_id == 'C06-10':
        assert steps == ('先列来源', '再核授权', '最后核离线副本')
    elif case_id == 'C06-13':
        assert '重复项只列候选不删除' in conditions
        assert steps[-1] == '重复项只列候选不删除'
    elif case_id == 'C06-14':
        assert '需用户后续确认发送' in conditions
        assert steps == ('检查出处', '检查私密字段', '检查版本号')
    else:
        assert steps == ('若材料有日期先按日期分组，无日期先按主题',)
