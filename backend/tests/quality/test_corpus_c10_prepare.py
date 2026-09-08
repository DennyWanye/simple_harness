"""C10 contested-state source controls, not model quality or confirmation coverage."""
from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path

import pytest
import simple_harness as h
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_v7 import (HumanMemoryV7Runtime, local_memory_principal,
    host_classification_policy, HOST_SUPPORTED_FILTER_POLICIES)
from deskpet.quality.corpus_c10 import (SETUPS, SLOTS, SOURCES, EXTRAS, compile_c10_setup,
    validate_c10_setup, RESTRICTED_CASES, SUPPRESSED_INCUMBENT_CASES)
from deskpet.quality.corpus_c10_prepare import open_c10_fixture, current_head
from deskpet.quality.corpus_scoring import supported_case_ids
from tests.quality.test_corpus_c01_revision_clock import _host, AUTH, CLOCK

ORIGINAL_CANDIDATES = [
    os.environ.get('CORPUS_MEMORY_SDK_ROOT'),
    '/Users/taiwan/PROJECTS/SimplaHarness/simple-harness-memory-sdk',
    '/Users/denny/projects/simple-harness-memory-sdk',
]
ORIGINAL_RELATIVE = ('plans/2026-08-29-human-memory-digital-twin/quality/recall-corpus-candidate/'
                     'review-zh/successor-12x20/10-contested-not-required.md')


def _original():
    for root in ORIGINAL_CANDIDATES:
        if root and (Path(root) / ORIGINAL_RELATIVE).is_file():
            return Path(root) / ORIGINAL_RELATIVE
    pytest.skip('original C10 corpus markdown unavailable on this machine')


def batch(case_id):
    return compile_c10_setup(case_id, SETUPS[case_id][0], scenario_clock=CLOCK)


def test_all_twenty_original_setups_and_input_boundary():
    # Compare ONLY original setup fields, not gold or generated answers.
    originals, current, inputs = {}, None, {}
    for line in _original().read_text().splitlines():
        if line.startswith('## C10-'):
            current = line.split('｜', 1)[0][3:]
        if line.startswith('**setup（模型初始不可见）：** '):
            originals[current] = line.split('** ', 1)[1]
        if line.startswith('**provider_input（初始可见）：** '):
            inputs[current] = line.split('** ', 1)[1]
    assert set(originals) == set(SETUPS) == set(SLOTS) == set(SOURCES) == {f'C10-{n:02}' for n in range(1, 21)}
    for case_id, text in originals.items():
        assert SETUPS[case_id] == (text, sha256(text.encode()).hexdigest())
        compiled = batch(case_id)
        validate_c10_setup(compiled)
        # Fixture prose is synthetic: never the setup line, never the user's turn.
        assert compiled.incumbent_text != text and compiled.challenger_text != text
        assert compiled.incumbent_text != compiled.challenger_text
        assert inputs[case_id] not in compiled.incumbent_text and inputs[case_id] not in compiled.challenger_text
        assert compiled.incumbent_time < compiled.contest_time < compiled.scenario_time
        with pytest.raises(ValueError, match='source_changed'):
            compile_c10_setup(case_id, {'setup': text, 'gold': 'forbidden'}, scenario_clock=CLOCK)
        with pytest.raises(ValueError, match='exact_compiled'):
            validate_c10_setup(replace(compiled, challenger_text='tampered'))
        for slot in compiled.slots:
            assert slot.incumbent != slot.challenger or slot.kind == 'procedure'
    assert batch('C10-11').slots[0].kind == 'procedure'
    assert batch('C10-12').slots.__len__() == 3
    assert batch('C10-16').scenario_time - batch('C10-16').contest_time == 60.0
    assert batch('C10-14').privacy_class == 'restricted' and RESTRICTED_CASES == {'C10-14'}
    assert batch('C10-15').suppress_incumbent and SUPPRESSED_INCUMBENT_CASES == {'C10-15'}
    assert [e.label for e in batch('C10-20').extras] == ['H'] and EXTRAS.keys() == {'C10-20'}
    assert {c for c in supported_case_ids() if c.startswith('C10-')} == set(SETUPS)


def _args(host, tmp_path):
    return dict(path=host.path, memory_path=tmp_path / 'memory.db', principal=local_memory_principal(),
        authority_ref=AUTH.authority_ref, classification_policy=host_classification_policy(),
        supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES)


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', ['C10-01', 'C10-11', 'C10-12', 'C10-16', 'C10-20'])
async def test_actual_two_job_contest_creates_public_conflict_group(tmp_path, case_id):
    host = await _host(tmp_path / 'host')
    compiled = batch(case_id)
    async with open_c10_fixture(**_args(host, tmp_path), batch=compiled) as (_, actual):
        assert actual['fixture_executions'] == 2 and actual['outcome'].value == 'applied'
        assert actual['application'].receipt.validation_status.value == 'accepted'
        assert actual['incumbent_application'].receipt.committed_at == compiled.incumbent_time
        assert actual['application'].receipt.committed_at == compiled.contest_time
        incumbent, challenger = actual['source_pairs']['incumbent'], actual['source_pairs']['challenger']
        assert incumbent[0].evidence_id != challenger[0].evidence_id
        assert incumbent[0].sanitized_payload['text'] == compiled.incumbent_text
        assert challenger[0].sanitized_payload['text'] == compiled.challenger_text
        for slot in compiled.slots:
            old, new = actual['labels'][slot.label], actual['labels']['contest-' + slot.label]
            assert (old.memory_id, old.revision, new.revision) == (new.memory_id, 1, 2)
            assert old.memory_type == new.memory_type == slot.kind
            assert old.evidence_ids == (incumbent[0].evidence_id,)
            assert new.evidence_ids == (challenger[0].evidence_id,)
        assert all(op.kind.value == 'contest' for op in actual['plan'].operations)
        assert all(op.kind.value == 'create' for op in actual['initial_plan'].operations)
        # The twin graph lists every revision; the head of each slot is revision 2.
        for slot in compiled.slots:
            node = current_head(actual['graph_after'], actual['labels'][slot.label].memory_id)
            assert (node.revision, node.conflict_status, node.lifecycle_state) == (2, 'contested', 'active')
            revisions = {n.revision for n in actual['graph_after'].nodes if n.memory_id == node.memory_id}
            assert revisions == {1, 2}
        for extra in compiled.extras:
            node = current_head(actual['graph_after'], actual['labels'][extra.label].memory_id)
            assert (node.revision, node.conflict_status) == (1, 'uncontested')
        assert len({n.memory_id for n in actual['graph_after'].nodes}) == len(compiled.slots) + len(compiled.extras)
        assert (await host.service.queue_snapshot())['turns'] == []
    runtime = HumanMemoryV7Runtime(tmp_path / 'memory.db', evidence_authority=HostEvidenceAuthority(host.path),
        clock=lambda: compiled.scenario_time)
    try:
        manager = await runtime.manager()
        assert await manager.get_memory_mutation_receipt_view(principal=runtime.principal(),
            receipt_ref=actual['new_receipt_ref']) == actual['new_receipt']
        assert await HostEvidenceAuthority(host.path).read_admitted(incumbent[0].evidence_id) == incumbent
        assert await HostEvidenceAuthority(host.path).read_admitted(challenger[0].evidence_id) == challenger
        graph = await manager.get_twin_graph_view(principal=runtime.principal())
        assert {(n.memory_id, n.revision, n.conflict_status) for n in graph.nodes} == {
            (n.memory_id, n.revision, n.conflict_status) for n in actual['graph_after'].nodes}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_restricted_members_are_ineligible_for_ordinary_self_recall(tmp_path):
    host = await _host(tmp_path / 'host')
    compiled = batch('C10-14')
    async with open_c10_fixture(**_args(host, tmp_path), batch=compiled) as (_, actual):
        for slot in compiled.slots:
            assert actual['labels'][slot.label].memory_id == actual['labels']['contest-' + slot.label].memory_id
            assert actual['labels']['contest-' + slot.label].revision == 2
        # RESTRICTED joins above the PERSONAL floor: the SDK withholds both members
        # from the ordinary graph; the receipt view is their only public proof.
        assert actual['graph_contested'].nodes == () and actual['graph_after'].nodes == ()
    runtime = HumanMemoryV7Runtime(tmp_path / 'memory.db', evidence_authority=HostEvidenceAuthority(host.path),
        clock=lambda: compiled.scenario_time)
    try:
        lanes = await runtime.typed_recall(query='受限小组成员', run_id='c10-14-current', turn_ordinal=1,
            memory_types=('semantic',), include_short_horizon=False)
        result = lanes.execution.result
        assert result.items == () and result.confirmation_groups == (), \
            'restricted members never enter ordinary self recall, not even as a confirmation'
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_contested_group_reaches_recall_only_as_confirmation(tmp_path):
    host = await _host(tmp_path / 'host')
    compiled = batch('C10-01')
    async with open_c10_fixture(**_args(host, tmp_path), batch=compiled) as (_, actual):
        memory_id = actual['labels']['A'].memory_id
    runtime = HumanMemoryV7Runtime(tmp_path / 'memory.db', evidence_authority=HostEvidenceAuthority(host.path),
        clock=lambda: compiled.scenario_time)
    try:
        lanes = await runtime.typed_recall(query='默认区域', run_id='c10-01-current', turn_ordinal=1,
            memory_types=('semantic',), include_short_horizon=False)
        result = lanes.execution.result
        # The SDK type-authority gate withholds a contested head from items and
        # offers the whole group as one confirmation: a query in C10 is exactly the
        # "unrelated confirmation" the gold forbids, so PASS requires zero query.
        assert result.items == ()
        assert len(result.confirmation_groups) == 1
        members = result.confirmation_groups[0].members
        assert [(m.member.source_ref, m.member.source_revision) for m in members] == [(memory_id, 1), (memory_id, 2)]
        assert {m.public_payload['object_value'] for m in members} == {'上海', '东京'}
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_suppressed_incumbent_source_hides_whole_group_and_records_visibility(tmp_path):
    host = await _host(tmp_path / 'host')
    compiled = batch('C10-15')
    async with open_c10_fixture(**_args(host, tmp_path), batch=compiled) as (_, actual):
        decision = actual['suppression_decision']
        source = actual['source_pairs']['incumbent']
        assert decision is not None and decision.scope_ref == source[0].evidence_id
        assert decision.action.value == 'directive'
        assert decision.effective_at == compiled.contest_time + 1.0
        # Before the suppression: one memory, both revisions, contested head.
        assert {n.memory_id for n in actual['graph_contested'].nodes} == {actual['labels']['A'].memory_id}
        assert current_head(actual['graph_contested'], actual['labels']['A'].memory_id).conflict_status == 'contested'
        # After: the SDK's own rule (recorded, never scored) hides the whole group.
        assert actual['graph_after'].nodes == ()
        assert actual['partial_group_visible_memory_ids'] == []
        assert await HostEvidenceAuthority(host.path).read_admitted(source[0].evidence_id) == source
    runtime = HumanMemoryV7Runtime(tmp_path / 'memory.db', evidence_authority=HostEvidenceAuthority(host.path),
        clock=lambda: compiled.scenario_time)
    try:
        lanes = await runtime.typed_recall(query='默认区域', run_id='c10-15-current', turn_ordinal=1,
            memory_types=('semantic',), include_short_horizon=False)
        result = lanes.execution.result
        assert result.items == () and result.confirmation_groups == ()
    finally:
        await runtime.close()
