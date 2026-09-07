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
    import os
    checkout=Path(os.environ.get('TYPED_RECALL_SOURCE_CHECKOUT', ROOT.parents[2]/'simple-harness-memory-sdk-0626-source'))
    bridge=load(ROOT/'runners/typed_recall_bridge.py')
    identity=bridge.source_identity(checkout,pin)
    wheel_name='simple_harness_memory_sdk-'+pin['version']+'-py3-none-any.whl'
    vendor_wheel=ROOT.parents[1]/'backend/vendor'/wheel_name
    # Before Host adopts the pinned candidate the exact wheel only exists in the local build artifact dir.
    fallback=sorted(ROOT.parents[1].glob('.local-test-evidence/*/memory*-artifact/build/'+wheel_name))
    wheel=os.environ.get('TYPED_RECALL_MEMORY_WHEEL', str(vendor_wheel if vendor_wheel.is_file() or not fallback else fallback[-1]))
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

    for mutation in ('schema2','extra_key'):
        cell=copy.deepcopy(fault)
        for snap in cell['observations']['full_state'].values():
            table=snap['tables']['typed_recall_requests']
            for row in table['rows']:
                wire=json.loads(row['request_json'])
                if mutation=='schema2':wire['schema_version']=2
                else:wire['unknown']=True
                row['request_json']=state.canonical(wire)
            table['root_hash']=state.digest(table['rows'])
        verdict=oracle.assess_source(cell)
        assert verdict['status']=='FAIL' and verdict['reason']=='durable request exact schema/keys differ',(mutation,verdict)
    original=next(c for c in cells if 'one-member' in c['cell_id'])
    for mutation in ('swap','reuse'):
        cell=copy.deepcopy(original);o=cell['observations']
        for snap in o['full_state'].values():
            table=snap['tables']['cognitive_evidence_spans']
            originals=copy.deepcopy(table['rows'])
            for row in table['rows']:
                if row['revision'] not in (7,8):continue
                revision=8 if mutation=='swap' and row['revision']==7 else 7
                donor=next(r for r in originals if r['memory_id']==row['memory_id'] and r['revision']==revision)
                identity={k:row[k] for k in ('memory_id','revision','ordinal')}
                row.update(donor);row.update(identity)
            table['root_hash']=state.digest(table['rows'])
            members=snap['tables']['cognitive_conflict_members']
            for member in members['rows']:
                spans=sorted([r for r in table['rows'] if r['memory_id']==member['memory_id'] and r['revision']==member['revision']],key=lambda r:r['ordinal'])
                member['evidence_set_hash']=state.digest([{k:v for k,v in r.items() if k not in {'memory_id','revision'}} for r in spans])
                member['member_hash']=state.digest({k:v for k,v in member.items() if k!='member_hash'})
            members['root_hash']=state.digest(members['rows'])
        # Keep the original damaged group's pre-corruption commitment, now for the wrong-source pair.
        before=o['full_state']['before'];group=before['tables']['cognitive_conflict_groups']['rows'][0]
        hashes=[r['member_hash'] for r in sorted(before['tables']['cognitive_conflict_members']['rows'],key=lambda r:r['ordinal'])]
        group_hash=state.digest({**{k:v for k,v in group.items() if k!='group_hash'},'member_hashes':hashes})
        for snap in o['full_state'].values():
            table=snap['tables']['cognitive_conflict_groups'];table['rows'][0]['group_hash']=group_hash;table['root_hash']=state.digest(table['rows'])
        execution=o['control']['execution']
        old_hash=execution['decision']['confirmation_groups'][0]['conflict_group_hash']
        def replace_group(value):
            if isinstance(value,dict):return {k:replace_group(v) for k,v in value.items()}
            if isinstance(value,list):return [replace_group(v) for v in value]
            return group_hash if value==old_hash else value
        execution=replace_group(execution);o['control']['execution']=execution
        execution['decision_hash']=state.digest({'domain':'simple-harness/recall-decision/v4','payload':execution['decision']})
        execution['result']['decision_hash']=execution['decision_hash']
        execution['result_hash']=state.digest({'domain':'simple-harness/typed-recall-result/v1','payload':execution['result']})
        execution['hashes'].update(decision=execution['decision_hash'],result=execution['result_hash'])
        verdict=oracle.assess_source(cell)
        assert verdict['status']=='FAIL' and verdict['reason']=='original distinct member evidence IDs differ',verdict
