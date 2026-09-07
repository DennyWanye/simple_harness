"""Installed public Procedure setup and independent oracle counterexamples; no SQL/model."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def load(folder, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / folder / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProcedurePublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_active_and_eligible_public_oracle(self):
        fixture = json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
        inputs = load('runners', 'typed_recall_normal_inputs')
        consumer = load('adapters', 'typed_recall_normal_cases')
        oracle = load('runners', 'typed_recall_a2_oracle')
        ids = {'eligibility/procedure-active', 'eligibility/procedure-eligible-state',
            'eligibility/epistemic:procedure:explicit_user:source_bound',
            'eligibility/epistemic:procedure:explicit_user:user_confirmed'}
        recipes = [r for r in inputs.recipes(fixture) if r['cell_id'] in ids]
        self.assertEqual({r['cell_id'] for r in recipes}, ids)
        with TemporaryDirectory() as directory:
            rows = await consumer.run_cases(recipes, Path(directory))
        for row in rows:
            with self.subTest(cell=row['cell_id']):
                self.assertNotIn('exception', row['observations'])
                result = oracle.assess_normal(fixture, row)
                self.assertEqual(result['status'], 'PASS', result)
                if row['cell_id'].endswith('eligible-state'):
                    self.assertEqual(row['observations']['recipe']['seed']['state'], 'eligible')
                    self.assertEqual(row['observations']['procedure_binding']['result']['lifecycle_state'],
                        'eligible_for_activation')
                    self.assertEqual(row['observations']['recalls'][0]['execution']['result']['items'], [])
        active = next(r for r in rows if r['cell_id']=='eligibility/procedure-active')
        for field, value in [('target_revision', 99), ('run_id', 'foreign-run'),
                             ('terminal_receipt_id', 'invented-terminal')]:
            tampered = copy.deepcopy(active)
            grant = next(e for e in tampered['observations']['calls']
                if e['call']=='resolve_procedure_observation_authority')
            grant['authority']['intent'][field] = value
            verdict = oracle.assess_normal(fixture, tampered)
            self.assertEqual(verdict['status'], 'FAIL', verdict)
            self.assertIn('exact target/intent/source/snapshot-only', verdict['reason'])

    async def test_wrong_fingerprint_and_reopen(self):
        fixture = json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
        inputs = load('runners', 'typed_recall_normal_inputs')
        recipe = next(r for r in inputs.recipes(fixture) if r['cell_id']=='eligibility/procedure-active')
        helper = load('adapters', 'typed_recall_case_manager')
        procedure = load('adapters', 'typed_recall_procedure_cases')
        with TemporaryDirectory() as directory:
            case = helper.CaseManager(Path(directory)/'memory.sqlite')
            await case.open()
            try:
                target = await case.seed(recipe['seed'])
                proof = await procedure.bind(case, recipe, target)
                params = dict(query=recipe['seed']['payload']['name'], memory_types=('procedure',))
                good = await case.recall(**params, fingerprint=(proof['fingerprint'],), key='good')
                self.assertEqual(len(good['execution']['result']['items']), 1)
                wrong = await case.recall(**params, fingerprint=('0'*64,), key='wrong')
                self.assertEqual(wrong['execution']['decision']['outcome'], 'no_recall')
                self.assertEqual(wrong['execution']['result']['items'], [])
                self.assertEqual(wrong['execution']['candidate_query_count'], 1)
                await case.close()
                await case.open()
                replay = await case.recall(**params, fingerprint=(proof['fingerprint'],), key='good')
                self.assertTrue(replay['execution']['replayed'])
                self.assertEqual(replay['execution']['candidate_query_count'], 0)
                self.assertEqual(replay['execution']['result'], good['execution']['result'])
                fresh = await case.recall(**params, fingerprint=(proof['fingerprint'],), key='fresh')
                item = fresh['execution']['result']['items'][0]['selected_item']
                self.assertEqual(item['source_revision'], proof['result']['committed_revision'])
            finally:
                await case.close()

    def test_conditional_expected_requires_proof_and_literal_preserved(self):
        fixture = json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
        inputs = load('runners', 'typed_recall_normal_inputs')
        oracle = load('runners', 'typed_recall_a2_oracle')
        recipe = next(r for r in inputs.recipes(fixture)
            if r['cell_id']=='eligibility/epistemic:procedure:observed_behavior:repeated_observation')
        self.assertFalse(oracle.normal_expected(fixture, recipe))
        self.assertTrue(oracle.normal_expected(fixture, recipe, applicability=True))
        self.assertFalse(oracle.normal_expected(fixture, recipe, trigger_signal=True))
        eligible = next(r for r in inputs.recipes(fixture) if r['cell_id']=='eligibility/procedure-eligible-state')
        self.assertEqual(eligible['seed']['state'], 'eligible')
        self.assertEqual([s['state'] for s in eligible['lifecycle_path']], ['draft', 'eligible'])
        self.assertFalse(oracle.normal_expected(fixture, eligible, applicability=True))
