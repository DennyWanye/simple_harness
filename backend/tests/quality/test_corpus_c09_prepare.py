"""New public C09 preparation controls, not real-model quality or native proof."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
import simple_harness as h
import simple_harness_memory as m
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_v7 import (HumanMemoryV7Runtime, local_memory_principal,
    host_classification_policy, HOST_SUPPORTED_FILTER_POLICIES)
from deskpet.quality.corpus_c09 import SETUPS, CHANGES, compile_c09_setup, validate_c09_setup
from deskpet.quality.corpus_c09_prepare import open_c09_fixture, SupersededActionAuthority
from tests.quality.test_corpus_c01_revision_clock import _host, AUTH, CLOCK

ORIGINAL = Path('/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/'
    'quality/recall-corpus-candidate/review-zh/successor-12x20/09-superseded.md')


def test_original_setup_only_compiler_rejects_drift_and_unprepared_procedure():
    originals, case_id = {}, None
    for line in ORIGINAL.read_text().splitlines():
        if line.startswith('## C09-'):
            case_id = line.split('｜', 1)[0][3:]
        if line.startswith('**setup（模型初始不可见）：** '):
            originals[case_id] = line.split('** ', 1)[1]
    assert set(originals) == set(SETUPS) == {f'C09-{i:02}' for i in range(1, 21)}
    assert set(CHANGES) == set(originals) - {'C09-13'}
    for case_id, original in originals.items():
        assert SETUPS[case_id] == (original, sha256(original.encode()).hexdigest())
        with pytest.raises(ValueError, match='source_changed'):
            compile_c09_setup(case_id, original + 'new value from oracle', scenario_clock=CLOCK)
        if case_id == 'C09-13':
            with pytest.raises(ValueError, match='procedure_successor_not_prepared'):
                compile_c09_setup(case_id, original, scenario_clock=CLOCK)
            continue
        batch = compile_c09_setup(case_id, original, scenario_clock=CLOCK)
        with pytest.raises(ValueError, match='exact_compiled_setup'):
            validate_c09_setup(replace(batch, changes=()))
    assert compile_c09_setup('C09-02', SETUPS['C09-02'][0], scenario_clock=CLOCK).changes[0][2] is None
    assert compile_c09_setup('C09-18', SETUPS['C09-18'][0], scenario_clock=CLOCK).changes[0][2] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', sorted(CHANGES))
async def test_actual_original_job_and_public_successor_survive_reopen(tmp_path, case_id):
    host = await _host(tmp_path / 'host')
    batch = compile_c09_setup(case_id, SETUPS[case_id][0], scenario_clock=CLOCK)
    args = dict(path=host.path, memory_path=tmp_path/'memory.db', principal=local_memory_principal(),
        authority_ref=AUTH.authority_ref, classification_policy=host_classification_policy(),
        supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES)
    async with open_c09_fixture(**args, batch=batch) as (manager, actual):
        assert actual['outcome'].value == 'applied' and actual['fixture_executions'] == 1
        assert actual['application'].receipt.validation_status is h.AnalysisValidationStatus.ACCEPTED
        assert len(actual['graph_before'].nodes) == len(batch.specs) > 0
        assert actual['source_pair'][0].sanitized_payload['text'] == batch.setup_text
        assert (await host.service.queue_snapshot())['turns'] == []
        # Replay the actual authorized mutation, not a reconstructed receipt.
        result = await manager.apply_memory_mutation_plan(principal=args['principal'],
            scope=m.MemoryScope.personal(args['principal'].actor_id),
            plan=actual['plan'])
        assert result.receipt_ref == actual['new_receipt_ref']
        if case_id == 'C09-01':
            # An unissued plan with a value not in the authored setup cannot
            # obtain authority, even when it cites these genuine old receipts.
            op = replace(actual['plan'].operations[0], action_authority_ref=None,
                payload=h.SemanticMemoryPayload('user:self', 'service_port', '9999', ('同一服务',)))
            wrong = replace(actual['plan'], operations=(op,))
            issuer = SupersededActionAuthority(path=host.path, batch=batch,
                principal=args['principal'], manager_getter=lambda: manager)
            with pytest.raises(ValueError, match='c09_revision_exact_successor_required'):
                await issuer.authorize(plan=wrong, original=actual['initial_plan'],
                    prior_ref=actual['old_receipt_ref'],
                    committed_revision=actual['application'].receipt.committed_revision,
                    source=actual['source_pair'][0], proof=actual['source_pair'][1])
    runtime = HumanMemoryV7Runtime(args['memory_path'], evidence_authority=HostEvidenceAuthority(host.path),
        clock=lambda: batch.scenario_time)
    try:
        manager = await runtime.manager()
        for name in ('old', 'new'):
            assert await manager.get_memory_mutation_receipt_view(principal=runtime.principal(),
                receipt_ref=actual[name+'_receipt_ref']) == actual[name+'_receipt']
        for i, (predicate, old, new, _) in enumerate(batch.changes):
            lanes = await runtime.typed_recall(query=predicate, run_id=f'c09-public-{i}', turn_ordinal=1,
                memory_types=('semantic',), include_short_horizon=False)
            selected = lanes.execution.result.items
            if new is None:
                assert selected == (), 'retired claim must not be an ordinary current result'
            else:
                item = actual['new_receipt'].operations[i]
                assert len(selected) == 1
                assert selected[0].selected_item.source_ref == item.memory_id
                assert selected[0].selected_item.source_revision == 2
                assert selected[0].selected_item.source_content_hash == item.content_hash
        for i, (predicate, _, _) in enumerate(batch.unchanged):
            original = next(op for op in actual['old_receipt'].operations if op.operation_id == f'unchanged-{i}')
            lanes = await runtime.typed_recall(query=predicate, run_id=f'c09-unchanged-{i}', turn_ordinal=1,
                memory_types=('semantic',), include_short_horizon=False)
            selected = lanes.execution.result.items
            assert len(selected) == 1
            assert selected[0].selected_item.source_ref == original.memory_id
            assert selected[0].selected_item.source_revision == 1
        assert await HostEvidenceAuthority(host.path).read_admitted(actual['source_pair'][0].evidence_id) == actual['source_pair']
    finally:
        await runtime.close()
