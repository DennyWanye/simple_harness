"""Source-only new controls; main runs these with its complete real candidate."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import simple_harness


@pytest.mark.parametrize('case_id', ['C05-04', 'C05-09', 'C05-14', 'C05-20'])
def test_actual_main_task_setup_scoring_and_authored_followups(tmp_path, case_id):
    _child(tmp_path, case_id, 'visible')


def test_actual_empty_preview_does_not_send_followup(tmp_path):
    _child(tmp_path, 'C05-20', 'empty')


def _child(tmp_path, case_id, mode, *, script_name='c05_phase_child.py'):
    # Fresh module-level main owners per case. No reload, registry rewrite or
    # borrowed-origin override; this is the main checkout's installed target.
    host = Path(__file__).resolve().parents[3]
    target = Path(simple_harness.__file__).resolve().parent.parent
    directory = tmp_path / '.local-test-evidence' / case_id
    directory.parent.mkdir()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=os.pathsep.join((str(target), str(host / 'backend'))))
    log = tmp_path / 'c05-phase-child.log'
    with log.open('xb') as handle:
        child = subprocess.run([sys.executable, str(Path(__file__).with_name(script_name)),
            str(directory), str(host), case_id, mode], env=env, stdout=handle,
            stderr=subprocess.STDOUT, timeout=120, check=False)
    assert child.returncode == 0, f'see {log}; original child failure is retained'
    control = json.loads((directory / 'control.json').read_text())
    assert control['mode'] == mode and control['cleanup_errors'] == []
