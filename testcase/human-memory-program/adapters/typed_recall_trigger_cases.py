"""Original trigger-axis inputs over public APIs; missing trigger is not a recall PASS."""
import copy
import importlib.util
from pathlib import Path


def load(name):
    spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


async def run(case,recipe):
    contract=recipe['trigger_contract'];o={'recipe':recipe,'calls':case.events,'sources':case.sources,'recalls':[]}
    if not contract['typed_trigger_complete']:
        helper=load('typed_recall_case_manager')
        case.events.append({'call':'ProspectiveMemoryPayload','input':{'action':recipe['seed']['payload']['action'],'trigger':None}})
        try:helper.h.ProspectiveMemoryPayload(recipe['seed']['payload']['action'],None)
        except TypeError as exc:o['construction_rejection']={'type':type(exc).__name__,'reason':str(exc)}
        return o
    target=await case.seed(recipe['seed'])
    scheduler=load('typed_recall_prospective_cases')
    trigger=case.payload(recipe['seed']).trigger
    if contract['current_signal_complete']:
        ack=await scheduler.register(case,target,trigger)
        o['ack']=ack.to_json()
    params=dict(query=recipe['seed']['payload']['action'],memory_types=('prospective',))
    recall=await case.recall(**params,key='original-trigger-axis')
    recall['replay']=(await case.recall(**params,key='original-trigger-axis'))['execution']
    o['recalls']=[recall];o=copy.deepcopy(o)
    if not contract['current_signal_complete']:
        ack=await scheduler.register(case,target,trigger)
        positive=await case.recall(**params,key='registration-control')
        positive['replay']=(await case.recall(**params,key='registration-control'))['execution']
        o['positive_control']={'calls':copy.deepcopy(case.events),'ack':ack.to_json(),'recall':positive}
    return o
