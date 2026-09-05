"""Bridge regressions; never counted as the 401 product acceptance execution."""
import copy
import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]

def load(path):
    spec=importlib.util.spec_from_file_location(path.stem,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_recipes_have_only_inputs_and_original_budget_limits():
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    rows=load(ROOT/'runners/typed_recall_normal_inputs.py').recipes(fixture)
    assert len(rows)==292 and len({r['cell_id'] for r in rows})==292
    def keys(value):
        if isinstance(value,dict):
            for key,value in value.items():
                assert key not in {'expected','outcome','source_content_hash','payload_hash','score','canonical_sha256'}
                keys(value)
        elif isinstance(value,list):
            for entry in value:keys(entry)
    keys(rows)
    budget=next(r for r in rows if r['cell_id']=='selection-budget/budget:semantic-cjk')
    assert budget['limits']==[{'max_bytes':166,'max_tokens':150},{'max_tokens':149}]


@pytest.mark.asyncio
async def test_actual_bridge_seed_has_required_evidence_refs_and_oracle_rejects_tamper(tmp_path):
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    recipe=next(r for r in load(ROOT/'runners/typed_recall_normal_inputs.py').recipes(fixture)
                if r['cell_id']=='eligibility/valid-from-equals-now')
    result=(await load(ROOT/'adapters/typed_recall_normal_cases.py').run_cases([recipe],tmp_path))[0]
    assert 'exception' not in result['observations'],result['observations'].get('exception')
    oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
    judged=oracle.assess_normal(fixture,result)
    assert judged['status']=='PASS',judged
    changed=copy.deepcopy(result)
    changed['observations']['recalls'][0]['execution']['result']['items'][0]['public_payload']['object_value']='forged'
    assert oracle.assess_normal(fixture,changed)['status']=='FAIL'


@pytest.mark.asyncio
async def test_review_boundary_receipt_evidence_and_empty_rejection_counterexamples(tmp_path):
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    recipes=load(ROOT/'runners/typed_recall_normal_inputs.py').recipes(fixture)
    selected=[r for r in recipes if r['cell_id'] in {'eligibility/valid-from-equals-now','eligibility/valid-until-equals-now'}]
    results=await load(ROOT/'adapters/typed_recall_normal_cases.py').run_cases(selected,tmp_path)
    oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
    for row in results:
        assert oracle.assess_normal(fixture,row)['status']=='PASS'
        for mutation in ('interval','clock','receipt_hash','evidence_ids','spans'):
            bad=copy.deepcopy(row);o=bad['observations']
            event=next(e for e in o['calls'] if e['call']=='apply_memory_mutation_plan')
            if mutation=='interval':event['plan']['operations'][0]['valid_time_interval']['valid_until']=None
            elif mutation=='clock':o['recalls'][0]['now']+=1
            elif mutation=='receipt_hash':o['sources'][0]['receipt']['receipt_hash']='f'*64
            elif mutation=='evidence_ids':o['sources'][0]['receipt']['operations'][0]['evidence_ids']=[]
            else:event['plan']['operations'][0]['evidence_spans']=[]
            assert oracle.assess_normal(fixture,bad)['status']=='FAIL',mutation
    row=next(r for r in results if 'valid-until-equals-now' in r['cell_id'])
    bad=copy.deepcopy(row);v=bad['observations']['recalls'][0]['execution']
    v['decision'].update(outcome='rejected',reason_codes=['recall_invalid_plan'])
    v['decision_hash']=oracle.sdk_domain_hash('simple-harness/recall-decision/v4',v['decision'])
    v['result'].update(decision_hash=v['decision_hash'],reason_codes=['recall_invalid_plan'])
    v['result_hash']=oracle.sdk_domain_hash('simple-harness/typed-recall-result/v1',v['result'])
    replay=copy.deepcopy(v);replay.update(replayed=True,candidate_query_started=False,candidate_query_count=0)
    bad['observations']['recalls'][0]['replay']=replay
    assert oracle.assess_normal(fixture,bad)['status']=='FAIL'


def test_evidence_invalidation_revokes_previously_admitted_cells():
    bridge=load(ROOT/'runners/typed_recall_bridge.py')
    summary={'layers':{'public':{'status':'NOT_RUN/BLOCKED','passed_cells':['eligibility/a']}},
             'cell_results':{'eligibility/a':{'status':'PASS','reason':''}}}
    bridge.invalidate_admissions(summary,'public','evidence changed')
    assert summary['layers']['public']['passed_cells']==[]
    assert summary['cell_results']['eligibility/a']['status']=='FAIL'


@pytest.mark.asyncio
async def test_state_executor_reaches_real_head_suppression_and_partial_group(tmp_path):
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    inputs={'payloads':{k:{**v,'qualifiers':[]} for k,v in fixture['conflict_write_oracle']['canonical_payloads'].items()},
        'state_cells':['eligibility/current-head','eligibility/stale-head','eligibility/suppressed',
            'eligibility/ordinary-resolved','eligibility/ordinary-contested','conflict-state/contested-dependent-partial']}
    rows=await load(ROOT/'adapters/typed_recall_conflict_cases.py').run_state_cases(inputs,tmp_path)
    oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
    assert len(rows)==6
    for row in rows:
        assert 'exception' not in row['observations'],row['observations'].get('exception')
        judged=oracle.assess_state(fixture,row)
        assert judged['status']=='BLOCKED' and judged['business_assertions'],judged


@pytest.mark.asyncio
async def test_registered_typed_authority_reaches_recall_and_rejects_reference_tamper(tmp_path):
    import dataclasses
    module=load(ROOT/'adapters/typed_recall_case_manager.py')
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    recipes=load(ROOT/'runners/typed_recall_normal_inputs.py').recipes(fixture)
    selected=[r for r in recipes if r['cell_id'] in {
        'eligibility/epistemic:semantic:verified_external:source_verified',
        'eligibility/epistemic:semantic:observed_behavior:repeated_observation'}]
    assert len(selected)==2
    rows=await load(ROOT/'adapters/typed_recall_normal_cases.py').run_cases(selected,tmp_path)
    oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
    for row in rows:
        assert 'exception' not in row['observations'],row['observations'].get('exception')
        judged=oracle.assess_normal(fixture,row)
        assert judged['status']=='PASS',judged
        bad=copy.deepcopy(row)
        event=next(e for e in bad['observations']['calls'] if e['call']=='resolve_typed_observation')
        event['receipt']['value_hash']='f'*64
        assert oracle.assess_normal(fixture,bad)['status']=='FAIL'
    case=await module.CaseManager(tmp_path/'resolver.db').open()
    try:
        await case.evidence('independent text','typed-source','verified_external','source_verified')
        receipt=case.typed_receipts['typed-source-typed'];ref=case.typed_ref(receipt)
        assert await case.authority.resolve_typed_observation(ref)==receipt
        with pytest.raises(ValueError,match='binding'):
            await case.authority.resolve_typed_observation(dataclasses.replace(ref,value_hash='f'*64))
    finally:
        await case.close()


@pytest.mark.asyncio
async def test_rejection_baseline_requires_business_and_real_terminal_delta(tmp_path):
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
    module=load(ROOT/'adapters/typed_recall_case_manager.py')
    case=await module.CaseManager(tmp_path/'control.db').open()
    try:
        payload=next(v['provider_payload'] for v in fixture['approved_oracle']['semantic_source_vectors'] if v['id']=='incumbent')
        await case.seed({'memory_type':'semantic','payload':payload},evidence_id='evidence-relation-1')
        context,plan=case.request(query='3.11')
        rows=await case.cases.replay_cases(case.manager,case.principal,context,plan,{'mutations':[],'unsupported':[]},case.now,case.snapshot)
        baseline=rows[0]['observations'];state=baseline['after_control']
        oracle.check_rejection_control(fixture,baseline,state)
        bad=copy.deepcopy(baseline);bad['first']['result']['items']=[]
        with pytest.raises(ValueError,match='no-fault control failed'):
            oracle.check_rejection_control(fixture,bad,state)
        bad=copy.deepcopy(baseline);bad['before_control']=copy.deepcopy(bad['after_control'])
        with pytest.raises(ValueError,match='control delta'):
            oracle.check_rejection_control(fixture,bad,state)
        bad=copy.deepcopy(baseline)
        for key in ('before_control','after_control'):
            manifest=bad[key]['manifest']
            for root in manifest['table_roots']:
                if root['table_name'] in oracle.FINAL_TABLES:
                    manifest['total_row_count']-=root['row_count'];root.update(row_count=0,first_leaf_hash=None,last_leaf_hash=None)
            bad[key]['payload_hash']=oracle.hash_json(manifest)
        with pytest.raises(ValueError):
            oracle.check_rejection_control(fixture,bad,bad['after_control'])
    finally:
        await case.close()


@pytest.mark.asyncio
async def test_unknown_recipient_diagnostic_completes_input_without_granting_access(tmp_path):
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    recipes=load(ROOT/'runners/typed_recall_normal_inputs.py').recipes(fixture)
    rows=[r for r in recipes if r['cell_id'].startswith('eligibility/disclosure:UNKNOWN:') and r['purpose']!='AUDIT']
    assert len(rows)==20
    results=await load(ROOT/'adapters/typed_recall_normal_cases.py').run_cases(rows,tmp_path)
    oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
    for row in results:
        assert 'exception' not in row['observations'],row['observations'].get('exception')
        judged=oracle.assess_normal(fixture,row)
        assert judged['status']=='PASS',judged
        context=row['observations']['recalls'][0]['context']
        assert context['disclosure_context']['recipient']=='unknown'
        assert 'disclosure_unknown_recipient' in context['disclosure_context']['reason_codes']


@pytest.mark.asyncio
async def test_lifecycle_history_uses_real_authorized_revisions_before_recall(tmp_path):
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    compiler=load(ROOT/'runners/typed_recall_normal_inputs.py')
    recipes=[r for r in compiler.recipes(fixture) if len(r.get('lifecycle_path',[]))>1]
    assert len(recipes)==14
    rows=await load(ROOT/'adapters/typed_recall_normal_cases.py').run_cases(recipes,tmp_path)
    oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
    for row in rows:
        assert 'exception' not in row['observations'],(row['cell_id'],row['observations'].get('exception'))
        judged=oracle.assess_normal(fixture,row)
        assert judged['status']!='FAIL', (row['cell_id'],judged)
        assert judged['business_assertions'],(row['cell_id'],judged)
        if row['cell_id'].startswith(('eligibility/episode-','eligibility/semantic-')):
            assert judged['status']=='PASS',judged
        changed=copy.deepcopy(row)
        changed['observations']['sources'][-1]['receipt']['operations'][0]['revision']=1
        assert oracle.assess_normal(fixture,changed)['status']=='FAIL'
        for attack in ('initial_payload','empty_action_ref','wrong_resolved_target'):
            changed=copy.deepcopy(row);o=changed['observations']
            events=[e for e in o['calls'] if e['call']=='apply_memory_mutation_plan']
            event=events[0] if attack=='initial_payload' else events[-1]
            if attack=='initial_payload':
                payload=event['plan']['operations'][0]['payload']
                field='title' if 'title' in payload else 'object_value' if 'object_value' in payload else 'name' if 'name' in payload else 'action'
                payload[field]='forged prior input'
            elif attack=='empty_action_ref':event['plan']['operations'][0]['action_authority_ref']={}
            else:
                grant=next(e for e in o['calls'] if e['call']=='resolve_memory_action_authority')
                grant['grant']['intent']['target_revision']=99
            plan_hash=oracle.sdk_domain_hash('simple-harness/memory-mutation-plan/v5',event['plan'])
            event['result']['plan_hash']=plan_hash
            event['result_hash']=oracle.sdk_domain_hash('simple-harness/memory-mutation-apply-result/v4',event['result'])
            source=next(e for e in o['sources'] if e['receipt']['plan_id']==event['plan']['plan_id'])
            source['receipt']['plan_hash']=plan_hash
            assert oracle.assess_normal(fixture,changed)['status']=='FAIL',attack


@pytest.mark.asyncio
async def test_short_real_registration_projection_and_mixed_public_dispatch(tmp_path):
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    inputs=load(ROOT/'runners/typed_recall_normal_inputs.py').short_inputs(fixture)
    rows=await load(ROOT/'adapters/typed_recall_short_cases.py').run_cases(inputs,tmp_path)
    assert len(rows)==6
    oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
    for row in rows:
        assert 'exception' not in row['observations'],(row['cell_id'],row['observations'].get('exception'))
        judged=oracle.assess_short(fixture,row)
        assert judged['status']=='BLOCKED' and judged['business_assertions'],(row['cell_id'],judged)
