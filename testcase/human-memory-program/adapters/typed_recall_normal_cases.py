"""Input-only real public execution. Parent owns every acceptance assertion."""
import importlib.util
from pathlib import Path


def load(name):
    spec = importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def run_cases(recipes, workspace):
    helper = load('typed_recall_case_manager')
    rows = []
    for index, recipe in enumerate(recipes):
        case = helper.CaseManager(workspace/f'normal-{index}.sqlite',
            privacy=recipe.get('privacy','personal'),attributes=recipe.get('attributes',()),
            now=helper.seconds(recipe.get('now',1788170400.0)))
        observed = {'recipe':recipe,'calls':case.events,'sources':case.sources,'recalls':[]}
        opened = False
        try:
            await case.open()
            opened = True
            path=recipe.get('lifecycle_path',[recipe['seed']])
            previous=None
            for ordinal,spec in enumerate(path):
                kwargs={} if ordinal==0 else dict(kind='supersede' if spec['state']=='superseded' else 'revise',
                    target=helper.h.ExistingMemoryTarget(previous.memory_id,previous.revision))
                previous=await case.seed(spec,operation_id=f'create-{ordinal+1}',
                    evidence_id=f'evidence-case-{ordinal+1}',**kwargs)
            payload = recipe['seed']['payload']
            query = str(payload.get('object_value',payload.get('title',payload.get('name',payload.get('action')))))
            params = dict(query=query,memory_types=(recipe['seed']['memory_type'],),
                recipient=recipe.get('recipient','user_self'),purpose=recipe.get('purpose','personalization'),
                modes=recipe.get('modes',('full_text',)))
            if recipe['family']=='budget':
                limits = dict(max_items=8,max_bytes=16384,max_tokens=2048,deadline_ms=2000)
                for ordinal, limit in enumerate(recipe['limits']):
                    limits.update(limit)
                    value = await case.recall(**params,budget=limits.copy(),key=f'budget-{ordinal}')
                    value['replay'] = (await case.recall(**params,budget=limits.copy(),key=f'budget-{ordinal}'))['execution']
                    observed['recalls'].append(value)
            else:
                value = await case.recall(**params)
                value['replay'] = (await case.recall(**params))['execution']
                observed['recalls'].append(value)
        except Exception as exc:
            observed['exception'] = {'type':type(exc).__name__,'reason':str(exc)}
        finally:
            if opened:
                await case.close()
        rows.append({'cell_id':recipe['cell_id'],'status':'OBSERVED','reason':'','observations':observed})
    return rows
