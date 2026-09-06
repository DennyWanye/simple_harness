"""Independent rich Episode proof; legacy literal acceptance remains blocked."""
import copy
import importlib.util
from pathlib import Path


def check(fixture, observed):
    spec = importlib.util.spec_from_file_location('rich_base_oracle',Path(__file__).with_name('typed_recall_a2_oracle.py'))
    a = importlib.util.module_from_spec(spec); spec.loader.exec_module(a)
    kind=observed['original']['memory_type']
    if kind not in {'episode','procedure','prospective'}:raise ValueError('unsupported rich source kind')
    original = next(r for r in fixture['minimal_projection_oracle'] if r['memory_type']==kind)
    if observed['original'] != original or a.hash_json(original['payload']) != original['payload_hash']:
        raise ValueError('frozen rich source/literal hash differs')
    rich = original['source_record']
    minimal = {k:copy.deepcopy(rich[k]) for k in original['allowed_payload_fields']}
    actual = a.normal_projection({'memory_type':kind,'payload':minimal})
    full = ({'memory_type':kind,**actual,'thread_ref':None} if kind=='episode' else
        {'memory_type':kind,**{k:v for k,v in actual.items() if k!='effective_risk'},
            'proposed_risk_level':actual['effective_risk']} if kind=='procedure' else {'memory_type':kind,**actual})
    source = observed['sources'][0]
    if len(observed['sources'])!=1 or source['source_wire']!=full:
        raise ValueError('actual typed source differs')
    a.check_seed_authority(observed,{'seed':{'memory_type':kind,'payload':rich}},full,check_recall_refs=False)
    op = source['receipt']['operations'][0]
    expected_mapping = dict(original_source_ref=rich['source_ref'],actual_memory_id=op['memory_id'],
        actual_revision=op['revision'],evidence_id='secret-evidence',source_task_scope_id='rich-source-task')
    if observed['label_mapping']!=expected_mapping or op['revision']!=1:
        raise ValueError('label to actual source binding differs')
    events = [e for e in observed['calls'] if e['call']=='register_procedure_conversation' and e['registration']['registration_id']=='rich-registration']
    if len(events)!=1:
        raise ValueError('missing actual public source registration')
    event=events[0]; reg=event['registration']; metadata=reg['metadata']
    admission=next(e for e in observed['calls'] if e['call']=='ingest_committed_evidence')
    rh=a.sdk_domain_hash('simple-harness/conversation-evidence-registration/v3',reg)
    if (event['registration_hash']!=rh or event['reference']!=event['result']
            or event['reference']!={'registration_id':reg['registration_id'],'registration_hash':rh,
                'evidence_id':'secret-evidence','envelope_hash':admission['envelope_hash']}
            or reg['evidence_id']!='secret-evidence' or reg['envelope_hash']!=admission['envelope_hash']
            or metadata['evidence_id']!='secret-evidence' or metadata['task_scope_id']!='rich-source-task'
            or metadata['subject']!='principal-1' or metadata['run_id']!=admission['envelope']['run_id']
            or metadata['role']!='user' or metadata['tool_causal_link'] is not None
            or reg['metadata_receipt']['metadata_hash']!=a.sdk_domain_hash('simple-harness/conversation-evidence-metadata/v3',metadata)):
        raise ValueError('registered scope/source authority differs')
    revision=1
    scopes=['rich-source-task']
    expected_refs=[{'evidence_id':'secret-evidence','content_hash':admission['envelope_hash'],'ordinal':1}]
    recipe={'family':'lifecycle','seed':{'memory_type':kind,'state':'pending' if kind=='prospective' else 'active','payload':minimal}}
    if kind in {'procedure','prospective'}:
        name='typed_recall_'+kind+'_oracle'
        ps=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'))
        proof=importlib.util.module_from_spec(ps);ps.loader.exec_module(proof)
        if kind=='procedure':
            view={**observed,'calls':[e for e in observed['calls'] if e is not events[0]]}
            revision,ref=proof.check(view,recipe,a.check_admitted_span)
            expected_refs.append({**ref,'ordinal':2})
            scopes=['procedure-fixture-task','rich-source-task']
        else:
            revision=proof.check(observed,recipe)
    recall=observed['recalls'][0]
    for value in [recall,observed['fresh']]:
        if value['context']['evidence_refs']!=expected_refs or value['plan']['evidence_refs']!=expected_refs:
            raise ValueError('rich complete recall source refs differ')
        execution=value['execution'];items=execution['result']['items']
        if execution['decision']['outcome']!='recall' or execution['candidate_query_count']!=1 or len(items)!=1:
            raise ValueError('actual public recall did not select rich source')
        item=items[0]; selected=item['selected_item']
        if item['public_payload']!=actual:
            raise ValueError('rich source field leaked or public projection differs')
        a.check_execution_wire(value['execution'],value['context'],value['plan'])
        if (selected['source_ref']!=op['memory_id'] or selected['source_revision']!=revision
                or selected['source_content_hash']!=a.hash_json(full)
                or selected['public_payload_hash']!=a.hash_json(actual)
                or item['effective_privacy_class']!='sensitive' or item['source_task_scope_ids']!=scopes
                or item['cross_scope'] is not True or item['evidence_manifest_hash']!=a.hash_json(sorted(r['evidence_id'] for r in expected_refs))
                or value['context']['active_task_scope_id'] is not None):
            raise ValueError('actual classification/scope/source binding differs')
    fresh=observed['fresh']
    if (fresh['context']['evidence_refs']!=recall['context']['evidence_refs']
            or fresh['plan']['evidence_refs']!=recall['plan']['evidence_refs']
            or fresh['execution']['replayed'] is not False):
        raise ValueError('fresh actual source refs/replay status differs')
    replay=observed['replay']['execution'];first=recall['execution']
    if replay['result']!=first['result'] or replay['decision']!=first['decision'] or not replay['replayed'] or replay['candidate_query_count']!=0 or replay['candidate_query_started']:
        raise ValueError('reopen exact replay differs')
    if observed['actual_public_payload']!=actual or observed['actual_public_payload_hash']!=a.hash_json(actual):
        raise ValueError('separate actual payload report differs')
    if observed['legacy_status']!='BLOCKED' or observed['legacy_reason']!='ORIGINAL_LITERAL_WIRE_DIFFERS' or actual==original['payload']:
        raise ValueError('legacy literal incorrectly promoted')
    return {'rich_setup':'PASS','legacy_status':'BLOCKED'}
