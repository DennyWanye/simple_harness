"""Original trigger executor: two public recall cases and one construction conflict."""
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

class TriggerExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_three_and_signal_source_counterexamples(self):
        fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
        recipes=[r for r in load('runners','typed_recall_normal_inputs').recipes(fixture) if r['family']=='prospective_trigger']
        self.assertEqual(len(recipes),3)
        with TemporaryDirectory() as directory:
            rows=await load('adapters','typed_recall_normal_cases').run_cases(recipes,Path(directory))
        oracle=load('runners','typed_recall_a2_oracle')
        for row in rows:
            verdict=oracle.assess_normal(fixture,row)
            self.assertEqual(verdict['status'],'BLOCKED' if row['cell_id'].endswith('trigger-missing') else 'PASS',verdict)
            if row['cell_id'].endswith('trigger-missing'):self.assertTrue(verdict['reason'].startswith('CONSTRUCTION_CONFLICT:'))
        complete=next(r for r in rows if r['cell_id'].endswith('signal-complete'))
        tampered=copy.deepcopy(complete)
        event=next(e for e in tampered['observations']['calls'] if e['call']=='synthetic_scheduler_input')
        event['record']['run_id']='foreign'
        verdict=oracle.assess_normal(fixture,tampered)
        self.assertEqual(verdict['status'],'FAIL',verdict)
        self.assertIn('source/run/clock/outbox',verdict['reason'])
        missing=next(r for r in rows if r['cell_id'].endswith('signal-missing'))
        tampered=copy.deepcopy(missing)
        tampered['observations']['positive_control']['calls']=[e for e in tampered['observations']['positive_control']['calls'] if e['call']!='read_prospective_outbox']
        verdict=oracle.assess_normal(fixture,tampered)
        self.assertEqual(verdict['status'],'FAIL',verdict)
        self.assertIn('public outbox read incomplete',verdict['reason'])
        tampered=copy.deepcopy(missing)
        r=tampered['observations']['positive_control']['recall'];v=r['execution'];result=v['result'];decision=v['decision']
        result['items'][0]['selected_item']['source_revision']=9
        decision['selected_items']=[i['selected_item'] for i in result['items']]
        v['decision_hash']=oracle.sdk_domain_hash('simple-harness/recall-decision/v4',decision);result['decision_hash']=v['decision_hash']
        v['result_hash']=oracle.sdk_domain_hash('simple-harness/typed-recall-result/v1',result)
        v['result_item_hashes']=[oracle.sdk_domain_hash('simple-harness/typed-recall-result-item/v1',i) for i in result['items']]
        v['hashes'].update(decision=v['decision_hash'],result=v['result_hash'])
        r['replay']={**copy.deepcopy(v),'replayed':True,'candidate_query_count':0,'candidate_query_started':False}
        oracle.check_execution_wire(v,r['context'],r['plan'])
        verdict=oracle.assess_normal(fixture,tampered)
        self.assertEqual(verdict['status'],'FAIL',verdict)
        self.assertEqual(verdict['reason'],'trigger positive source binding differs')
