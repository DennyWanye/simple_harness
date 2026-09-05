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
    return rows
