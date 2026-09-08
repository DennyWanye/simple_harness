"""Real public authority-event executor plus independent oracle counterexamples; never 401 evidence."""
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


class AuthorityEventPublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_events_pass_and_independent_negatives_fail(self):
        fixture = json.loads((ROOT / 'fixtures/typed-recall-v3.json').read_text())
        adapter = load(ROOT / 'adapters/typed_recall_authority_event_cases.py')
        oracle = load(ROOT / 'runners/typed_recall_a2_oracle.py')
        context_use = load(ROOT / 'runners/typed_recall_context_use_oracle.py')
        events = load(ROOT / 'runners/typed_recall_authority_event_oracle.py')
        base = context_use.inputs(fixture)
        compiled = events.inputs(fixture, base)
        # Inputs carry no outcome/hash gold.
        for spec in compiled['events'].values():
            self.assertFalse({'expected', 'outcome', 'epoch', 'receipt_hash', 'expected_receipt_hash'} & set(spec))
        cells = ['current-use/authority:revoke', 'current-use/authority:contest', 'current-use/authority:result_expiry',
                 'current-use/authority:short_source_expiry', 'current-use/authority:policy_hash_change',
                 'current-use/context:new-continuation']
        with tempfile.TemporaryDirectory() as directory:
            rows = await adapter.run_cases({'cells': cells, 'recipe': base, 'events': compiled['events']}, Path(directory))
        verdicts = {row['cell_id']: (row, oracle.assess_cell(fixture, row)) for row in rows}
        for name in cells[1:4]:
            row, verdict = verdicts[name]
            self.assertEqual(row['status'], 'OBSERVED')
            self.assertEqual(verdict['status'], 'PASS', (name, verdict))
            self.assertGreaterEqual(len(verdict['business_assertions']), 4)
        # M0.6.29 use fence: a revoke restores exactly the bound sources, so the sealed
        # RECALL_AUTHORITY_STALE is no longer producible. The cell is executed and fully
        # witnessed (four business assertions) but reported as a public-contract conflict.
        row, verdict = verdicts['current-use/authority:revoke']
        self.assertEqual(row['status'], 'OBSERVED')
        self.assertEqual(verdict['status'], 'BLOCKED', verdict)
        self.assertTrue(verdict['reason'].startswith('PUBLIC_CONTRACT_CONFLICT:authority_event_cases[revoke]'), verdict)
        self.assertGreaterEqual(len(verdict['business_assertions']), 4)
        for name in cells[4:]:
            row, verdict = verdicts[name]
            self.assertEqual(row['status'], 'BLOCKED')
            self.assertTrue(verdict['reason'].startswith('PUBLIC_CONTRACT_CONFLICT:'), verdict)
        revoke = verdicts['current-use/authority:revoke'][0]
        for attack in ('epoch', 'stale_accepted', 'revoke_rejected', 'revoke_directive', 'mid_leak', 'replay', 'order', 'first_time', 'policy'):
            row = copy.deepcopy(revoke); o = row['observations']
            if attack == 'epoch': o['after']['execution']['result']['authority_epoch'] += 1
            # The admitted receipt must be a distinct receipt at the advanced epoch; replaying the first one is not it.
            elif attack == 'stale_accepted': o['uses']['after_event']['receipt'] = o['uses']['first']['receipt']
            # If the candidate ever rejects here again, the sealed row becomes producible and the cell must stop
            # being reported as a contract conflict - the oracle fails rather than silently keeping it BLOCKED.
            elif attack == 'revoke_rejected':
                o['uses']['after_event'].pop('receipt'); o['uses']['after_event'].pop('receipt_hash', None)
                o['uses']['after_event']['exception'] = {'type': 'MemoryValidationError', 'reason': 'RECALL_AUTHORITY_STALE'}
            elif attack == 'revoke_directive': o['revocation']['decision']['supersedes_directive_id'] = 'other-directive'
            elif attack == 'mid_leak': o['mid']['execution']['result']['items'] = o['initial']['execution']['result']['items']
            elif attack == 'replay': o['historical_replay']['candidate_query_count'] = 1
            elif attack == 'order': o['order'].remove('suppression_commit'); o['order'].append('suppression_commit')
            elif attack == 'first_time': o['uses']['first']['request']['requested_at'] += 1
            else: o['after']['execution']['result']['policy_hash'] = 'b' * 64
            self.assertEqual(oracle.assess_cell(fixture, row)['status'], 'FAIL', attack)
        expiry = verdicts['current-use/authority:result_expiry'][0]
        for attack in ('control_rejected', 'boundary_accepted', 'bound', 'valid_until'):
            row = copy.deepcopy(expiry); o = row['observations']
            if attack == 'control_rejected': o['uses']['control'].pop('receipt'); o['uses']['control']['exception'] = {'type': 'MemoryValidationError', 'reason': 'RECALL_AUTHORITY_STALE'}
            elif attack == 'boundary_accepted': o['uses']['after_event'].pop('exception'); o['uses']['after_event']['receipt'] = o['uses']['control']['receipt']
            elif attack == 'bound': o['initial']['execution']['result']['authority_expires_at'] += 1
            else:
                plan = next(e['plan'] for e in o['calls'] if e['call'] == 'apply_memory_mutation_plan' and e['plan']['plan_id'] == o['sources'][0]['receipt']['plan_id'])
                plan['operations'][0]['valid_time_interval']['valid_until'] = None
            self.assertEqual(oracle.assess_cell(fixture, row)['status'], 'FAIL', attack)
        short = verdicts['current-use/authority:short_source_expiry'][0]
        for attack in ('not_removed', 'fragment_type', 'short_leak'):
            row = copy.deepcopy(short); o = row['observations']
            if attack == 'not_removed': o['projection_rebuild']['result']['removed_chunk_count'] = 0
            elif attack == 'fragment_type':
                fragment = next(f for f in o['uses']['first']['fragments'] if f['fragment']['fragment_type'] == 'short_horizon')
                fragment['fragment']['fragment_type'] = 'recalled_memory'
            else: o['after']['execution']['result']['items'] = o['initial']['execution']['result']['items']
            self.assertEqual(oracle.assess_cell(fixture, row)['status'], 'FAIL', attack)


if __name__ == '__main__':
    unittest.main()
