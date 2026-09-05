"""Bridge-only counterexamples on the installed clock-capable candidate; not401 PASS."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
def load(path):
    spec=importlib.util.spec_from_file_location(path.stem,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

class PublicLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_loop_and_counterexamples(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path=Path(directory)
            fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
            adapter=load(ROOT/'adapters/typed_recall_context_use_cases.py');oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
            inputs=dict(cells=sorted(adapter.CELLS),payloads={k:{**v,'qualifiers':[]} for k,v in fixture['conflict_write_oracle']['canonical_payloads'].items()})
            rows=await adapter.run_cases(inputs,tmp_path)
            assert len(rows)==6
            for row in rows:
                verdict=oracle.assess_cell(fixture,row)
                assert verdict['status']=='BLOCKED',verdict
                assert len(verdict['business_assertions'])==4,verdict
            original=next(row for row in rows if row['cell_id']=='current-use/context:receipt-first')
            for attack in ('receipt','fresh_use_accepted','forget_scope','old_value','fragment','wrong_target'):
                row=copy.deepcopy(original);o=row['observations']
                if attack=='receipt':o['uses']['corrected_use']['receipt']['provider_attempt_id']='unbound'
                elif attack=='fresh_use_accepted':o['uses']['new_after_forget'].pop('exception')
                elif attack=='forget_scope':o['suppression']['request']['purpose']='recall'
                elif attack=='old_value':o['corrected']['execution']['result']['items'][0]['public_payload']['object_value']='3.11'
                elif attack=='fragment':o['uses']['corrected_use']['fragment']['source_revision']=1
                else:
                    e=next(e for e in o['calls'] if e['call']=='apply_memory_mutation_plan' and e['plan']['plan_id']=='loop-correct-plan')
                    e['plan']['operations'][0]['target']['revision']=2
                assert oracle.assess_cell(fixture,row)['status']=='FAIL',attack
            swapped=copy.deepcopy(original)
            uses=swapped['observations']['uses']
            uses['corrected_use']=copy.deepcopy(uses['initial_use'])
            uses['corrected_replay_after_reopen']=copy.deepcopy(uses['initial_use'])
            assert oracle.assess_cell(fixture,swapped)['status']=='FAIL'
            failed=copy.deepcopy(original)
            failed['observations'].update(phase='corrected_recall',exception={'type':'RuntimeError','reason':'unexpected product fault'})
            assert oracle.assess_cell(fixture,failed)['status']=='FAIL'


if __name__=="__main__":unittest.main()
