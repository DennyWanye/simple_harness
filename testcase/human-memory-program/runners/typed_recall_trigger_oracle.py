"""Original trigger-axis checks; independent from SDK implementation and no SQL."""
import importlib.util
from pathlib import Path


def assess(fixture,o,a):
    recipe=o['recipe'];original=next(r for r in fixture['eligibility_cases'] if recipe['cell_id']=='eligibility/'+r['id'])
    if original!=recipe['trigger_contract'] or original['axis']!='prospective_trigger':raise ValueError('frozen trigger input differs')
    if not original['typed_trigger_complete']:
        if (o.get('construction_rejection')!={'type':'TypeError','reason':'trigger must use a strict time or event trigger'}
                or o['sources'] or o['recalls'] or [e for e in o['calls'] if e['call']=='apply_prospective_signal']):
            raise ValueError('missing trigger exact public construction rejection differs')
        return dict(status='BLOCKED',reason='CONSTRUCTION_CONFLICT:strict public ProspectiveMemoryPayload cannot represent missing typed trigger; ingress rejection is not recall eligibility',business_assertions=['original missing-trigger input preserved; actual public DTO refusal only'])
    if len(o['sources'])!=1 or len(o['recalls'])!=1:raise ValueError('trigger setup actual source/recall missing')
    projection=a['normal_projection'](recipe['seed']);full={'memory_type':'prospective',**projection}
    a['check_seed_authority'](o,recipe,full)
    source=o['sources'][0];target=source['receipt']['operations'][0]
    event=next(e for e in o['calls'] if e['call']=='apply_memory_mutation_plan')
    op=event['plan']['operations'][0]
    if (op['kind']!='create' or op['lifecycle_state']!='pending' or op['epistemic_status']!='explicit_user'
            or op['verification_state']!='source_bound' or op['payload']!=full or target['revision']!=1):
        raise ValueError('trigger axis requires actual original pending CREATE')
    spec=importlib.util.spec_from_file_location('signal_oracle',Path(__file__).with_name('typed_recall_prospective_oracle.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    def ack(calls,result):
        m.check({'sources':o['sources'],'calls':calls,'recalls':o['recalls'],
            'prospective_binding':{'scope':'synthetic-sdk-contract-only','results':[result]}},
            {**recipe,'family':'lifecycle'})
    def recall(r,expected):
        value=r['execution'];decision=value['decision'];result=value['result'];context=r['context'];plan=r['plan']
        a['check_execution_wire'](value,context,plan)
        if (context['query']!=recipe['seed']['payload']['action'] or plan['query']!=context['query']
                or context['available_memory_types']!=['prospective'] or plan['requested_memory_types']!=['prospective']
                or context['subject']!=event['plan']['subject'] or context['run_id']!=event['plan']['run_id']
                or context['evidence_refs']!=event['plan']['evidence_refs'] or plan['evidence_refs']!=context['evidence_refs']
                or context['allowed_retrieval_modes']!=['full_text'] or plan['retrieval_modes']!=['full_text']
                or context['disclosure_context']['purpose']!='personalization' or context['disclosure_context']['recipient']!='user_self'
                or plan['budget']!=dict(max_items=8,max_bytes=16384,max_tokens=2048,deadline_ms=2000)
                or r['now']!=1788170400.0 or decision['decided_at']!=r['now']
                or decision['outcome']!=('recall' if expected else 'no_recall')
                or decision['reason_codes']!=['recall_user_fact_dependency' if expected else 'recall_no_eligible_memory']
                or len(result['items'])!=int(expected) or decision['filtered_candidate_count']!=int(expected)
                or value['candidate_query_count']!=1 or not value['candidate_query_started']
                or decision['candidate_count_stage']!='after_all_eligibility_gates'
                or result['confirmation_groups'] or decision['confirmation_groups']):raise ValueError('actual trigger eligibility/request/access differs')
        for item in result['items']:
            si=item['selected_item']
            if (si['source_ref']!=target['memory_id'] or si['source_revision']!=1
                    or si['source_content_hash']!=a['hash_json'](full) or si['source_kind']!='cognitive_memory' or si['memory_type']!='prospective'
                    or item['public_payload']!=projection or si['public_payload_hash']!=a['hash_json'](projection)
                    or item['evidence_manifest_hash']!=a['hash_json'](sorted(source['evidence_ids']))
                    or item['effective_privacy_class']!='personal' or item['information_attributes']
                    or item['score']!=round(.30/61,12) or item['cross_scope'] or item['source_task_scope_ids']):raise ValueError('trigger positive source binding differs')
        replay=r['replay']
        if not replay['replayed'] or replay['candidate_query_count']!=0 or replay['candidate_query_started'] or replay['result']!=result or replay['decision']!=decision:raise ValueError('trigger replay differs')
    present=original['current_signal_complete']
    if original['expected']!=('ELIGIBLE' if present else 'INELIGIBLE'):raise ValueError('original trigger expected differs')
    if present:ack(o['calls'],o['ack'])
    else:
        if any(e['call'] in {'apply_prospective_signal','resolve_prospective_signal_authority','synthetic_scheduler_input'} for e in o['calls']):raise ValueError('missing signal original observation contains acknowledgement')
        control=o['positive_control'];ack(control['calls'],control['ack']);recall(control['recall'],True)
    recall(o['recalls'][0],present)
    return dict(status='PASS',reason='',business_assertions=['original trigger boolean/expected unchanged','actual public source and registration presence/absence with positive control','full recall source/request/hash/access/replay bindings'])
