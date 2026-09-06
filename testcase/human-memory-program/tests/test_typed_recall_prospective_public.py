"""Synthetic scheduler over installed public Memory; no Host events/models/SQL."""
import copy
import dataclasses as dc
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parents[1]


def load(folder,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/folder/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def recipes():
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    return fixture,load('runners','typed_recall_normal_inputs').recipes(fixture)


class ProspectivePublicTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_pending_triggered_and_conditional_epistemic(self):
        fixture,all_recipes=recipes()
        ids={'eligibility/prospective-pending','eligibility/prospective-triggered',
            'eligibility/epistemic:prospective:explicit_user:source_bound',
            'eligibility/epistemic:prospective:explicit_user:user_confirmed',
            'eligibility/epistemic:prospective:explicit_user:unverified'}
        selected=[r for r in all_recipes if r['cell_id'] in ids]
        self.assertEqual({r['cell_id'] for r in selected},ids)
        with TemporaryDirectory() as directory:
            rows=await load('adapters','typed_recall_normal_cases').run_cases(selected,Path(directory))
        oracle=load('runners','typed_recall_a2_oracle')
        for row in rows:
            verdict=oracle.assess_normal(fixture,row)
            self.assertEqual(verdict['status'],'PASS',(row['cell_id'],verdict))
        original=next(r for r in rows if r['cell_id']=='eligibility/prospective-triggered')
        for field,value in [('run_id','foreign-run'),('observed_at',0),('trigger',{})]:
            forged=copy.deepcopy(original)
            entry=next(e for e in forged['observations']['calls'] if e['call']=='synthetic_scheduler_input')
            entry['record'][field]=value
            verdict=oracle.assess_normal(fixture,forged)
            self.assertEqual(verdict['status'],'FAIL',verdict)
            self.assertIn('synthetic source/run/clock/outbox',verdict['reason'])

    async def test_missing_registration_future_expired_and_reopen(self):
        _,all_recipes=recipes();recipe=next(r for r in all_recipes if r['cell_id']=='eligibility/prospective-pending')
        helper=load('adapters','typed_recall_case_manager');adapter=load('adapters','typed_recall_prospective_cases')
        with TemporaryDirectory() as directory:
            case=await helper.CaseManager(Path(directory)/'memory.sqlite').open()
            try:
                target=await case.seed(recipe['seed']);trigger=case.payload(recipe['seed']).trigger
                args=dict(query=recipe['seed']['payload']['action'],memory_types=('prospective',))
                missing=await case.recall(**args,key='missing')
                self.assertEqual(missing['execution']['decision']['outcome'],'no_recall')
                self.assertEqual(missing['execution']['candidate_query_count'],1)
                with self.assertRaisesRegex(Exception,'prospective_scheduler_registration_not_live'):
                    await adapter.signal(case,target,trigger,kind='event_occurred',state='pending',
                        next_state='triggered',identity='without-registration')
                await adapter.register(case,target,trigger)
                with self.assertRaisesRegex(Exception,'prospective_signal_observed_at_future'):
                    await adapter.signal(case,target,trigger,kind='event_occurred',state='pending',
                        next_state='triggered',observed_at=case.now+1,identity='future')
                with self.assertRaisesRegex(Exception,'prospective_signal_authority'):
                    await adapter.signal(case,target,trigger,kind='event_occurred',state='pending',
                        next_state='triggered',expires_at=case.now,identity='expired-authority')
                result=await adapter.signal(case,target,trigger,kind='event_occurred',state='pending',
                    next_state='triggered',identity='actual-synthetic-event')
                self.assertEqual(result.outcome.value,'applied')
                good=await case.recall(**args,key='triggered')
                self.assertEqual(len(good['execution']['result']['items']),1)
                await case.close();await case.open()
                replay=await case.recall(**args,key='triggered')
                self.assertTrue(replay['execution']['replayed'])
                self.assertEqual(replay['execution']['candidate_query_count'],0)
                self.assertEqual(replay['execution']['result'],good['execution']['result'])
                fresh=await case.recall(**args,key='fresh')
                self.assertEqual(fresh['execution']['result']['items'][0]['selected_item']['source_revision'],result.committed_revision)
            finally:await case.close()

    async def test_explicit_synthetic_time_input_not_original_event(self):
        _,all_recipes=recipes();original=next(r for r in all_recipes if r['cell_id']=='eligibility/prospective-pending')
        helper=load('adapters','typed_recall_case_manager');adapter=load('adapters','typed_recall_prospective_cases')
        with TemporaryDirectory() as directory:
            case=await helper.CaseManager(Path(directory)/'time.sqlite').open()
            try:
                seed=copy.deepcopy(original['seed'])
                seed['payload']['trigger']={'kind':'time','trigger_at':case.now+10,'timezone':'UTC'}
                target=await case.seed(seed);trigger=case.payload(seed).trigger
                await adapter.register(case,target,trigger)
                with self.assertRaisesRegex(ValueError,'precedes trigger_at'):
                    await adapter.signal(case,target,trigger,kind='time_due',state='pending',next_state='triggered')
                case.now+=10
                result=await adapter.signal(case,target,trigger,kind='time_due',state='pending',next_state='triggered',identity='on-time')
                self.assertEqual(result.outcome.value,'applied')
                self.assertEqual(result.lifecycle_state.value,'triggered')
            finally:await case.close()
