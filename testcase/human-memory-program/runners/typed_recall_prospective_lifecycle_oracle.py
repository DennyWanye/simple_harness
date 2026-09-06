"""Independent checks of actual append-only public lifecycle traces; no SDK."""
import importlib.util
from pathlib import Path


def check(fixture,o,a):
    recipe=o['recipe'];state=recipe['seed']['state']
    if recipe['family']!='lifecycle' or recipe['seed']['memory_type']!='prospective':raise ValueError('lifecycle recipe kind differs')
    if state not in {'candidate','in_progress','completed','rescheduled','cancelled','expired'}:raise ValueError('lifecycle scope differs')
    spec=importlib.util.spec_from_file_location('signal_oracle',Path(__file__).with_name('typed_recall_prospective_oracle.py'))
    signal=importlib.util.module_from_spec(spec);spec.loader.exec_module(signal)
    projection=a['normal_projection'](recipe['seed']);full={'memory_type':'prospective',**projection}
    calls=o['calls'];sources=o['sources'];trace=o['lifecycle_trace']
    if len(sources)!=(1 if state=='candidate' else 2):raise ValueError('lifecycle mutation cardinality differs')
    initial={**recipe['seed'],'state':'candidate' if state=='candidate' else 'pending'}
    for index,source in enumerate(sources):
        expected=initial if index==0 else recipe['seed']
        if source['input']!=expected or source['source_wire']!=full:raise ValueError('lifecycle original source differs')
        a['check_seed_authority'](o,{**recipe,'seed':expected},full,source_index=index,check_recall_refs=False)
        event=next(e for e in calls if e['call']=='apply_memory_mutation_plan' and e['plan']['plan_id']==source['receipt']['plan_id'])
        op=event['plan']['operations'][0];actual=source['receipt']['operations'][0]
        base=2 if index and state in {'in_progress','completed'} else index
        if (op['kind']!=('create' if index==0 else 'revise') or op['lifecycle_state']!=expected['state']
                or op['epistemic_status']!='explicit_user' or op['verification_state']!='source_bound'
                or actual['revision']!=base+1):raise ValueError('lifecycle exact mutation state/revision differs')
        if index:
            if actual['memory_id']!=sources[0]['receipt']['operations'][0]['memory_id']:
                raise ValueError('lifecycle returned revision changed target memory identity')
            a['check_action_grant'](o,event['plan'])
            if op['target']!={'target_kind':'existing_memory','memory_id':sources[0]['receipt']['operations'][0]['memory_id'],'revision':base}:
                raise ValueError('lifecycle mutation did not use actual signal revision')
    # Validate projections of actual signal calls with the existing independent
    # signal verifier. No synthetic result/receipt is introduced in these views.
    def verify_series(view_sources,view_calls,expected_state,identity,results):
        view={'sources':view_sources,'calls':view_calls,'recalls':o['recalls'],
            'prospective_binding':{'scope':'synthetic-sdk-contract-only','results':results}}
        signal.check(view,{**recipe,'seed':{**recipe['seed'],'state':expected_state}},
            registration_state='rescheduled' if expected_state=='rescheduled' else 'pending',ack_identity=identity)
    if state!='candidate':
        final_plan=sources[-1]['receipt']['plan_id']
        boundary=next(i for i,e in enumerate(calls) if e['call']=='apply_memory_mutation_plan' and e['plan']['plan_id']==final_plan)
        prefix=calls[:boundary]
        prefix_results=[e['result'] for e in prefix if e['call']=='apply_prospective_signal']
        verify_series(sources[:1],prefix,'triggered' if state in {'in_progress','completed'} else 'pending',
            'fixture-registration_accepted',prefix_results)
        suffix=calls[boundary:]
        if state=='rescheduled':
            verify_series(sources,suffix,'rescheduled','rescheduled-registration',
                [e['result'] for e in suffix if e['call']=='apply_prospective_signal'])
        elif any(e['call']=='apply_prospective_signal' for e in suffix):raise ValueError('unexpected post-mutation signal')
    elif any(e['call']=='apply_prospective_signal' for e in calls):raise ValueError('candidate cannot have invented registration')
    if [t['source'] for t in trace if t['kind']=='mutation']!=sources:raise ValueError('trace source differs')
    if [t['result'] for t in trace if t['kind']=='signal']!=[e['result'] for e in calls if e['call']=='apply_prospective_signal']:
        raise ValueError('trace signal differs')
    if len(o['recalls'])!=1:raise ValueError('original lifecycle recall cardinality differs')
    recall=o['recalls'][0];value=recall['execution'];result=value['result'];decision=value['decision']
    a['check_execution_wire'](value,recall['context'],recall['plan'])
    expected=a['normal_expected'](fixture,recipe)
    if decision['reason_codes']!=(['recall_user_fact_dependency'] if expected else ['recall_no_eligible_memory']):raise ValueError('lifecycle original reason differs')
    if (decision['outcome']!=('recall' if expected else 'no_recall') or len(result['items'])!=int(expected)
            or decision['filtered_candidate_count']!=int(expected) or value['candidate_query_count']!=1
            or not value['candidate_query_started'] or decision['candidate_count_stage']!='after_all_eligibility_gates'
            or result['confirmation_groups'] or decision['confirmation_groups']):raise ValueError('original lifecycle inclusion differs')
    replay=recall['replay']
    if not replay['replayed'] or replay['candidate_query_count']!=0 or replay['result']!=result or replay['decision']!=decision:
        raise ValueError('lifecycle exact replay differs')
    refs=[]
    for source in sources:
        event=next(e for e in calls if e['call']=='apply_memory_mutation_plan' and e['plan']['plan_id']==source['receipt']['plan_id'])
        for span in event['plan']['operations'][0]['evidence_spans']:
            refs.append({'evidence_id':span['evidence_id'],'content_hash':span['envelope_hash'],'ordinal':len(refs)+1})
    if recall['context']['evidence_refs']!=refs or recall['plan']['evidence_refs']!=refs:raise ValueError('lifecycle full source refs differ')
    if (recall['context']['query']!=recipe['seed']['payload']['action']
            or recall['plan']['requested_memory_types']!=['prospective'] or recall['context']['subject']!='principal-1'
            or recall['context']['available_memory_types']!=['prospective']
            or recall['plan']['query']!=recipe['seed']['payload']['action']
            or recall['plan']['retrieval_modes']!=['full_text']
            or recall['context']['allowed_retrieval_modes']!=['full_text']
            or recall['plan']['budget']!=dict(max_items=8,max_bytes=16384,max_tokens=2048,deadline_ms=2000)
            or recall['context']['disclosure_context']['recipient']!='user_self'
            or recall['context']['disclosure_context']['purpose']!='personalization'
            or recall['now']!=1788170400.0 or decision['decided_at']!=recall['now']
            or result['evaluated_at']!=recall['now']):raise ValueError('lifecycle actual context differs')
    for item in result['items']:
        selected=item['selected_item'];final=sources[-1]['receipt']['operations'][0]
        if (selected['source_ref']!=final['memory_id'] or selected['source_revision']!=final['revision']
                or selected['source_content_hash']!=a['hash_json'](full) or item['public_payload']!=projection
                or item['evidence_manifest_hash']!=a['hash_json'](sorted(sources[-1]['evidence_ids']))
                or selected['source_kind']!='cognitive_memory' or selected['memory_type']!='prospective'
                or item['effective_privacy_class']!='personal' or item['information_attributes']
                or selected['public_payload_hash']!=a['hash_json'](projection)
                or item['score']!=round(.30/61,12) or item['cross_scope'] or item['source_task_scope_ids']):
            raise ValueError('lifecycle selected source/hash/rank differs')
    if state=='candidate':
        control=o['candidate_control'];entry=control['source'];ev=next(e for e in control['calls']
            if e['call']=='apply_memory_mutation_plan' and e['plan']['plan_id']==entry['receipt']['plan_id'])
        a['check_action_grant']({'calls':control['calls']},ev['plan'])
        control_observed={'calls':control['calls'],'sources':[*sources,entry]}
        a['check_seed_authority'](control_observed,{**recipe,'seed':{**recipe['seed'],'state':'pending'}},full,source_index=1,check_recall_refs=False)
        boundary=control['calls'].index(ev)
        verify_series([*sources,entry],control['calls'][boundary:],'pending','candidate-control-registration',[control['ack']])
        target=sources[0]['receipt']['operations'][0]
        if entry['receipt']['operations'][0]['memory_id']!=target['memory_id']:
            raise ValueError('candidate control returned revision changed target memory identity')
        if (ev['plan']['operations'][0]['target']!={'target_kind':'existing_memory','memory_id':target['memory_id'],'revision':1}
                or entry['input']!={**recipe['seed'],'state':'pending'}):raise ValueError('candidate control changed identity')
        positive=control['recall'];a['check_execution_wire'](positive['execution'],positive['context'],positive['plan'])
        replay=positive['replay']
        if not replay['replayed'] or replay['candidate_query_count']!=0 or replay['result']!=positive['execution']['result']:raise ValueError('candidate control replay differs')
        if (ev['plan']['operations'][0]['lifecycle_state']!='pending'
                or entry['receipt']['operations'][0]['revision']!=2):raise ValueError('candidate control actual pending revision differs')
        pv=positive['execution'];pd=pv['decision'];pr=pv['result'];pc=positive['context'];pp=positive['plan']
        pref=refs+[{'evidence_id':s['evidence_id'],'content_hash':s['envelope_hash'],'ordinal':len(refs)+i+1}
            for i,s in enumerate(ev['plan']['operations'][0]['evidence_spans'])]
        if (pc['evidence_refs']!=pref or pp['evidence_refs']!=pref
                or pc['query']!=recipe['seed']['payload']['action'] or pp['query']!=pc['query']
                or pc['available_memory_types']!=['prospective'] or pp['requested_memory_types']!=['prospective']
                or pc['allowed_retrieval_modes']!=['full_text'] or pp['retrieval_modes']!=['full_text']
                or pc['subject']!='principal-1' or pc['run_id']!=ev['plan']['run_id']
                or pc['budget']!=recall['context']['budget'] or pp['budget']!=pc['budget']
                or pc['disclosure_context']!=recall['context']['disclosure_context']
                or positive['now']!=recall['now'] or pd['decided_at']!=positive['now']
                or pr['evaluated_at']!=positive['now'] or pd['outcome']!='recall'
                or pd['reason_codes']!=['recall_user_fact_dependency'] or pd['filtered_candidate_count']!=1
                or pd['candidate_count_stage']!='after_all_eligibility_gates'
                or pv['candidate_query_count']!=1 or not pv['candidate_query_started']
                or pd['confirmation_groups'] or pr['confirmation_groups']
                or replay['decision']!=pd or replay['candidate_query_started']):
            raise ValueError('candidate positive control request/access/clock differs')
        if len(positive['execution']['result']['items'])!=1 or positive['execution']['result']['items'][0]['selected_item']['source_ref']!=target['memory_id']:
            raise ValueError('candidate independent positive control missing')

        item=pr['items'][0];selected=item['selected_item'];pending=entry['receipt']['operations'][0]
        if (selected['source_revision']!=pending['revision'] or selected['source_kind']!='cognitive_memory'
                or selected['memory_type']!='prospective' or selected['source_content_hash']!=a['hash_json'](full)
                or item['public_payload']!=projection or selected['public_payload_hash']!=a['hash_json'](projection)
                or item['evidence_manifest_hash']!=a['hash_json'](sorted(entry['evidence_ids']))
                or item['effective_privacy_class']!='personal' or item['information_attributes']
                or item['score']!=round(.30/61,12) or item['cross_scope'] or item['source_task_scope_ids']):
            raise ValueError('candidate positive control pending revision/source binding differs')
