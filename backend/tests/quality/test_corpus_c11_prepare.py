"""C11 time-state source controls, not model quality or derived-source coverage."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_v7 import (HumanMemoryV7Runtime, local_memory_principal,
    host_classification_policy, HOST_SUPPORTED_FILTER_POLICIES)
from deskpet.quality.corpus_c11 import SETUPS, SPECS, compile_c11_setup, validate_c11_setup
from deskpet.quality.corpus_c11_prepare import open_c11_fixture
from tests.quality.test_corpus_c01_revision_clock import _host, AUTH, CLOCK


def test_original_temporal_setup_mapping_and_defaults_are_explicit():
    path = Path('/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/'
        'quality/recall-corpus-candidate/review-zh/successor-12x20/11-expired.md')
    original, case_id = {}, None
    for line in path.read_text().splitlines():
        if line.startswith('## C11-'): case_id = line.split('｜',1)[0][3:]
        if line.startswith('**setup（模型初始不可见）：** '): original[case_id] = line.split('** ',1)[1]
    assert set(original) == set(SETUPS) == {f'C11-{i:02}' for i in range(1,21)}
    for case_id, text in original.items():
        assert SETUPS[case_id] == (text, sha256(text.encode()).hexdigest())
        if case_id not in SPECS:
            with pytest.raises(ValueError, match='source_pending'):
                compile_c11_setup(case_id, text, scenario_clock=CLOCK)
            continue
        batch = compile_c11_setup(case_id, text, scenario_clock=CLOCK)
        with pytest.raises(ValueError, match='exact_manifest'):
            validate_c11_setup(replace(batch, intervals=()))
        assert all(end is None or end <= batch.scenario_time for _, _, end in batch.intervals)
    boundary = compile_c11_setup('C11-14', SETUPS['C11-14'][0], scenario_clock=CLOCK)
    assert boundary.intervals[0][2] == boundary.scenario_time
    assert boundary.specs[0][4] == '旧便签'  # Known marker only; no invented private body.


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', sorted(SPECS))
async def test_actual_job_nonempty_before_expiry_and_current_reopen_exclusion(tmp_path, case_id):
    host = await _host(tmp_path/'host')
    batch = compile_c11_setup(case_id, SETUPS[case_id][0], scenario_clock=CLOCK)
    args = dict(path=host.path, memory_path=tmp_path/'memory.db', principal=local_memory_principal(),
        authority_ref=AUTH.authority_ref, classification_policy=host_classification_policy(),
        supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES)
    async with open_c11_fixture(**args, batch=batch) as (_, actual):
        assert actual['outcome'].value == 'applied' and actual['fixture_executions'] == 1
        assert actual['application'].receipt.validation_status.value == 'accepted'
        assert actual['application'].receipt.committed_at == batch.ingestion_time
        assert len(actual['graph_before'].nodes) == 1 and actual['graph_after'].nodes == ()
        assert (await host.service.queue_snapshot())['turns'] == []
        operations = {op.operation_id: op for op in actual['plan'].operations}
        for label, start, end in batch.intervals:
            interval = operations[label].valid_time_interval
            assert (interval.valid_from, interval.valid_until) == (start, end)
            assert operations[label].lifecycle_state.value == 'active'  # Time exclusion, no state rewrite.
        if case_id == 'C11-13':
            assert len(actual['labels']) == 2
            assert actual['graph_before'].nodes[0].memory_id == actual['labels']['A'].memory_id
    runtime = HumanMemoryV7Runtime(args['memory_path'], evidence_authority=HostEvidenceAuthority(host.path),
        clock=lambda: batch.scenario_time)
    try:
        manager = await runtime.manager()
        assert await manager.get_memory_mutation_receipt_view(principal=runtime.principal(),
            receipt_ref=actual['receipt_ref']) == actual['receipt']
        assert await HostEvidenceAuthority(host.path).read_admitted(actual['source_pair'][0].evidence_id) == actual['source_pair']
        for predicate in {spec[3] for spec in batch.specs}:
            lanes = await runtime.typed_recall(query=predicate, run_id='c11-current-'+predicate,
                turn_ordinal=1, memory_types=('semantic',), include_short_horizon=False)
            assert lanes.execution.result.items == (), 'expired/future history is not current recall'
    finally:
        await runtime.close()
