"""Rich public Procedure/Prospective proofs; legacy projection remains blocked."""
import copy
import importlib.util
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]

def load(folder,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/folder/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class RichLifecycleSources(unittest.IsolatedAsyncioTestCase):
    async def check_kind(self,kind):
        fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
        row=next(r for r in fixture['minimal_projection_oracle'] if r['memory_type']==kind)
        with TemporaryDirectory() as directory:
            observation=await load('adapters','typed_recall_rich_source_cases').run(row,Path(directory))
        output=os.environ.get('RICH_SOURCE_OBSERVATIONS')
        if output:
            dest=Path(output);dest.mkdir(parents=True,exist_ok=True)
            (dest/(kind+'.json')).write_text(json.dumps(observation,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
        oracle=load('runners','typed_recall_rich_source_oracle')
        self.assertEqual(oracle.check(fixture,observation),{'rich_setup':'PASS','legacy_status':'BLOCKED'})
        for field in ['source_ref','evidence_ids','classification','conflict_status','cross_scope','extra_typed_field']:
            bad=copy.deepcopy(observation)
            bad['recalls'][0]['execution']['result']['items'][0]['public_payload'][field]=row['source_record'][field]
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'rich source field leaked'):
                oracle.check(fixture,bad)
        bad=copy.deepcopy(observation);bad['fresh']['context']['evidence_refs']=[]
        with self.assertRaisesRegex(ValueError,'rich complete recall source refs differ'):
            oracle.check(fixture,bad)
        bad=copy.deepcopy(observation);bad['label_mapping']['actual_memory_id']='foreign-memory'
        with self.assertRaisesRegex(ValueError,'label to actual source binding'):
            oracle.check(fixture,bad)

    async def test_procedure(self):
        await self.check_kind('procedure')

    async def test_prospective(self):
        await self.check_kind('prospective')
