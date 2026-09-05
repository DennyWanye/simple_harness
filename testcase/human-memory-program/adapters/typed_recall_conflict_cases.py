"""Real revision 7 -> 8 -> 9 public conflict construction; no revision relabeling."""
import dataclasses as dc
import simple_harness as h
import importlib.util
from pathlib import Path


def helper():
    spec=importlib.util.spec_from_file_location('case_manager',Path(__file__).with_name('typed_recall_case_manager.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


async def seed_revision7(case, payload):
    spec=dict(memory_type='semantic',payload=payload)
    op=await case.seed(spec,evidence_id='evidence-user-python-311')
    for revision in range(2,8):
        op=await case.seed(spec,operation_id=f'revise-{revision}',evidence_id='evidence-user-python-311',
            kind='revise',target=h.ExistingMemoryTarget(op.memory_id,op.revision))
    return op


async def contest(case,op,payload,**kwargs):
    return await case.seed(dict(memory_type='semantic',payload=payload,conflict_status='contested'),
        operation_id=kwargs.pop('operation_id','contest'),evidence_id=kwargs.pop('evidence_id','evidence-user-python-312'),
        kind='contest',target=h.ExistingMemoryTarget(op.memory_id,op.revision),**kwargs)


async def run_cases(inputs,workspace):
    module=helper();rows=[]
    payloads=inputs['payloads']
    for index,recipe in enumerate(inputs['cases']):
        case=await module.CaseManager(workspace/f'conflict-{index}.sqlite').open()
        observed=dict(conflict_recipe=recipe,calls=case.events,sources=case.sources)
        try:
            op=await seed_revision7(case,payloads['incumbent'])
            observed['revision7']=op.to_json()
            name=recipe['id']
            if name.startswith('contest-') and name!='contest-create-distinct-evidence':
                observed['before']=await case.snapshot()
                try:
                    if name=='contest-missing-evidence':await contest(case,op,payloads['challenger'],no_evidence=True)
                    elif name=='contest-same-content':await contest(case,op,payloads['incumbent'])
                    elif name=='contest-stale-target':await contest(case,dc.replace(op,revision=6),payloads['challenger'])
                    elif name=='contest-no-new-evidence':await contest(case,op,payloads['challenger'],evidence_id='evidence-user-python-311')
                    else:raise NotImplementedError('frozen attack has no public input implementation: '+name)
                except Exception as exc:observed['rejection']=dict(type=type(exc).__name__,reason=str(exc))
                observed['after']=await case.snapshot()
            else:
                op=await contest(case,op,payloads['challenger']);observed['revision8']=op.to_json()
                observed['confirmation']=await case.recall(query='preferred_python',key='conflict-before')
                if name.startswith('resolve-'):
                    kind='supersede' if name.endswith('supersede') else 'suppress' if name.endswith('suppress') else 'revise'
                    payload=payloads['incumbent'] if name=='resolve-select-incumbent' else payloads['replacement']
                    state='superseded' if kind=='supersede' else 'forgotten' if kind=='suppress' else 'active'
                    op=await case.seed(dict(memory_type='semantic',payload=payload,conflict_status='resolved',state=state),
                        operation_id='resolve',evidence_id='evidence-user-resolution',kind=kind,
                        target=h.ExistingMemoryTarget(op.memory_id,op.revision))
                    observed['revision9']=op.to_json()
                    observed['after_resolution']=await case.recall(query='preferred_python',key='conflict-after')
                observed['state']=await case.snapshot()
        except Exception as exc:observed['exception']=dict(type=type(exc).__name__,reason=str(exc))
        finally:await case.close()
        rows.append(dict(cell_id='conflict-state/'+recipe['id'],status='OBSERVED',reason='',observations=observed))
    return rows
