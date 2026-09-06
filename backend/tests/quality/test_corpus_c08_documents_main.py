"""Two new historical-document cases over the existing actual-main child.

Source only until main grants execution on its current installed target. No
copy of main initialization, no old retained/scalar/dispatcher parametrization.
"""
from dataclasses import replace
import json

import pytest

from deskpet.quality.corpus_c08_documents import DOCUMENTS
from deskpet.quality.corpus_c08_retained import (
    compile_c08_retained_setup, retained_carrier_kind, validate_retained_setup,
)
from tests.quality.test_corpus_c08_retained_main import (
    CLOCK, _source_fields,
    test_actual_main_retained_source_then_current_request as _run_actual_main,
)


@pytest.mark.parametrize('case_id', ['C08-15', 'C08-16'])
def test_actual_main_document_source_forget_reopen_current(tmp_path, case_id):
    source = _source_fields(case_id)  # original setup/current only, not gold
    batch = compile_c08_retained_setup(case_id, source['setup'], scenario_clock=CLOCK)
    document = DOCUMENTS[case_id]
    assert retained_carrier_kind(batch) == document.kind
    # Refuse source drift and cross-case document substitution before any
    # runtime is created. No mutation of either original MD or shared mappings.
    with pytest.raises(ValueError, match='^c08_setup_source_changed$'):
        compile_c08_retained_setup(case_id, source['setup'] + ' ', scenario_clock=CLOCK)
    other = DOCUMENTS['C08-16' if case_id == 'C08-15' else 'C08-15']
    with pytest.raises(ValueError, match='^c08_retained_exact_manifest_required$'):
        validate_retained_setup(replace(batch,
            messages=(batch.messages[0], ('assistant', other.assistant))))

    # Reuse the same public main/S1/job/suppression/owned-manager reopen child
    # and its physical next-request check. Calling the function directly does
    # not execute its four old-case/wrong-assistant pytest parametrizations.
    _run_actual_main(tmp_path, case_id, 'retained')
    directory = tmp_path / 'main-retained'
    seed = json.loads((directory / 'seed.json').read_text())
    phase = json.loads((directory / 'retained-phase/phase.json').read_text())
    assert phase['kind'] == 'deterministic_retained_document'
    assert seed['fixture_defaults'] == ['authored-setup-only-old-group', document.kind]
    group = seed['completed_group']
    terminal = group['terminal_source'][0]['sanitized_payload']
    assert terminal['terminal_state'] == 'COMPLETED'
    assert terminal['messages'] == [
        {'role': 'user', 'content': document.user},
        {'role': 'assistant', 'content': document.assistant},
    ]
    user, assistant = group['registrations']
    assert user['envelope']['evidence_id'] != assistant['envelope']['evidence_id']
    assert seed['mutation_receipt']['operations'][0]['evidence_ids'] == [user['envelope']['evidence_id']]
    assert len(seed['graph_before']['nodes']) == 1
    assert [item['visible'] for item in seed['visibility_before']['items']] == [True, True]
    assert [item['reason'] for item in seed['visibility_after']['items']] == ['history_suppressed'] * 2
    assert seed['graph_after']['nodes'] == [] and seed['graph_after']['edges'] == []
    assert seed['short_generation_exercised'] is False
