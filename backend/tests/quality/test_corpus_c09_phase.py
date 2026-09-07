"""New dispatcher combinations; do not repeat nineteen preparation controls."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
import simple_harness


@pytest.mark.parametrize('case_id', ['C09-02', 'C09-20'])
def test_actual_dispatcher_superseded_setup_then_isolated_request(tmp_path, case_id):
    host = Path(__file__).resolve().parents[3]
    target = Path(simple_harness.__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=os.pathsep.join((str(target), str(host / 'backend'))))
    log = tmp_path / 'c09-phase-child.log'
    with log.open('xb') as handle:
        child = subprocess.run([sys.executable, str(Path(__file__).with_name('c09_phase_child.py')),
            str(tmp_path / '.local-test-evidence' / 'c09-dispatch'), str(host), case_id],
            env=env, stdout=handle, stderr=subprocess.STDOUT, timeout=90, check=False)
    assert child.returncode == 0, f'see {log}; original child output retained'
