"""M0.6.31 confirmation-member history-visibility witness plus counterexamples; never 401 evidence."""
import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConflictGroupVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_durable_group_binding_passes_and_independent_negatives_fail(self):
        fixture = json.loads((ROOT / 'fixtures/typed-recall-v3.json').read_text())
        adapter = load(ROOT / 'adapters/typed_recall_conflict_cases.py')
        oracle = load(ROOT / 'runners/typed_recall_a2_oracle.py')
        conflict = fixture['conflict_write_oracle']
        resolution = next(row for row in conflict['resolution_cases'] if row['id'] == 'resolve-replacement')
        inputs = {'payloads': {name: {**value, 'qualifiers': []}
                               for name, value in conflict['canonical_payloads'].items()},
                  'cases': [{'id': row['id'], 'mutation': row.get('mutation', {})}
                            for row in (conflict['create_case'], resolution)]}
        with tempfile.TemporaryDirectory() as directory:
            rows = await adapter.run_cases(inputs, Path(directory))
        verdicts = {row['cell_id']: (row, oracle.assess_cell(fixture, row)) for row in rows}
        for name, (row, verdict) in verdicts.items():
            self.assertEqual(row['status'], 'OBSERVED')
            self.assertEqual(verdict['status'], 'PASS', (name, verdict))
        created = verdicts['conflict-state/contest-create-distinct-evidence'][0]
        resolved = verdicts['conflict-state/resolve-replacement'][0]

        # The live members must really be visible, the tampered member hash must really be rejected,
        # and the recorded bindings must be the exact public result/member identities.
        for attack in ('member_not_visible', 'tamper_accepted', 'binding_hash', 'binding_result_id',
                       'member_hash_wire', 'unexpected_after'):
            row = copy.deepcopy(created)
            o = row['observations']
            if attack == 'member_not_visible':
                o['group_visibility']['snapshot']['items'][1]['visible'] = False
            elif attack == 'tamper_accepted':
                o['group_visibility_control']['snapshot']['items'][0] = \
                    copy.deepcopy(o['group_visibility']['snapshot']['items'][0])
            elif attack == 'binding_hash':
                o['group_visibility']['bindings'][0]['item_hash'] = 'a' * 64
            elif attack == 'binding_result_id':
                o['group_visibility']['bindings'][0]['result_id'] = 'recall-result:other'
            elif attack == 'member_hash_wire':
                o['confirmation']['execution']['result_confirmation_member_hashes'][0][0] = 'b' * 64
            else:
                o['group_visibility_after'] = copy.deepcopy(o['group_visibility'])
            self.assertEqual(oracle.assess_cell(fixture, row)['status'], 'FAIL', attack)

        # After a real resolution the WHOLE group must go stale on the same durable bindings.
        for attack in ('still_visible', 'half_stale', 'wrong_reason'):
            row = copy.deepcopy(resolved)
            items = row['observations']['group_visibility_after']['snapshot']['items']
            if attack == 'still_visible':
                for item in items:
                    item['visible'], item['reason'] = True, oracle.GROUP_VISIBLE
            elif attack == 'half_stale':
                items[1]['visible'], items[1]['reason'] = True, oracle.GROUP_VISIBLE
            else:
                items[0]['reason'] = oracle.GROUP_MISMATCH
            self.assertEqual(oracle.assess_cell(fixture, row)['status'], 'FAIL', attack)


if __name__ == '__main__':
    unittest.main()
