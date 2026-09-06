"""Remaining frozen lifecycle cases using actual public mutation/signal steps."""
import copy
import importlib.util
from pathlib import Path

STATES={'in_progress','completed','rescheduled','cancelled','expired','candidate'}


def load(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def supported(recipe):
    return (recipe['family']=='lifecycle' and recipe['seed']['memory_type']=='prospective'
        and recipe['seed']['state'] in STATES)


async def run(case,recipe):
    helper=load('typed_recall_case_manager');scheduler=load('typed_recall_prospective_cases')
    final_state=recipe['seed']['state']
    seed={**recipe['seed'],'state':'candidate' if final_state=='candidate' else 'pending'}
    trace=[]
    target=await case.seed(seed,operation_id='lifecycle-create',evidence_id='lifecycle-initial')
    trace.append({'kind':'mutation','source':copy.deepcopy(case.sources[-1])})
    trigger=case.payload(seed).trigger
    if final_state!='candidate':
        ack=await scheduler.register(case,target,trigger)
        trace.append({'kind':'signal','result':ack.to_json()})
        if final_state in {'in_progress','completed'}:
            matched=await scheduler.signal(case,target,trigger,kind='event_occurred',state='pending',next_state='triggered')
            trace.append({'kind':'signal','result':matched.to_json()})
            target=helper.h.ExistingMemoryTarget(matched.memory_id,matched.committed_revision)
        target=await case.seed(recipe['seed'],kind='revise',target=helper.h.ExistingMemoryTarget(target.memory_id,target.revision),
            operation_id='lifecycle-revise',evidence_id='lifecycle-final')
        trace.append({'kind':'mutation','source':copy.deepcopy(case.sources[-1])})
        if final_state=='rescheduled':
            ack=await scheduler.register(case,target,trigger,state='rescheduled',identity='rescheduled-registration')
            trace.append({'kind':'signal','result':ack.to_json()})
    params={'query':recipe['seed']['payload']['action'],'memory_types':('prospective',)}
    result=await case.recall(**params,key='original-lifecycle')
    result['replay']=(await case.recall(**params,key='original-lifecycle'))['execution']
    original={'recipe':recipe,'calls':copy.deepcopy(case.events),'sources':copy.deepcopy(case.sources),
        'recalls':[result],'lifecycle_trace':trace}
    if final_state=='candidate':
        # Separate, append-only positive control on the same ID. Never replace
        # the candidate observation or claim a registration for candidate itself.
        control=await case.seed({**recipe['seed'],'state':'pending'},kind='revise',
            target=helper.h.ExistingMemoryTarget(target.memory_id,target.revision),
            operation_id='candidate-control',evidence_id='candidate-control-evidence')
        ack=await scheduler.register(case,control,trigger,identity='candidate-control-registration')
        recall=await case.recall(**params,key='candidate-positive-control')
        recall['replay']=(await case.recall(**params,key='candidate-positive-control'))['execution']
        original['candidate_control']={'source':copy.deepcopy(case.sources[-1]),'ack':ack.to_json(),
            'recall':recall,'calls':copy.deepcopy(case.events)}
    return original
