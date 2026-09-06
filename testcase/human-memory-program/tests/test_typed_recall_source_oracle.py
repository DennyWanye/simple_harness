"""Exact-source only: real ten-cell setup plus independent witness tampering."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def load(path):
    spec=importlib.util.spec_from_file_location(path.stem,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


@pytest.mark.asyncio
async def test_source_ten_complete_state_and_rejection_counterexamples(tmp_path):
    fixture=json.loads((ROOT/'fixtures/typed-recall-v3.json').read_text())
    layers=json.loads((ROOT/'fixtures/typed-recall-execution-layers-v1.json').read_text())
    pin=layers['clean_wheel_public_manager']['candidate_memory_identity']
    checkout=Path('/Users/denny/projects/simple-harness-memory-sdk-0613-runner-source')
    bridge=load(ROOT/'runners/typed_recall_bridge.py')
    identity=bridge.source_identity(checkout,pin)
    wheel='/Users/denny/projects/simple-harness-memory-sdk-typed-short-sources/.local-test-evidence/2026-09-06/typed-short-sources/artifact/build1/simple_harness_memory_sdk-0.6.13-py3-none-any.whl'
    candidate=bridge.wheel_identity(wheel,pin['wheel_sha256'],pin['source_commit'],'simple-harness-memory-sdk','simple_harness_memory')
    request={'layer':'source','source_identity':identity,'candidate_identity':{'memory':candidate},
             'cell_ids':layers['source_exact_commit_integration']['exact_cells'],
             'inputs':{'claim':next(r['source'] for r in fixture['approved_oracle']['semantic_source_vectors'] if r['id']=='incumbent')}}
    adapter=load(ROOT/'adapters/typed_recall_source_cases.py');oracle=load(ROOT/'runners/typed_recall_a2_oracle.py')
    state=load(ROOT/'runners/typed_recall_source_oracle.py')
    cells=await adapter.run(request,tmp_path)
    assert len(cells)==10
    for cell in cells:
        verdict=oracle.assess_source(cell)
        assert verdict['status']=='PASS',(cell['cell_id'],verdict)
    fault=next(c for c in cells if c['cell_id'].endswith('decision-header'))
    for attack in ('nonfinal','missing_table','pk','request','attempt','terminal','item','rollback'):
        cell=copy.deepcopy(fault);o=cell['observations'];snap=o['full_state']['recovery_after']
        if attack=='missing_table':
            del snap['tables']['cognitive_memory_heads']
        elif attack=='pk':snap['tables']['typed_recall_results']['pk']=['request_id']
        else:
            table={'nonfinal':'cognitive_memory_heads','request':'typed_recall_requests','attempt':'typed_recall_attempts','terminal':'typed_recall_terminals','item':'typed_recall_result_items','rollback':'typed_recall_requests'}[attack]
            if attack=='rollback':snap=o['full_state']['immediate']
            row=snap['tables'][table]['rows'][0]
            field={'nonfinal':'current_revision','request':'request_hash','attempt':'attempt_hash','terminal':'attempt_id','item':'ordinal','rollback':'deadline_at'}[attack]
            row[field]=999 if isinstance(row[field],(int,float)) else 'forged'
            snap['tables'][table]['root_hash']=state.digest(snap['tables'][table]['rows'])
        assert oracle.assess_source(cell)['status']=='FAIL',attack
    for original in (c for c in cells if c['observations'].get('corruption')):
        for attack in ('wrong_layer','wrong_error','member_hash','group_hash','unrelated_write','recall_sent'):
            cell=copy.deepcopy(original);o=cell['observations']
            if attack=='wrong_layer':o['exception_frames']=[]
            elif attack=='wrong_error':o['exception']['reason']='unrelated failure'
            elif attack=='recall_sent':o['recall_calls']=1
            else:
                snap=o['full_state']['before' if attack!='unrelated_write' else 'rejected']
                table={'member_hash':'cognitive_conflict_members','group_hash':'cognitive_conflict_groups','unrelated_write':'cognitive_memory_heads'}[attack]
                row=snap['tables'][table]['rows'][0]
                key={'member_hash':'member_hash','group_hash':'group_hash','unrelated_write':'current_revision'}[attack]
                row[key]=999 if key=='current_revision' else 'f'*64
                snap['tables'][table]['root_hash']=state.digest(snap['tables'][table]['rows'])
            assert oracle.assess_source(cell)['status']=='FAIL',(original['cell_id'],attack)
