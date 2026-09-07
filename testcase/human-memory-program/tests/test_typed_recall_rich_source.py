"""Bounded installed public rich Episode source and field disclosure oracles."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]

def load(folder,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/folder/(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class RichSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_rich_episode_and_independent_leak_fields(self):
        fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
        row=next(r for r in fixture['minimal_projection_oracle'] if r['memory_type']=='episode')
        adapter=load('adapters','typed_recall_rich_source_cases')
        oracle=load('runners','typed_recall_rich_source_oracle')
        with TemporaryDirectory() as directory:
            observed=await adapter.run(row,Path(directory))
        self.assertEqual(oracle.check(fixture,observed),{'rich_setup':'PASS','legacy_status':'BLOCKED'})
        # Test the independent field-disclosure predicate before wire hashes:
        # these are oracle counterexamples, not claims the SDK emitted a leak.
        for field in ['source_ref','evidence_ids','classification','conflict_status','cross_scope','extra_typed_field']:
            bad=copy.deepcopy(observed)
            bad['recalls'][0]['execution']['result']['items'][0]['public_payload'][field]=row['source_record'][field]
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'rich source field leaked'):
                oracle.check(fixture,bad)
        bad=copy.deepcopy(observed);bad['label_mapping']['actual_memory_id']='foreign-memory'
        with self.assertRaisesRegex(ValueError,'label to actual source binding'):
            oracle.check(fixture,bad)
        bad=copy.deepcopy(observed);bad['legacy_status']='PASS'
        with self.assertRaisesRegex(ValueError,'legacy literal incorrectly promoted'):
            oracle.check(fixture,bad)
