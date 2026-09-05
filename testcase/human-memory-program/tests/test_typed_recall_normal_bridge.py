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
    assert len(rows)==290 and len({r['cell_id'] for r in rows})==290
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
