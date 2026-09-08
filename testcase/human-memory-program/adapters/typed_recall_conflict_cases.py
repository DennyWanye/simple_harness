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
                    if name=='contest-no-evidence':await contest(case,op,payloads['challenger'],no_evidence=True)
                    elif name=='contest-same-content':await contest(case,op,payloads['incumbent'])
                    elif name=='contest-stale-target':await contest(case,dc.replace(op,revision=6),payloads['challenger'])
                    elif name=='contest-evidence-not-distinct':
                        await case.seed(dict(memory_type='semantic',payload=payloads['challenger'],conflict_status='contested'),
                            operation_id='contest',evidence_id='evidence-user-python-311',kind='contest',
                            target=h.ExistingMemoryTarget(op.memory_id,op.revision),reuse_evidence=True)
                    elif name=='contest-active-group-exists':
                        op=await contest(case,op,payloads['challenger'])
                        observed['before']=await case.snapshot()
                        await contest(case,op,payloads['replacement'],operation_id='contest-again',evidence_id='evidence-user-python-313')
                    elif name=='contest-cross-principal':
                        case.principal=dc.replace(case.principal,actor_id='principal-2')
                        case.disclosure=case.helpers._disclosure('principal-2')
                        case.base_revision=1
                        await contest(case,op,payloads['challenger'])
                    elif name=='contest-cross-memory':
                        await contest(case,dc.replace(op,memory_id='mem-other'),payloads['challenger'])
                    else:raise NotImplementedError('frozen attack has no public input implementation: '+name)
                except Exception as exc:observed['rejection']=dict(type=type(exc).__name__,reason=str(exc))
                case.principal=dc.replace(case.principal,actor_id='principal-1')
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


async def run_state_cases(inputs,workspace):
    import simple_harness_memory as m
    module=helper();rows=[]
    payloads=inputs['payloads']
    for index,name in enumerate(inputs['state_cells']):
        case=await module.CaseManager(workspace/f'state-{index}.sqlite').open()
        o=dict(state_cell=name,calls=case.events,sources=case.sources)
        try:
            # pre/post snapshots bracket the single state transition under test; the
            # parent binds receipts and protected table roots across that transition.
            if name in {'eligibility/current-head','eligibility/stale-head'}:
                op=await seed_revision7(case,payloads['incumbent'])
                o['pre_transition']=await case.snapshot()
                op=await case.seed(dict(memory_type='semantic',payload=payloads['challenger']),operation_id='revise-8',
                    evidence_id='evidence-user-python-312',kind='revise',target=h.ExistingMemoryTarget(op.memory_id,op.revision))
                o['post_transition']=await case.snapshot()
                o['current_head']=op.to_json()
                o['recall']=await case.recall(query='3.12' if name.endswith('/current-head') else '11')
            elif name=='eligibility/suppressed':
                op=await case.seed(dict(memory_type='semantic',payload=payloads['incumbent']))
                o['before']=await case.recall(query='3.11',key='before-suppression')
                o['pre_transition']=await case.snapshot()
                request=m.SuppressionRequest('suppress-case',case.principal.actor_id,m.SuppressionScopeKind.MEMORY,
                    op.memory_id,'user_requested',case.now,purpose=m.OrdinaryMemoryPurpose.RECALL)
                event=dict(call='suppress',request=request.to_json());case.events.append(event)
                event['decision']=(await case.manager.suppress(principal=case.principal,request=request)).to_json()
                o['post_transition']=await case.snapshot()
                o['recall']=await case.recall(query='3.11',key='after-suppression')
            else:
                op=await seed_revision7(case,payloads['incumbent'])
                if name=='eligibility/ordinary-contested':o['pre_transition']=await case.snapshot()
                op=await contest(case,op,payloads['challenger'])
                if name=='eligibility/ordinary-contested':o['post_transition']=await case.snapshot()
                o['confirmation']=await case.recall(query='preferred_python',key='confirmation')
                if name=='eligibility/ordinary-resolved':
                    o['pre_transition']=await case.snapshot()
                    await case.seed(dict(memory_type='semantic',payload=payloads['incumbent'],conflict_status='resolved'),
                        operation_id='resolve',evidence_id='evidence-user-resolution',kind='revise',
                        target=h.ExistingMemoryTarget(op.memory_id,op.revision))
                    o['post_transition']=await case.snapshot()
                elif name=='conflict-state/contested-dependent-partial':
                    o['pre_transition']=await case.snapshot()
                    request=m.SuppressionRequest('suppress-challenger',case.principal.actor_id,m.SuppressionScopeKind.EVIDENCE,
                        'evidence-user-python-312','user_requested',case.now,purpose=m.OrdinaryMemoryPurpose.RECALL)
                    event=dict(call='suppress',request=request.to_json());case.events.append(event)
                    event['decision']=(await case.manager.suppress(principal=case.principal,request=request)).to_json()
                    o['post_transition']=await case.snapshot()
                o['recall']=await case.recall(query='preferred_python',key='after-state')
            o['replay']=(await case.manager.execute_typed_recall(principal=case.principal,
                context=h.RecallContext.from_json(o['recall']['context']),plan=h.RecallPlan.from_json(o['recall']['plan']),now=case.now))
            o['replay']=case.cases.execution_wire(o['replay'])
            o['state']=await case.snapshot()
        except Exception as exc:o['exception']=dict(type=type(exc).__name__,reason=str(exc))
        finally:await case.close()
        rows.append(dict(cell_id=name,status='OBSERVED',reason='',observations=o))
    return rows
