"""Narrow plan-contract probes; does not open or mutate product databases.

Run: python -B reproduce_review.py /absolute/path/to/extracted/arp-1.1
"""
import copy
import json
from pathlib import Path
import sqlite3
import sys

kit = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(kit))
from tests.sql_fixture import execution
from reference.schema_codec import decode
from reference.runtime_rules import canonical

out = []
c = execution()
c.execute("UPDATE arp_agent_sessions SET state='ACTIVE',row_version=2 WHERE session_id='s'")
c.execute("UPDATE arp_agent_sessions SET state='DRAINING',generation=2,row_version=3,destroy_command_id='destroy',destroy_command_hash=? WHERE session_id='s'", ('3' * 64,))
c.execute("UPDATE arp_agent_sessions SET state='PURGING',row_version=4,sealed_highwater=1,delete_proof_ref_json='{}' WHERE session_id='s'")
try:
    c.execute("UPDATE arp_agent_sessions SET state='QUARANTINED',generation=3,row_version=5 WHERE session_id='s'")
    outcome = 'ALLOWED'
except sqlite3.IntegrityError as exc:
    outcome = str(exc)
out.append({'case': 'R1_PURGING_TO_QUARANTINED', 'observed': outcome,
            'state_after': c.execute('SELECT state,generation,row_version FROM arp_agent_sessions').fetchone()})
c.close()
base = json.loads((kit / 'examples/typed-fixtures.json').read_text())['fixtures']['SearchPage']
for mode in ['SCANNING_COMPLETE_EMPTY', 'PARTIAL_INDEX_COMPLETE_EMPTY']:
    value = copy.deepcopy(base)
    coverage = value['receipt']['coverage']
    if mode == 'SCANNING_COMPLETE_EMPTY':
        coverage.update(phase='SCANNING', rank_scope='NONE', ranking_final=False, snapshot_chunks=1)
        value.update(has_more=True, next_cursor='review-token', page_semantics='PROGRESS')
    else:
        coverage.update(index_coverage='PARTIAL', expected_groups=1)
    for _ in range(4):
        value['body_bytes'] = len(canonical(value))
    try:
        decode('SearchPage', canonical(value))
        outcome = 'ACCEPTED'
    except Exception as exc:
        outcome = repr(exc)
    out.append({'case': 'R2_' + mode, 'expected': 'REJECT', 'observed': outcome})
print(json.dumps({'scope': 'PLAN_CONTRACT_ONLY_NOT_PRODUCT', 'results': out}, ensure_ascii=False, indent=2))
