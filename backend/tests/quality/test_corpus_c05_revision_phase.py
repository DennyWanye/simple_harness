"""Only new C18 between-turn phase controls; main executes its real target."""
from test_corpus_c05_phase import _child


def test_actual_revision_between_preview_and_selection(tmp_path):
    _child(tmp_path, 'C05-18', 'revision', script_name='c05_revision_phase_child.py')


def test_actual_empty_preview_does_not_start_revision_phase(tmp_path):
    _child(tmp_path, 'C05-18', 'empty', script_name='c05_revision_phase_child.py')
