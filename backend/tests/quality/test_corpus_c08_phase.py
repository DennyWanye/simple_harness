"""One new dispatcher control; the five retained-helper controls are not repeated."""
import os
from pathlib import Path
import subprocess
import sys

import simple_harness


def test_actual_dispatcher_retained_phase_then_separate_scoring_request(tmp_path):
    host = Path(__file__).resolve().parents[3]
    target = Path(simple_harness.__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=os.pathsep.join((str(target), str(host / 'backend'))))
    directory = tmp_path / '.local-test-evidence' / 'retained-dispatch'
    log = tmp_path / 'c08-dispatch-child.log'
    with log.open('xb') as handle:
        child = subprocess.run([sys.executable, str(Path(__file__).with_name('c08_phase_child.py')),
            str(directory), str(host)], env=env, stdout=handle, stderr=subprocess.STDOUT,
            timeout=90, check=False)
    assert child.returncode == 0, f'see {log}; retain original execution/phase on failure'

