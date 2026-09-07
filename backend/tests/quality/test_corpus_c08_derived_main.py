"""Five new derived-carrier controls; reuse the sole actual-main child."""
from dataclasses import replace
from datetime import datetime
import json

import pytest

from deskpet.quality.corpus_c08 import SETUPS
from deskpet.quality.corpus_c08_derived import CARRIERS, extra_payload
from deskpet.quality.corpus_c08_retained import compile_c08_retained_setup, validate_retained_setup
from tests.quality.test_corpus_c08_retained_main import (
    CLOCK, _source_fields,
    test_actual_main_retained_source_then_current_request as _run_actual_main,
)


@pytest.mark.parametrize('case_id', ['C08-02', 'C08-04', 'C08-09', 'C08-13', 'C08-19'])
def test_actual_main_derived_carrier_forget_reopen_current(tmp_path, case_id):
    source = _source_fields(case_id)
    batch = compile_c08_retained_setup(case_id, source['setup'], scenario_clock=CLOCK)
    spec = CARRIERS[case_id]
    with pytest.raises(ValueError, match='^c08_setup_source_changed$'):
        compile_c08_retained_setup(case_id, source['setup'] + ' ', scenario_clock=CLOCK)
    with pytest.raises(ValueError, match='^c08_retained_exact_manifest_required$'):
        validate_retained_setup(replace(batch, value='not-the-original-fact'))
    # Do not backfill the missing alias target using the current question.
    if case_id == 'C08-02':
        with pytest.raises(ValueError, match='^c08_retained_case_not_supported$'):
            compile_c08_retained_setup('C08-20', SETUPS['C08-20'][0], scenario_clock=CLOCK)

    _run_actual_main(tmp_path, case_id, 'retained')
    directory = tmp_path / 'main-retained'
    seed = json.loads((directory / 'seed.json').read_text())
    phase = json.loads((directory / 'retained-phase/phase.json').read_text())
    assert phase['kind'] == 'deterministic_retained_derived_carrier'
    assert seed['fixture_defaults'] == ['authored-setup-only-old-group', spec.kind]
    assert seed['derived_carrier']['derived_payload'] == extra_payload(batch).to_json()
    group = seed['completed_group']
    user, assistant = group['registrations']
    assert user['envelope']['evidence_id'] != assistant['envelope']['evidence_id']
    assert group['terminal_source'][0]['sanitized_payload']['messages'] == [
        {'role': 'user', 'content': spec.user}, {'role': 'assistant', 'content': spec.assistant}]
    ops = {op['operation_id']: op for op in seed['mutation_receipt']['operations']}
    label = 'P' if case_id == 'C08-13' else 'D'
    assert set(ops) == {'A', label}
    assert ops['A']['memory_id'] != ops[label]['memory_id']
    assert all(op['evidence_ids'] == [user['envelope']['evidence_id']] for op in ops.values())
    assert {node['memory_id'] for node in seed['graph_before']['nodes']} == {op['memory_id'] for op in ops.values()}
    assert seed['graph_before']['edges'] == []  # associations are claims, not APPLIES_TO edges
    assert [item['visible'] for item in seed['visibility_before']['items']] == [True, True]
    assert [item['reason'] for item in seed['visibility_after']['items']] == ['history_suppressed'] * 2
    assert seed['graph_after']['nodes'] == [] and seed['graph_after']['edges'] == []
    assert seed['entity_relation_edge_exercised'] is False
    assert seed['short_generation_exercised'] is False
    if case_id != 'C08-13':
        assert ops['D']['memory_type'] == 'semantic'
        assert seed['derived_carrier']['derived_payload']['object_value'] == dict(spec.fields)
        assert seed['reminder_carrier'] is None
        return
    assert ops['P']['memory_type'] == 'prospective'
    p_node = next(node for node in seed['graph_before']['nodes'] if node['memory_id'] == ops['P']['memory_id'])
    assert p_node['lifecycle_state'] == 'pending'
    assert extra_payload(batch).trigger.trigger_at == datetime.fromisoformat('2027-04-17T12:00:00+08:00').timestamp()
    reminder = seed['reminder_carrier']
    registration = reminder['registration']
    target = reminder['source']
    assert registration['result']['outcome'] == 'acknowledged'
    assert registration['authority']['intent']['scheduler_registration_ref']
    assert target['target_memory_id'] == ops['P']['memory_id'] and target['target_revision'] == 1
    assert target['target_source']['mutation_receipt_ref'] == seed['mutation_receipt_ref']
    assert target['target_source']['operation_id'] == 'P'
    reopened = json.loads((directory / 'reminder-reopen.json').read_text())
    assert reopened['historical_authority_only'] is True
    assert reopened['registration'] == registration
    # Observation metadata may differ; exact target identity/source facts may not.
    for key in ('outbox_id', 'outbox_payload_hash', 'target_memory_id', 'target_revision', 'target_source'):
        assert reopened['source'][key] == target[key]
    assert not reminder['timer_fired'] and not reminder['future_delivery_exercised']
    assert not reminder['registration_cancelled']
