"""Real public executors for the run-11 admissions plus independent counterexamples; never 401 evidence.

Covers: baseline unbounded-validity cell, exact/unsupported/conflicting replay admissions,
Harness parser attacks, conflict rejection mapping and the epistemic legal-state seeds.
"""
import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture():
    return json.loads((ROOT / 'fixtures/typed-recall-v3.json').read_text())


class AdmissionOracleTests(unittest.IsolatedAsyncioTestCase):
    async def test_baseline_replay_and_conflicting_replay_admissions(self):
        fx = fixture(); oracle = load(ROOT / 'runners/typed_recall_a2_oracle.py')
        manager = load(ROOT / 'adapters/typed_recall_public_manager.py')
        inputs = {'claim': next(r['source'] for r in fx['approved_oracle']['semantic_source_vectors'] if r['id'] == 'incumbent'),
                  'validity': {k: v for k, v in fx['eligibility_cases'][0].items() if k in {'now', 'valid_from', 'valid_until'}},
                  'mutations': [{k: r[k] for k in ('original_attack', 'public_path', 'mutation')} for r in fx['approved_oracle']['mutation_mapping']],
                  'unsupported': [{k: r[k] for k in ('id', 'selectors', 'modes')} for r in fx['unsupported_cases']]}
        request = {'layer': 'public', 'cell_ids': ['eligibility/valid-until-null-unbounded'], 'inputs': inputs}
        with tempfile.TemporaryDirectory() as directory:
            rows = await manager.baseline_cases(request, Path(directory))
        by_id = {r['cell_id']: r for r in rows}
        baseline = by_id['unsupported-replay/exact-replay']['observations']
        verdicts = {name: oracle.assess_cell(fx, row, baseline) for name, row in by_id.items()}
        for name, verdict in verdicts.items():
            self.assertEqual(verdict['status'], 'PASS', (name, verdict))
        unbounded = by_id['eligibility/valid-until-null-unbounded']
        for attack in ('replay', 'valid_until', 'apply_receipt', 'clock', 'filtered'):
            row = copy.deepcopy(unbounded); o = row['observations']
            if attack == 'replay': o['replay']['candidate_query_count'] = 1
            elif attack == 'valid_until': o['mutation_plan']['operations'][0]['valid_time_interval']['valid_until'] = 1.0
            elif attack == 'apply_receipt': o['apply_result']['receipt_ref']['receipt_id'] = 'other-receipt'
            elif attack == 'clock': o['recall_now'] += 1
            else: o['execution']['decision']['filtered_candidate_count'] = 2
            self.assertEqual(oracle.assess_cell(fx, row, baseline)['status'], 'FAIL', attack)
        conflicting = by_id['unsupported-replay/conflicting-replay']
        row = copy.deepcopy(conflicting); row['observations']['rejection_receipt'] = None
        verdict = oracle.assess_cell(fx, row, baseline)
        self.assertEqual(verdict['status'], 'BLOCKED'); self.assertIn('CANDIDATE_READ_WITNESS', verdict['reason'])
        row = copy.deepcopy(conflicting); row['observations']['context']['query'] = 'other query'
        self.assertEqual(oracle.assess_cell(fx, row, baseline)['status'], 'FAIL')
        event_only = by_id['unsupported-replay/event-only']
        for attack in ('replay', 'carrier'):
            row = copy.deepcopy(event_only); o = row['observations']
            if attack == 'replay': o['replay']['replayed'] = False
            else: o['input_carrier']['original_selectors'] = ['ENVIRONMENT']
            self.assertEqual(oracle.assess_cell(fx, row, baseline)['status'], 'FAIL', attack)
        exact = by_id['unsupported-replay/exact-replay']
        row = copy.deepcopy(exact); row['observations']['before_control'] = copy.deepcopy(row['observations']['after_control'])
        self.assertEqual(oracle.assess_cell(fx, row, baseline)['status'], 'FAIL')

    async def test_parser_attacks_pass_and_page_attacks_stay_blocked(self):
        fx = fixture(); oracle = load(ROOT / 'runners/typed_recall_a2_oracle.py')
        adapter = load(ROOT / 'adapters/typed_recall_return_cases.py')
        rows_in = [fx['source_binding_cases'][2], *fx['protocol_negative_cases'], *fx['result_page_cases']]
        keys = {'id', 'request', 'wire_protocol_version', 'source_kind', 'source_ref', 'source_revision', 'chunk_ref', 'result_hash', 'coordinate', 'use_at', 'bounds'}
        claim = next(r['source'] for r in fx['approved_oracle']['semantic_source_vectors'] if r['id'] == 'incumbent')
        inputs = {'seed': {'memory_type': 'semantic', 'payload': {k: claim[k] for k in ('subject_entity', 'predicate', 'object_value', 'qualifiers')}},
                  'expires_at': next(r['use_at'] for r in fx['result_page_cases'] if 'use_at' in r),
                  'cases': [{k: v for k, v in r.items() if k in keys} for r in rows_in]}
        with tempfile.TemporaryDirectory() as directory:
            rows = await adapter.run_cases(inputs, Path(directory))
        verdicts = {r['cell_id']: (r, oracle.assess_cell(fx, r)) for r in rows}
        for name in ('protocol/strict-v3-rejected', 'protocol/invalid-source-discriminant', 'protocol/cognitive-missing-revision', 'protocol/short-fake-revision'):
            self.assertEqual(verdicts[name][1]['status'], 'PASS', (name, verdicts[name][1]))
        # The paging zero-candidate-read obligation is now witnessed by invariance: the same page
        # calls are replayed after the bound memory is really suppressed and the candidate layer's
        # answer drops from 1 to 0, and not one byte of any page moves.
        page_cells = ('protocol/naked-source-ref', 'protocol/page-wrong-result-hash', 'protocol/page-wrong-coordinate',
                      'protocol/page-expired-result', 'protocol/page-correct-binding')
        for name in page_cells:
            self.assertEqual(verdicts[name][1]['status'], 'PASS', (name, verdicts[name][1]))
        page = verdicts['protocol/page-correct-binding']
        self.assertEqual(page[0]['observations']['under_bound']['exception'],
                         {'type': 'MemoryLimitError', 'reason': 'typed_recall_page_budget_too_small'})
        witness = page[0]['observations']['zero_read_witness']
        self.assertEqual(len(witness['control_before']['execution']['result']['items']), 1)
        self.assertEqual(len(witness['control_after']['execution']['result']['items']), 0)
        for name in page_cells:
            for attack in ('no_witness', 'page_moved', 'control_not_suppressed', 'control_never_read', 'wrong_target'):
                row = copy.deepcopy(verdicts[name][0]); o = row['observations']
                if attack == 'no_witness':
                    o.pop('zero_read_witness')
                elif attack == 'page_moved':
                    replay = o['zero_read_witness']['replays'][name.split('/', 1)[1]]
                    if 'returned' in replay: replay['returned']['byte_count'] += 1
                    else: replay['exception']['reason'] = 'other'
                elif attack == 'control_not_suppressed':
                    after = o['zero_read_witness']['control_after']['execution']
                    after['result']['items'] = copy.deepcopy(
                        o['zero_read_witness']['control_before']['execution']['result']['items'])
                elif attack == 'control_never_read':
                    o['zero_read_witness']['control_after']['execution']['candidate_query_started'] = False
                else:
                    o['zero_read_witness']['suppression']['memory_id'] = 'other-memory'
                self.assertEqual(oracle.assess_cell(fx, row)['status'], 'FAIL', (name, attack))
        strict = verdicts['protocol/strict-v3-rejected'][0]
        for attack in ('memory_called', 'extra_call', 'parser_input', 'reason', 'state'):
            row = copy.deepcopy(strict); o = row['observations']
            if attack == 'memory_called': o['memory_called'] = True
            elif attack == 'extra_call': o['calls'].append('execute_typed_recall')
            elif attack == 'parser_input': o['parser_input']['decision_id'] = 'forged'
            elif attack == 'reason': o['exception']['reason'] = 'other'
            else:
                root = next(r for r in o['after']['manifest']['table_roots'] if r['table_name'] == 'cognitive_memory_heads')
                root['root_hash'] = 'f' * 64; o['after']['payload_hash'] = oracle.hash_json(o['after']['manifest'])
            self.assertEqual(oracle.assess_cell(fx, row)['status'], 'FAIL', attack)

    async def test_conflict_rejection_mapping_and_state_witness(self):
        fx = fixture(); oracle = load(ROOT / 'runners/typed_recall_a2_oracle.py')
        adapter = load(ROOT / 'adapters/typed_recall_conflict_cases.py')
        conflict = fx['conflict_write_oracle']
        inputs = {'payloads': {k: {**v, 'qualifiers': []} for k, v in conflict['canonical_payloads'].items()},
                  'cases': [{'id': r['id'], 'mutation': r.get('mutation', {})} for r in conflict['reject_cases']]}
        with tempfile.TemporaryDirectory() as directory:
            rows = await adapter.run_cases(inputs, Path(directory))
        verdicts = {r['cell_id']: (r, oracle.assess_cell(fx, r)) for r in rows}
        expected = {'contest-no-evidence': 'PASS', 'contest-stale-target': 'PASS', 'contest-cross-principal': 'PASS', 'contest-cross-memory': 'PASS',
                    'contest-same-content': 'PUBLIC_WITNESS_UNAVAILABLE:', 'contest-evidence-not-distinct': 'PUBLIC_WITNESS_UNAVAILABLE:',
                    'contest-active-group-exists': 'PUBLIC_WITNESS_UNAVAILABLE:',
                    'contest-nested': 'DUPLICATE_OF_POSITIVE_INVARIANT_WITNESS:',
                    'contest-one-member': 'DUPLICATE_OF_POSITIVE_INVARIANT_WITNESS:',
                    'contest-three-members': 'DUPLICATE_OF_POSITIVE_INVARIANT_WITNESS:'}
        for name, want in expected.items():
            row, verdict = verdicts['conflict-state/' + name]
            if want == 'PASS':
                self.assertEqual(verdict['status'], 'PASS', (name, verdict))
            else:
                self.assertEqual(verdict['status'], 'BLOCKED', (name, verdict)); self.assertTrue(verdict['reason'].startswith(want), verdict)
            self.assertIn('complete receipt/evidence/action-grant chain for revisions 1..7', verdict['business_assertions'])
        stale = verdicts['conflict-state/contest-stale-target'][0]
        for attack in ('reason', 'target', 'state', 'chain'):
            row = copy.deepcopy(stale); o = row['observations']
            if attack == 'reason': o['rejection']['reason'] = 'mutation_target_not_found'
            elif attack == 'target':
                event = [e for e in o['calls'] if e['call'] == 'apply_memory_mutation_plan' and e['plan']['operations'][0]['kind'] == 'contest'][-1]
                event['plan']['operations'][0]['target']['revision'] = 7
            elif attack == 'state':
                root = next(r for r in o['after']['manifest']['table_roots'] if r['table_name'] == 'cognitive_memory_revisions')
                root['row_count'] += 1; o['after']['manifest']['total_row_count'] += 1; o['after']['payload_hash'] = oracle.hash_json(o['after']['manifest'])
            else:
                grant = next(e for e in o['calls'] if e['call'] == 'resolve_memory_action_authority'); grant['grant']['nonce'] = 'forged'
            self.assertEqual(oracle.assess_cell(fx, row)['status'], 'FAIL', attack)
        no_evidence = verdicts['conflict-state/contest-no-evidence'][0]
        row = copy.deepcopy(no_evidence); row['observations']['calls'].append({'call': 'apply_memory_mutation_plan', 'plan': {'plan_id': 'contest-plan', 'operations': [{'kind': 'contest'}]}})
        self.assertEqual(oracle.assess_cell(fx, row)['status'], 'FAIL')

    async def test_epistemic_legal_state_seeds_reach_the_recall_gate(self):
        fx = fixture(); oracle = load(ROOT / 'runners/typed_recall_a2_oracle.py')
        compiler = load(ROOT / 'runners/typed_recall_normal_inputs.py'); consumer = load(ROOT / 'adapters/typed_recall_normal_cases.py')
        ids = {'eligibility/epistemic:semantic:llm_inference:unverified', 'eligibility/epistemic:episode:unknown:unverified',
               'eligibility/epistemic:procedure:unknown:unverified', 'eligibility/epistemic:procedure:observed_behavior:source_verified',
               'eligibility/epistemic:procedure:observed_behavior:repeated_observation', 'eligibility/epistemic:semantic:unknown:user_confirmed',
               'eligibility/procedure-superseded', 'eligibility/procedure-reinforced'}
        recipes = [r for r in compiler.recipes(fx) if r['cell_id'] in ids]
        self.assertEqual({r['cell_id'] for r in recipes}, ids)
        self.assertEqual(next(r for r in recipes if r['cell_id'].endswith('semantic:llm_inference:unverified'))['seed']['state'], 'candidate')
        self.assertEqual(next(r for r in recipes if r['cell_id'].endswith('procedure:unknown:unverified'))['seed']['state'], 'draft')
        with tempfile.TemporaryDirectory() as directory:
            rows = await consumer.run_cases(recipes, Path(directory))
        for row in rows:
            verdict = oracle.assess_normal(fx, row); name = row['cell_id']
            if name.endswith('observed_behavior:repeated_observation'):
                # The sealed ELIGIBLE_WITH_APPLICABILITY row is now reached through the real
                # public promotion: three attributable low-risk successes, each with its own
                # registered tool terminal receipt, carry the draft head to active.
                self.assertEqual(verdict['status'], 'PASS', (name, verdict))
                promotion = row['observations']['procedure_binding']['promotion']
                self.assertEqual([step['result']['lifecycle_state'] for step in promotion],
                                 ['draft', 'eligible_for_activation', 'active'])
                self.assertEqual([step['result']['independent_successes'] for step in promotion], [1, 2, 3])
                recall = row['observations']['recalls'][0]['execution']
                self.assertEqual(len(recall['result']['items']), 1)
                self.assertEqual(recall['result']['items'][0]['selected_item']['source_revision'],
                                 promotion[-1]['result']['committed_revision'])
                for mutate in (
                    lambda o: o['procedure_binding']['promotion'].pop(),
                    lambda o: o['procedure_binding']['promotion'][2]['result'].__setitem__('lifecycle_state', 'draft'),
                    lambda o: o['procedure_binding']['promotion'][1]['result'].__setitem__('independent_successes', 3),
                    lambda o: o['procedure_binding']['promotion'][0].__setitem__('task_scope_id',
                        o['procedure_binding']['promotion'][1]['task_scope_id']),
                    lambda o: next(e for e in o['calls']
                        if e['call'] == 'register_procedure_observation_conversation'
                        and e['ordinal'] == 1 and e['item_ordinal'] == 2)['registration']['metadata']
                        .__setitem__('tool_causal_link', None),
                ):
                    broken = copy.deepcopy(row); mutate(broken['observations'])
                    self.assertEqual(oracle.assess_normal(fx, broken)['status'], 'FAIL', mutate)
            elif name.endswith('semantic:unknown:user_confirmed'):
                # The public contract refuses the pair at ingress; the cell is proved by the
                # exact refusal plus its paired permitted control (forbidden oracle).
                self.assertEqual(verdict['status'], 'PASS', (name, verdict))
                witness = row['observations']['forbidden']
                self.assertEqual(row['observations']['exception'],
                                 {'type': 'MemoryValidationError', 'reason': 'mutation_unknown_must_be_unverified'})
                self.assertEqual(len(witness['zero_recall']['execution']['result']['items']), 0)
                self.assertEqual(len(witness['control_recall']['execution']['result']['items']), 1)
                broken = copy.deepcopy(row)
                broken['observations']['exception']['reason'] = 'something_else'
                self.assertEqual(oracle.assess_normal(fx, broken)['status'], 'FAIL')
            else:
                self.assertEqual(verdict['status'], 'PASS', (name, verdict))
                recall = row['observations']['recalls'][0]['execution']
                expected_items = 1 if name.endswith('procedure-reinforced') else 0
                self.assertEqual(len(recall['result']['items']), expected_items, name)
                self.assertEqual(recall['candidate_query_count'], 1)
        seeded = next(r for r in rows if r['cell_id'].endswith('semantic:llm_inference:unverified'))
        changed = copy.deepcopy(seeded)
        changed['observations']['recalls'][0]['execution']['result']['items'] = [{'forged': True}]
        self.assertEqual(oracle.assess_normal(fx, changed)['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
