"""New retained-source main controls only; each case owns a fresh interpreter.

Run on the main candidate's installed H0710/M619 under the default resource
runner. No scalar controls, remote models, copied credentials or new target.
"""
from importlib.metadata import version
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import simple_harness
import simple_harness_memory

from deskpet.quality.corpus_c08_retained import compile_c08_retained_setup


CORPUS = Path('/Users/denny/projects/simple-harness-memory-sdk/plans/2026-08-29-human-memory-digital-twin/'
    'quality/recall-corpus-candidate/review-zh/successor-12x20/08-suppressed.md')
TARGET = Path('/Users/denny/projects/simple_harness-primary-candidate/.local-test-evidence/'
    '2026-09-07/harness0710-artifact/installed')
CLOCK = '2026-09-06T10:00:00+08:00'


def _source_fields(case_id):
    """Extract only the original setup/current-input lines, never the oracle."""
    active, fields = False, {}
    with CORPUS.open(encoding='utf-8') as source:
        for line in source:
            if line.startswith('## '):
                if active:
                    break
                active = line.startswith('## ' + case_id + '｜')
            if not active:
                continue
            for prefix, key in (
                ('**setup（模型初始不可见）：** ', 'setup'),
                ('**provider_input（初始可见）：** ', 'current_user_message'),
            ):
                if line.startswith(prefix):
                    assert key not in fields
                    fields[key] = line.removeprefix(prefix).rstrip('\r\n')
            if len(fields) == 2:
                break  # Do not consume this case's following gold line.
    assert set(fields) == {'setup', 'current_user_message'} and fields['current_user_message']
    compile_c08_retained_setup(case_id, fields['setup'], scenario_clock=CLOCK)
    return fields


@pytest.mark.parametrize('case_id,mode', [
    ('C08-01', 'retained'), ('C08-06', 'retained'),
    ('C08-11', 'retained'), ('C08-18', 'retained'),
    ('C08-01', 'wrong-assistant'),
], ids=['C08-01', 'C08-06', 'C08-11', 'C08-18', 'wrong-assistant'])
def test_actual_main_retained_source_then_current_request(tmp_path, case_id, mode):
    assert version('simple-harness-sdk') == '0.7.10'
    assert version('simple-harness-memory-sdk') == '0.6.19'
    assert Path(simple_harness.__file__).resolve().is_relative_to(TARGET)
    assert Path(simple_harness_memory.__file__).resolve().is_relative_to(TARGET)
    host = Path(__file__).resolve().parents[3]
    directory = tmp_path / 'main-retained'
    directory.mkdir()
    control = dict(case_id=case_id, mode=mode, scenario_clock=CLOCK, **_source_fields(case_id))
    (directory/'control.json').write_text(json.dumps(control, ensure_ascii=False))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=os.pathsep.join((str(TARGET), str(host/'backend'))))
    child = Path(__file__).with_name('c08_retained_main_child.py')
    # No new session/process group: the shared resource owner includes child
    # and descendants. A natural exit is required; no pytest teardown in child.
    with (directory/'child.log').open('xb') as log:
        completed = subprocess.run([sys.executable, str(child), str(directory), str(host)],
            env=env, stdout=log, stderr=subprocess.STDOUT, timeout=90, check=False)
    assert completed.returncode == 0, 'see main-retained/child.log and result.json'
    outcome = json.loads((directory/'result.json').read_text())
    assert outcome['cleanup_errors'] == []
    assert outcome['mapping_rejected'] is True
    assert outcome['fixture_posts'] == 1 and outcome['remote_calls'] == 0
    if mode == 'wrong-assistant':
        assert outcome['status'] == 'WRONG_ASSISTANT_REJECTED'
        assert outcome['scoring_posts'] == 0 and outcome['seed_started'] is False
        assert outcome['actual_wrong_group_completed'] is True
    else:
        assert outcome['status'] == 'RETAINED_THEN_CURRENT_COMPLETED'
        assert outcome['job_outcomes_before_current'] == ['applied', 'idle']
        assert outcome['scoring_posts'] == 1 and outcome['production_manager_reopened'] is True
        assert outcome['original_source_retained'] is True
        assert outcome['setup_sdk_run_id'] != outcome['current_sdk_run_id']
        assert outcome['setup_provider_invocation_id'] != outcome['current_provider_invocation_id']
        assert outcome['current_messages'] == [{'role':'user', 'content':control['current_user_message']}]
    assert outcome['local_provider_closed'] is True and outcome['models_loaded'] == 0
