"""Compile input-only recipes from the frozen original cases (no SDK imports)."""
import copy
import json
from itertools import product


def recipes(fixture):
    profiles = {r['memory_type']: {k: copy.deepcopy(r['source_record'][k])
                for k in r['allowed_payload_fields']} for r in fixture['minimal_projection_oracle']}
    profiles['semantic']['qualifiers'] = []
    claim = next(r['source'] for r in fixture['approved_oracle']['semantic_source_vectors'] if r['id']=='incumbent')
    claim = {k:claim[k] for k in ('subject_entity','predicate','object_value','qualifiers')}
    rows = []
    def add(name, family, spec, **kwargs):
        rows.append(dict(cell_id=name, family=family, seed=spec, **kwargs))
    def seed(kind='semantic', **kwargs):
        return dict(memory_type=kind, payload=copy.deepcopy(claim if kind=='semantic' else profiles[kind]), **kwargs)
    for row in fixture['eligibility_cases']:
        if row['axis']=='validity' and row['id']!='valid-until-null-unbounded':
            add('eligibility/'+row['id'], 'validity', seed(valid_from=row['valid_from'],valid_until=row['valid_until']),now=row['now'])
    for name in ('not-suppressed','ordinary-uncontested'):
        add('eligibility/'+name,'basic',seed())
    for row in fixture['eligibility_cases']:
        if row['axis']=='prospective_trigger':
            add('eligibility/'+row['id'],'prospective_trigger',seed('prospective',state='pending'),
                trigger_contract=copy.deepcopy(row))
    for row in fixture['lifecycle_cases']:
        add('eligibility/'+row['id'], 'lifecycle', seed(row['memory_type'],state=row['state']))
    axis = fixture['exhaustive_axis_contract']['epistemic_verification']
    for kind, epi, verification in product(axis['memory_types'],axis['epistemic_values'],axis['verification_values']):
        add(f'eligibility/epistemic:{kind}:{epi}:{verification}', 'epistemic',seed(kind,epistemic=epi,verification=verification))
    axis = fixture['exhaustive_axis_contract']['disclosure']
    for recipient,purpose,privacy in product(axis['recipients'],axis['purposes'],axis['privacy_values']):
        add(f'eligibility/disclosure:{recipient}:{purpose}:{privacy}', 'disclosure',seed(),
            privacy=privacy,recipient=recipient,purpose=purpose)
    for row in fixture['attribute_floor_cases']:
        add(f"eligibility/attribute:{row['recipient']}:{row['attribute']}", 'attribute',seed(),
            recipient=row['recipient'],purpose='task_execution',privacy=row['privacy'],attributes=[row['attribute']])
    for kind in ('semantic','episode','procedure','prospective'):
        add('selection-budget/projection:'+kind, 'projection',dict(memory_type=kind,payload=profiles[kind]),privacy='SENSITIVE')
    for row in fixture['budget_oracle']['literal_cases']:
        if row['id'].startswith('semantic-'):
            payload = json.loads(row['canonical_json'])[0]['payload']
            payload['qualifiers'] = []
            add('selection-budget/budget:'+row['id'], 'budget',dict(memory_type='semantic',payload=payload),
                limits=[{k:v for k,v in row['exact_limit'].items() if k.startswith('max_')},
                        {k:v for k,v in row['limit_plus_one'].items() if k.startswith('max_')}])
    for row in fixture['ranking_oracle']['vector_degradation_cases']:
        add('selection-budget/'+row['id'], 'vector',seed(),modes=row['requested_lanes'])
    for row in rows:
        if row['family']=='lifecycle':row['lifecycle_path']=lifecycle_path(row)
    return rows


def lifecycle_path(recipe):
    """Input-only legal histories; the frozen final state is never rewritten."""
    if recipe['family']!='lifecycle':return [copy.deepcopy(recipe['seed'])]
    seed=recipe['seed'];kind=seed['memory_type'];state=seed['state']
    paths={
        'episode':{'amended':['active','amended'],'disputed':['active','disputed'],'superseded':['active','superseded']},
        'semantic':{'superseded':['active','superseded']},
        'procedure':{**{s:['active',s] for s in ('reinforced','revised','inapplicable','superseded')},
            'eligible':['draft','eligible']},
        'prospective':{**{s:['pending',s] for s in ('triggered','rescheduled','cancelled','expired')},
            'in_progress':['pending','triggered','in_progress'],'completed':['pending','triggered','completed']}}
    return [{**copy.deepcopy(seed),'state':s} for s in paths[kind].get(state,[state])]


def short_inputs(fixture):
    projection=next(r for r in fixture['minimal_projection_oracle'] if r['memory_type']=='short_horizon')['source_record']
    source=next(v['provider_payload'] for v in fixture['approved_oracle']['semantic_source_vectors'] if v['id']=='incumbent')
    cases=[{'cell_id':'protocol/'+name} for name in ('mixed-long-short','short-only')]
    for row in fixture['eligibility_cases']:
        if row['id'] in {'short-chain-complete','short-expiry-equals-now','short-future','short-source-suppressed'}:
            cases.append({'cell_id':'eligibility/'+row['id'],**{k:v for k,v in row.items() if k in {'now','occurred_at','expires_at'}}})
    return {'semantic':{'memory_type':'semantic','payload':copy.deepcopy(source)},
        'text':projection['content'],'occurred_at':projection['occurred_at'],'cases':cases}
