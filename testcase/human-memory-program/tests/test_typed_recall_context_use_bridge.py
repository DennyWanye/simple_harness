"""Real public executor plus independent oracle tamper controls; never401 quality evidence."""
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


class PublicContextUseTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_item_scenarios_and_independent_negative_oracle(self):
        fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
        adapter=load(ROOT/'adapters/typed_recall_context_use_cases.py')
        oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
        compiler=load(ROOT/'runners/typed_recall_context_use_oracle.py')
        with tempfile.TemporaryDirectory() as directory:
            rows=await adapter.run_cases({'cells':sorted(adapter.CELLS),'recipe':compiler.inputs(fixture)},Path(directory))
        self.assertEqual({r['cell_id'] for r in rows},adapter.CELLS)
        for row in rows:
            verdict=oracle.assess_cell(fixture,row)
            if row['cell_id'].endswith(('receipt-first','duplicate-same-provider-attempt')):
                self.assertEqual(verdict['status'],'BLOCKED',verdict)
                self.assertEqual(verdict['reason'],'CURRENT_USE_HARNESS_RESERVATION_EXACT_ONCE_WITNESS_UNVERIFIED')
            else:
                self.assertEqual(verdict['status'],'PASS',verdict)
            self.assertNotIn('TWO_ITEM_EPOCH_CONTINUATION_ORACLE_PENDING',verdict['reason'])
            self.assertGreaterEqual(len(verdict['business_assertions']),3)
        original=next(r for r in rows if r['cell_id']=='current-use/context:receipt-first')
        for attack in ('missing_item','missing_page','item_hash','epoch','policy','receipt_attempt',
                       'new_stale_accepted','forget_scope','unaffected_hidden','replay','order','validation_input',
                       'validation_time','validation_reason','page_request','fragment_bytes'):
            row=copy.deepcopy(original);o=row['observations']
            if attack=='missing_item':o['initial']['execution']['result']['items'].pop()
            elif attack=='missing_page':o['uses']['first']['pages'].pop()
            elif attack=='item_hash':o['uses']['first']['request']['item_bindings'][1]['item_hash']='a'*64
            elif attack=='epoch':o['after']['execution']['result']['authority_epoch']+=1
            elif attack=='policy':o['after']['execution']['result']['policy_hash']='b'*64
            elif attack=='receipt_attempt':o['uses']['first']['receipt']['provider_attempt_id']='unbound'
            elif attack=='new_stale_accepted':o['uses']['after_suppression'].pop('exception')
            elif attack=='forget_scope':o['suppression']['request']['purpose']='recall'
            elif attack=='unaffected_hidden':o['after']['execution']['result']['items']=[]
            elif attack=='replay':o['historical_replay']['candidate_query_count']=1
            elif attack=='order':o['order'].remove('first');o['order'].append('first')
            elif attack=='validation_input':o['receipt_validation']['different_attempt']['request']['turn_id']='another-turn'
            elif attack=='validation_time':o['receipt_validation']['different_attempt']['request']['requested_at']+=1
            elif attack=='validation_reason':o['receipt_validation']['different_attempt']['exception']['reason']='unrelated rejection'
            elif attack=='page_request':o['uses']['first']['pages'][0]['request']['max_items']=2
            else:o['uses']['first']['fragments'][0]['fragment']['byte_estimate']+=1
            self.assertEqual(oracle.assess_cell(fixture,row)['status'],'FAIL',attack)
        continuation=next(r for r in rows if r['cell_id']=='current-use/context:new-continuation')
        probes=continuation['observations']['continuation']
        self.assertEqual(probes['same_attempt']['exception'],
                         {'type':'MemoryIdempotencyConflict','reason':'RECALL_CONTEXT_USE_IDEMPOTENCY_CONFLICT'})
        self.assertEqual(probes['fresh_attempt']['exception'],
                         {'type':'MemoryValidationError','reason':'typed_recall_context_use_invocation_binding_invalid'})
        self.assertIn('receipt',probes['control'])
        for attack in ('same_attempt_accepted','fresh_attempt_accepted','control_refused','control_turn_changed',
                       'probe_turn_unchanged','shared_attempt','sealed_validation_reason','missing_probe'):
            row=copy.deepcopy(continuation);o=row['observations']
            if attack=='same_attempt_accepted':
                o['continuation']['same_attempt'].pop('exception')
                o['continuation']['same_attempt']['receipt']=copy.deepcopy(o['uses']['first']['receipt'])
                o['continuation']['same_attempt']['receipt_hash']=o['uses']['first']['receipt_hash']
            elif attack=='fresh_attempt_accepted':
                o['continuation']['fresh_attempt']['exception']={'type':'MemoryValidationError','reason':'RECALL_AUTHORITY_STALE'}
            elif attack=='control_refused':
                o['continuation']['control'].pop('receipt')
            elif attack=='control_turn_changed':
                o['continuation']['control']['request']['turn_id']='continuation-2'
            elif attack=='probe_turn_unchanged':
                o['continuation']['fresh_attempt']['request']['turn_id']=o['initial']['context']['turn_id']
            elif attack=='shared_attempt':
                o['continuation']['control']['request']['provider_attempt_id']=\
                    o['continuation']['fresh_attempt']['request']['provider_attempt_id']
            elif attack=='sealed_validation_reason':
                o['receipt_validation']['different_continuation']['exception']['reason']='unrelated rejection'
            else:
                o['continuation'].pop('fresh_attempt')
            self.assertEqual(oracle.assess_cell(fixture,row)['status'],'FAIL',attack)

        wrong=next(r for r in rows if r['cell_id']=='current-use/context:wrong-snapshot')
        for attack in ('dto_reason','valid_attack_input','valid_attack_accepted','stale_instead_of_snapshot','wrong_conflict_reason'):
            row=copy.deepcopy(wrong);o=row['observations']
            if attack=='dto_reason':o['wrong_snapshot']['exception']['reason']='unrelated validation error'
            elif attack=='valid_attack_input':o['changed_snapshot']['request']['provider_attempt_id']='other'
            elif attack=='valid_attack_accepted':o['changed_snapshot']['receipt']=o['uses']['first']['receipt']
            elif attack=='stale_instead_of_snapshot':o['changed_snapshot']['exception']={'type':'MemoryValidationError','reason':'RECALL_AUTHORITY_STALE'}
            else:o['changed_snapshot']['exception']['reason']='IDEMPOTENCY_CONFLICT'
            self.assertEqual(oracle.assess_cell(fixture,row)['status'],'FAIL',attack)


if __name__=='__main__':unittest.main()
