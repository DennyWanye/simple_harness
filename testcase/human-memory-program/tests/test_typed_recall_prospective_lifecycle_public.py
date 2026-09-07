"""Remaining six original lifecycle cases; actual public calls, independent tamper checks."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]


def load(folder,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/folder/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


class ProspectiveLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_six_original_states_and_independent_proof_tampering(self):
        fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
        states={'candidate','in_progress','completed','rescheduled','cancelled','expired'}
        recipes=[r for r in load('runners','typed_recall_normal_inputs').recipes(fixture)
            if r['family']=='lifecycle' and r['seed']['memory_type']=='prospective' and r['seed']['state'] in states]
        self.assertEqual({r['seed']['state'] for r in recipes},states)
        with TemporaryDirectory() as directory:
            rows=await load('adapters','typed_recall_normal_cases').run_cases(recipes,Path(directory))
        oracle=load('runners','typed_recall_a2_oracle')
        for row in rows:
            self.assertNotIn('exception',row['observations'],row)
            verdict=oracle.assess_normal(fixture,row)
            self.assertEqual(verdict['status'],'PASS',(row['cell_id'],verdict))
        for row in rows:
            forged=copy.deepcopy(row)
            forged['observations']['sources'][-1]['input']['state']='pending'
            verdict=oracle.assess_normal(fixture,forged)
            self.assertEqual(verdict['status'],'FAIL',verdict)
            self.assertIn('original source differs',verdict['reason'])
        progress=next(r for r in rows if r['observations']['recipe']['seed']['state']=='in_progress')
        forged=copy.deepcopy(progress)
        event=next(e for e in forged['observations']['calls'] if e['call']=='apply_memory_mutation_plan' and e['plan']['operations'][0]['kind']=='revise')
        event['plan']['operations'][0]['target']['revision']=1
        self.assertEqual(oracle.assess_normal(fixture,forged)['status'],'FAIL')
        rescheduled=next(r for r in rows if r['observations']['recipe']['seed']['state']=='rescheduled')
        forged=copy.deepcopy(rescheduled)
        event=[e for e in forged['observations']['calls'] if e['call']=='read_prospective_outbox'][-1]
        event['entries']=[]
        verdict=oracle.assess_normal(fixture,forged)
        self.assertEqual(verdict['status'],'FAIL',verdict)
        self.assertIn('outbox command missing',verdict['reason'])
        candidate=next(r for r in rows if r['observations']['recipe']['seed']['state']=='candidate')
        forged=copy.deepcopy(candidate)
        forged['observations']['candidate_control']['source']['input']['state']='candidate'
        self.assertEqual(oracle.assess_normal(fixture,forged)['status'],'FAIL')

        def rehash(recall):
            value=recall['execution'];decision=value['decision'];result=value['result']
            decision['selected_items']=[i['selected_item'] for i in result['items']]
            value['decision_hash']=oracle.sdk_domain_hash('simple-harness/recall-decision/v4',decision)
            result['decision_hash']=value['decision_hash']
            value['result_hash']=oracle.sdk_domain_hash('simple-harness/typed-recall-result/v1',result)
            value['result_item_hashes']=[oracle.sdk_domain_hash('simple-harness/typed-recall-result-item/v1',i) for i in result['items']]
            value['hashes'].update(decision=value['decision_hash'],result=value['result_hash'])
            recall['replay']={**copy.deepcopy(value),'replayed':True,'candidate_query_count':0,'candidate_query_started':False}
            oracle.check_execution_wire(value,recall['context'],recall['plan'])
            oracle.check_execution_wire(recall['replay'],recall['context'],recall['plan'])
        forged=copy.deepcopy(progress)
        # The public receipt-view hash is opaque (not a hash of its projection).
        # Corrupt the returned projected ID, preserve that opaque commitment,
        # and recompute all downstream selected/decision/result/replay hashes.
        fo=forged['observations'];fo['sources'][-1]['receipt']['operations'][0]['memory_id']='foreign-memory'
        fo['recalls'][0]['execution']['result']['items'][0]['selected_item']['source_ref']='foreign-memory'
        rehash(fo['recalls'][0])
        verdict=oracle.assess_normal(fixture,forged)
        self.assertEqual(verdict['status'],'FAIL',verdict)
        self.assertEqual(verdict['reason'],'lifecycle returned revision changed target memory identity')
        forged=copy.deepcopy(candidate)
        positive=forged['observations']['candidate_control']['recall']
        positive['execution']['result']['items'][0]['selected_item']['source_revision']=1
        rehash(positive)
        verdict=oracle.assess_normal(fixture,forged)
        self.assertEqual(verdict['status'],'FAIL',verdict)
        self.assertEqual(verdict['reason'],'candidate positive control pending revision/source binding differs')
