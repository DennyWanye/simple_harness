"""New state-phase controls only; main's complete installed candidate runs them."""
import pytest

from test_corpus_c05_phase import _child


@pytest.mark.parametrize('case_id', ['C05-07', 'C05-08'])
def test_actual_main_state_source_scoring_phase(tmp_path, case_id):
    _child(tmp_path, case_id, 'state')


def test_actual_main_active_preview_cannot_switch_before_confirmation(tmp_path):
    _child(tmp_path, 'C05-07', 'early_selection')
