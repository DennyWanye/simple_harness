"""Strict executable validator for this bundle's deliberately small Schema subset.
Production reuses the SDK codecs; this is not a general JSON Schema implementation.
"""
import json,math,re
from pathlib import Path
from .runtime_rules import RuleError,parse_strict
ROOT=Path(__file__).resolve().parents[1]
SCHEMA=json.loads((ROOT/'contracts/runtime-plane.schema.json').read_text())
def validate(value,spec):
    if '$ref' in spec:return validate(value,SCHEMA['$defs'][spec['$ref'].split('/')[-1]])
    for keyword in ('oneOf','anyOf'):
        if keyword in spec:
            passed=0
            for sub in spec[keyword]:
                try:validate(value,sub);passed+=1
                except RuleError:pass
            if passed<1 or (keyword=='oneOf' and passed!=1):raise RuleError('UNION_MISMATCH')
            return
    if 'const' in spec and (type(value)!=type(spec['const']) or value!=spec['const']):raise RuleError('CONST')
    if 'enum' in spec and not any(type(value)==type(v) and value==v for v in spec['enum']):raise RuleError('ENUM')
    types=spec.get('type',[]);types=[types] if isinstance(types,str) else types
    pred={'null':lambda:type(value) is type(None),'boolean':lambda:type(value) is bool,'integer':lambda:type(value) is int,'number':lambda:type(value) in (int,float) and math.isfinite(value),'string':lambda:type(value) is str,'array':lambda:type(value) is list,'object':lambda:type(value) is dict}
    if types and not any(pred[t]() for t in types):raise RuleError('TYPE')
    if type(value) is dict:
        p=spec.get('properties',{})
        if not set(spec.get('required',()))<=value.keys():raise RuleError('MISSING_FIELD')
        for k,v in value.items():
            if k in p:validate(v,p[k])
            elif spec.get('additionalProperties',True) is False:raise RuleError('UNKNOWN_FIELD')
            elif isinstance(spec.get('additionalProperties'),dict):validate(v,spec['additionalProperties'])
    elif type(value) is list:
        if len(value)>spec.get('maxItems',10**9) or len(value)<spec.get('minItems',0):raise RuleError('ARRAY_LIMIT')
        if spec.get('uniqueItems') and len({json.dumps(v,sort_keys=True) for v in value})!=len(value):raise RuleError('DUPLICATE_ITEM')
        for v in value:validate(v,spec.get('items',{}))
    elif type(value) is str:
        if len(value)>spec.get('maxLength',10**9) or len(value)<spec.get('minLength',0):raise RuleError('STRING_LIMIT')
        if 'pattern' in spec and not re.search(spec['pattern'],value):raise RuleError('STRING_PATTERN')
    elif type(value) in (int,float):
        if not math.isfinite(value) or value<spec.get('minimum',float('-inf')) or value>spec.get('maximum',float('inf')):raise RuleError('NUMBER_LIMIT')

# Context-free semantic rules. Current authorisation/DB identity are production
# resolver obligations, not simulated by a schema test.
def validate_semantics(name, value):
    from .runtime_rules import canonical,digest,validate_bundle_paths,validate_vector
    def need(ok,code='STATE_COMBINATION_INVALID'):
        if not ok:raise RuleError(code)
    if name in ('SearchPage','ManagementSearchPage','HistoryReadPage','CataloguePage','SkillDetailsPage','ContextManifestPage'):
        need(value['has_more']==(value['next_cursor'] is not None))
    if name in ('SearchPage','ManagementSearchPage'):
        c=value['receipt']['coverage'];validate_semantics('SearchCoverage',c)
        need(value['receipt']['returned_count']==len(value['items']))
        if c['phase']=='SCANNING':
            need(not value['items'] and value['has_more'] and value['page_semantics']=='PROGRESS')
        else:need(c['ranking_final'] and value['page_semantics']=='APPEND_FINAL')
        need(all(x['rank_ordinal']>=1 for x in value['items']))
    if name=='SearchCoverage':
        need(value['scanned_chunks']<=value['snapshot_chunks'])
        need(value['vector_ready_groups']<=value['indexed_groups']<=value['expected_groups'])
        if value['phase']=='SCANNING':need(not value['ranking_final'] and value['rank_scope']=='NONE')
        else:need(value['ranking_final'] and value['rank_scope']=='FROZEN_INDEX_CHANNEL_TOPK' and value['scanned_chunks']==value['snapshot_chunks'])
        if value['index_coverage']=='COMPLETE':need(value['indexed_groups']==value['expected_groups'] and (value['mode']!='HYBRID' or value['vector_ready_groups']==value['expected_groups']))
    if name=='HistoryReadRequest':need(value['seq_from']<=value['seq_to'],'HISTORY_BYTE_RANGE_INVALID')
    if name=='HistorySlice':
        need(0<=value['utf8_start']<=value['utf8_end']<=value['total_utf8_bytes'],'HISTORY_BYTE_RANGE_INVALID')
        raw=value['text'].encode('utf8')
        need(len(raw)==value['utf8_end']-value['utf8_start'],'HISTORY_BYTE_RANGE_INVALID')
        import hashlib
        need(hashlib.sha256(raw).hexdigest()==value['slice_hash'],'REF_IDENTITY_MISMATCH')
        need(value['record_complete']==(value['utf8_start']==0 and value['utf8_end']==value['total_utf8_bytes']))
    if name=='HistoryReadPage':
        need(value['requested_seq_from']<=value['requested_seq_to'])
        for x in value['items']:validate_semantics('HistorySlice',x)
    if name=='TokenReceipt':
        if value['prior_output_reserve_tokens']>0:need(value['prior_basis_ref'] is not None,'PRIOR_RESERVE_UNAVAILABLE')
        need(value['wire_input_tokens']<=value['max_input_budget'],'FINAL_CONTEXT_OVERFLOW')
    if name=='JournalGroup':
        need(value['seq_from']<=value['seq_to'])
        need(len(set(value['record_ids']))==len(value['record_ids']))
        if value['kind']=='CLOSED_TOOL':need(value['closed'] and value['closure_receipt_ref']is not None and len(set(value['call_ids']))==len(value['call_ids']) and len(set(value['result_call_ids']))==len(value['result_call_ids']) and set(value['call_ids'])==set(value['result_call_ids']))
        if value['kind'] in ('OPEN_TAIL','OPAQUE_REQUIRED'):need(value['mandatory'])
    if name=='ProtocolGroupSnapshot':
        for g in value['groups']:validate_semantics('JournalGroup',g)
        gids=[g['group_id']for g in value['groups']]
        need(len(gids)==len(set(gids)))
        need(set(value['mandatory_group_ids'])=={g['group_id']for g in value['groups']if g['mandatory']})
        need(any(g['kind']=='USER_ANCHOR' and g['mandatory'] and g['turn_id']==value['current_turn_id']for g in value['groups']))
    if name=='ContextManifest':
        tr=value['token_receipt'];validate_semantics('TokenReceipt',tr)
        need(value['planned_request_hash']==tr['request_hash'],'REQUEST_HASH_MISMATCH')
        need(value['input_token_charge']==tr['wire_input_tokens'] and value['prior_output_reserve_tokens']==tr['prior_output_reserve_tokens'],'BAD_TOKEN_RECEIPT')
        need(value['reserved_output_tokens']==tr['requested_output_tokens'],'BAD_TOKEN_RECEIPT')
        need(value['recent_complete_turn_count']==len(set(value['selected_turn_ids'])))
    if name in ('CatalogueSummary','CapabilityCatalogueItem','ToolCatalogueItem','SkillCatalogueItem'):
        need(value['kind'].lower()==value['definition_ref']['kind'],'CATALOGUE_KIND_MISMATCH')
        need(len(canonical(value))<=4096,'ITEM_TOO_LARGE')
        if value['access_view']=='MODEL':need('credential_ref_name'not in value and 'endpoint_namespace'not in value)
    if name=='Skill':validate_bundle_paths(x['relative_path']for x in value['files'])
    if name=='SkillDetailsPage':validate_bundle_paths(x['relative_path']for x in value['files'])
    if name=='ContextSettingsView':need(value['view_revision']==value['adoption_revision'])
    if name=='ContextSummaryView':
        if value['context_id']is None:
            need(value['manifest_ref']is None and value['manifest_policy_ref']is None and value['last_count_mode']is None)
    if name=='RuntimeProfile':
        from .runtime_rules import embedding_mode
        embedding_mode(value['context_policy']['embedding_required_for_activation'], value['allow_lexical_degradation'], creating=True, available=True)
        validate_semantics('Policy',value['context_policy'])
    if name=='Policy':
        need(set(value['section_soft_caps'])==set('ABCDE'),'POLICY_INVALID')
        need(value['embedding_overlap_tokens']<value['embedding_chunk_tokens'],'POLICY_INVALID')
    if name=='DependencyLock':
        need(value['complete']==(len(value['unresolved'])==0))
    if name=='CollectionWitness':
        need(value['count']==len(value['refs']))
        need(value['set_hash']==digest(value['refs']),'REF_IDENTITY_MISMATCH')
    if name=='CompleteSessionDisposal':
        from .runtime_rules import complete_disposal
        for c in value['collections']:validate_semantics('CollectionWitness',c)
        actual=complete_disposal(value['collections']);need(value['all_safe']==actual)
    if name=='PreparedRequestDisposition':
        if value['replacement_ordinal']is not None:
            need(value['action']=='CANCEL_UNSENT' and value['no_send_receipt_ref']is not None and value['reservation_settlement_ref']is not None,'REQUEST_UNSENT_TERMINATION_REQUIRED')
        if value['action']=='RECONCILE_ORIGINAL':need(value['replacement_ordinal']is None)
    if name=='EmbeddingCallReceipt':
        need((value['status']=='SUCCEEDED')==(value['output_ref']is not None))
        if value['pricing_mode']=='NO_PROVIDER_CHARGE':need(value['cost_micros']==0)
        if value['status']=='UNKNOWN':need(value['output_ref']is None)
    if name=='HostRequest':
        table=json.loads((ROOT/'contracts/host-verbs.json').read_text());entry=table[value['verb']]
        need((value['payload']is None)!=(value['payload_ref']is None),'HOST_PAYLOAD_MISMATCH')
        if value['payload']is not None:
            validate(value['payload'],SCHEMA['$defs'][entry['request_type']]);validate_semantics(entry['request_type'],value['payload'])
            p=value['payload']
            if value['verb']=='agent_context_settings_update':need(value['expected_revision']==p['expected_adoption_revision'],'EXPECTED_REVISION_MISMATCH')
            if 'cursor'in p:need(value['cursor']==p['cursor'],'CURSOR_REQUEST_MISMATCH')
            if 'limit'in p:need(value['limit']==p['limit'],'CURSOR_REQUEST_MISMATCH')
    if name=='HostResponse':
        entry=json.loads((ROOT/'contracts/host-verbs.json').read_text())[value['request_verb']]
        if value['error']is not None:need(not value['items'] and value['command_receipt']is None)
        else:
            need(len(value['items'])==1,'HOST_PAYLOAD_MISMATCH')
            validate(value['items'][0],SCHEMA['$defs'][entry['response_type']]);validate_semantics(entry['response_type'],value['items'][0])
            need(entry['access']!='WRITE' or value['command_receipt']is not None,'HOST_PAYLOAD_MISMATCH')
            p=value['items'][0]
            if 'next_cursor'in p:need(value['next_cursor']==p['next_cursor'])

def decode(name,raw):
    value=parse_strict(raw,max_bytes=8*1024*1024 if name in ('ContextManifest','ProtocolGroupSnapshot') else 262144)
    validate(value,SCHEMA['$defs'][name]);validate_semantics(name,value);return value
