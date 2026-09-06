"""Public C08 setup/suppression controls. No remote model or quality verdict."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest
import simple_harness as h
import simple_harness_memory as m
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_v7 import (
    local_memory_principal, host_classification_policy, HOST_SUPPORTED_FILTER_POLICIES,
)
from deskpet.quality.corpus_c08 import SETUPS, FACTS, compile_c08_setup, validate_c08_setup
from deskpet.quality.corpus_c08_prepare import prepare_c08_seed
from deskpet.quality.corpus_setup_jobs import SetupFixtureDeliveryAuthority
from deskpet.sdk_adapters.context_route import local_owner_auth
from tests.memory.test_primary_read_api import setup

ORIGINAL = Path('/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/'
    'quality/recall-corpus-candidate/review-zh/successor-12x20/08-suppressed.md')
CLOCK = '2026-09-06T10:00:00+08:00'


def test_original_setup_boundary_and_unprepared_derived_cases():
    originals, case_id = {}, None
    for line in ORIGINAL.read_text().splitlines():
        if line.startswith('## C08-'):
            case_id = line.split('｜', 1)[0][3:]
        if line.startswith('**setup（模型初始不可见）：** '):
            originals[case_id] = line.split('** ', 1)[1]
    assert set(originals) == set(SETUPS) == {f'C08-{n:02}' for n in range(1, 21)}
    assert len(FACTS) == 12
    for case_id, original in originals.items():
        assert SETUPS[case_id] == (original, sha256(original.encode()).hexdigest())
        if case_id not in FACTS:
            with pytest.raises(ValueError, match='derived_source_not_prepared'):
                compile_c08_setup(case_id, original, scenario_clock=CLOCK)
            continue
        compiled = compile_c08_setup(case_id, original, scenario_clock=CLOCK)
        validate_c08_setup(compiled)
        with pytest.raises(ValueError, match='exact_compiled_setup'):
            validate_c08_setup(replace(compiled, specs=()))
        with pytest.raises(ValueError, match='setup_source_changed'):
            compile_c08_setup(case_id, {'setup': original, 'gold': 'forbidden'}, scenario_clock=CLOCK)


@pytest.mark.asyncio
@pytest.mark.parametrize('case_id', sorted(FACTS))
async def test_real_nonempty_seed_then_suppression_survives_reopen(tmp_path, monkeypatch, case_id):
    import tests.memory.test_primary_read_api as read_fixture
    monkeypatch.setattr(read_fixture, 'AUTH', local_owner_auth())
    host = await setup(tmp_path / 'host')
    batch = compile_c08_setup(case_id, SETUPS[case_id][0], scenario_clock=CLOCK)
    principal = local_memory_principal()
    delivery = SetupFixtureDeliveryAuthority()
    memory_path = tmp_path / 'memory.db'
    kwargs = dict(classification_policy=host_classification_policy(),
        supported_filter_policies=HOST_SUPPORTED_FILTER_POLICIES,
        evidence_authority=HostEvidenceAuthority(host.path), clock=lambda: batch.scenario_time)
    manager = await m.build_human_memory_v7(memory_path, analysis_delivery_authority=delivery, **kwargs)
    try:
        result = await prepare_c08_seed(path=host.path, manager=manager, principal=principal,
            authority_ref=local_owner_auth().authority_ref, batch=batch, delivery_authority=delivery)
        assert result['application'].receipt.validation_status is h.AnalysisValidationStatus.ACCEPTED
        assert result['fixture_executions'] == 1
        assert len(result['graph_before'].nodes) == 1
        assert result['graph_before'].nodes[0] == result['labels']['A']
        assert result['labels']['A'].memory_type == FACTS[case_id][0]
        assert result['graph_after'].nodes == ()
        source, proof = result['source_pair']
        assert result['request'].ordered_evidence_refs == result['plan'].evidence_refs == (
            h.EvidenceRef(source.evidence_id, source.envelope_hash, 1),)
        assert source.sanitized_payload['text'] == SETUPS[case_id][0]
    finally:
        await manager.close()
    reopened = await m.build_human_memory_v7(memory_path, **kwargs)
    try:
        graph = await reopened.get_twin_graph_view(principal=principal)
        assert graph.nodes == () and graph.edges == ()
        # Public idempotent receipt proves a persisted decision, not just an
        # empty rebuilt graph. Original evidence stays byte-for-byte intact.
        replay = await reopened.suppress(principal=principal, request=result['suppression_request'])
        assert replay == result['suppression_decision']
        assert await HostEvidenceAuthority(host.path).read_admitted(source.evidence_id) == (source, proof)
    finally:
        await reopened.close()
