"""Original applicability axes via installed public manager, no SQL or model."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def load(folder, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / folder / (name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApplicabilityExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_three_and_wrong_current_context(self):
        fixture = json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
        inputs = load('runners','typed_recall_normal_inputs')
        consumer = load('adapters','typed_recall_normal_cases')
        oracle = load('runners','typed_recall_a2_oracle')
        recipes = [r for r in inputs.recipes(fixture) if r['family']=='procedure_applicability']
        self.assertEqual(len(recipes),3)
        with TemporaryDirectory() as directory:
            rows = await consumer.run_cases(recipes,Path(directory))
        for row in rows:
            with self.subTest(cell=row['cell_id']):
                self.assertNotIn('exception',row['observations'])
                verdict = oracle.assess_normal(fixture,row)
                self.assertEqual(verdict['status'],'PASS',verdict)
                recall = row['observations']['recalls'][0]
                self.assertEqual(recall['execution']['candidate_query_count'],1)
                self.assertEqual(len(recall['execution']['result']['items']),int(row['cell_id'].endswith('-match')))
                self.assertEqual(recall['replay']['candidate_query_count'],0)
                # An authentic recorded result must still not pass with a different
                # current-context label, even before any outer wire hash check.
                bad = copy.deepcopy(row)
                context = bad['observations']['recalls'][0]['context']
                context['procedure_applicability_fingerprints'] = ['f'*64]
                verdict = oracle.assess_normal(fixture,bad)
                self.assertEqual(verdict['status'],'FAIL',verdict)
                self.assertIn('procedure current recall fingerprint differs',verdict['reason'])
        match = next(r for r in rows if r['cell_id'].endswith('-match'))
        bad = copy.deepcopy(match)
        grant = next(e for e in bad['observations']['calls'] if e['call']=='resolve_procedure_observation_authority')
        grant['authority']['intent']['target_revision'] = 99
        verdict = oracle.assess_normal(fixture,bad)
        self.assertEqual(verdict['status'],'FAIL',verdict)
        self.assertIn('exact target/intent/source/snapshot-only',verdict['reason'])
