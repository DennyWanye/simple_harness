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
        action=recipe['seed']['payload']['action']
        case.events.append({'call':'ProspectiveMemoryPayload','input':{'action':action,'trigger':None}})
        try:helper.h.ProspectiveMemoryPayload(action,None)
        except TypeError as exc:o['construction_rejection']={'type':type(exc).__name__,'reason':str(exc)}
        # A missing typed trigger is also unreachable through the wire form: from_json is exact-keys.
        untyped={'action':action,'trigger':{'kind':'event','event':'release_succeeded'}}
        case.events.append({'call':'ProspectiveMemoryPayload.from_json','input':untyped})
        try:
            helper.h.ProspectiveMemoryPayload.from_json(untyped)
            o['wire_rejection']={'constructed':True}
        except Exception as exc:o['wire_rejection']={'type':type(exc).__name__,'reason':str(exc)}
        # Positive control on the same database: the identical action with a complete typed
        # trigger is seeded, registered and recalled, so the zero above is attributable to the
        # trigger axis and not to an empty store or a non-matching query.
        target=await case.seed(recipe['seed'])
        scheduler=load('typed_recall_prospective_cases')
        trigger=case.payload(recipe['seed']).trigger
        ack=await scheduler.register(case,target,trigger)
        params=dict(query=action,memory_types=('prospective',))
        positive=await case.recall(**params,key='trigger-missing-control')
        positive['replay']=(await case.recall(**params,key='trigger-missing-control'))['execution']
        o['positive_control']={'calls':copy.deepcopy(case.events),'ack':ack.to_json(),'recall':positive}
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
