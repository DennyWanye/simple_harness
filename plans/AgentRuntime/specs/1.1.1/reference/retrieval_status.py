"""Context-free retrieval truth table. No source/ACL assertions are simulated."""
from .runtime_rules import RuleError

def need(value, code='RETRIEVAL_STATE_INVALID'):
    if not value:
        raise RuleError(code)

def status_for(coverage, query_result_count):
    """Incomplete scan/coverage outranks degradation, which outranks zero hits."""
    if coverage['phase'] == 'SCANNING':
        return 'PARTIAL'
    if coverage['index_coverage'] == 'PARTIAL':
        return 'PARTIAL'
    if coverage['mode'] == 'LEXICAL_ONLY':
        return 'LEXICAL_ONLY'
    return 'COMPLETE_EMPTY' if query_result_count == 0 else 'COMPLETE'

def validate_coverage(c):
    need(0 <= c['scanned_chunks'] <= c['snapshot_chunks'])
    need(0 <= c['vector_ready_groups'] <= c['indexed_groups'] <= c['expected_groups'])
    if c['phase'] == 'SCANNING':
        need(not c['ranking_final'] and c['rank_scope'] == 'NONE')
    else:
        need(c['ranking_final'] and c['rank_scope'] == 'FROZEN_INDEX_CHANNEL_TOPK')
        need(c['scanned_chunks'] == c['snapshot_chunks'])
    if c['index_coverage'] == 'COMPLETE':
        need(c['indexed_groups'] == c['expected_groups'])
        if c['mode'] == 'HYBRID':
            need(c['vector_ready_groups'] == c['expected_groups'])

def validate_receipt(r):
    c=r['coverage'];validate_coverage(c)
    total=r['query_result_count'];count=r['returned_count'];offset=r['page_offset']
    need(r['indexed_closed_groups'] == c['indexed_groups'])
    need(0 <= r['searched_closed_groups'] <= c['indexed_groups'])
    if c['phase'] == 'SCANNING':
        need(total is None and count == 0 and offset == 0 and r['has_more'])
    else:
        need(total is not None and 0 <= offset <= total and offset+count <= total)
        need(r['searched_closed_groups'] == c['indexed_groups'])
        if total == 0:
            need(offset == 0 and count == 0 and not r['has_more'])
        else:
            need(offset < total and count > 0)
            need(r['has_more'] == (offset+count < total))
    need(r['status'] == status_for(c,total))

def validate_page(p):
    r=p['receipt'];validate_receipt(r)
    need(p['has_more'] == r['has_more'] == (p['next_cursor'] is not None))
    need(r['returned_count'] == len(p['items']))
    c=r['coverage']
    if c['phase']=='SCANNING':
        need(not p['items'] and p['page_semantics']=='PROGRESS')
    else:
        need(p['page_semantics']=='APPEND_FINAL')
        need([x['rank_ordinal'] for x in p['items']] == list(range(r['page_offset']+1,r['page_offset']+1+len(p['items']))))
        need(len({x['chunk_id'] for x in p['items']})==len(p['items']))

def validate_aggregate(r):
    from .runtime_rules import digest
    c=r['coverage'];items=r['candidate_items'];total=r['query_result_count']
    need(r['candidate_set_hash']==digest(items),'RECALL_AGGREGATE_MISMATCH')
    need(r['page_chain_hash']==digest(r['page_refs']),'RECALL_AGGREGATE_MISMATCH')
    need(len({(p['kind'],p['id'],p['revision'],p['content_hash'])for p in r['page_refs']})==len(r['page_refs']))
    if r['outcome']=='READY':
        need(r['skip_code'] is None and c is not None and c['phase']=='RESULTS')
        validate_coverage(c)
        need(r['index_snapshot_ref'] is not None and r['search_query_id'] is not None and r['page_refs'])
        need(total==len(items))
        need([x['rank_ordinal'] for x in items]==list(range(1,len(items)+1)))
        need(len({x['chunk_id']for x in items})==len(items))
        need(r['status']==status_for(c,total))
    else:
        need(not items and total is None and r['skip_code'] is not None)
        if r['skip_code'] in ('EMPTY_QUERY','RECALL_DISABLED'):
            need(r['status']=='NOT_REQUESTED' and c is None and not r['page_refs'])
            need(r['index_snapshot_ref'] is None and r['search_query_id'] is None)
            need(r['query_embedding_invocation_ref'] is None and not r['unsettled_call_refs'])
        else:
            need(r['status']=='PARTIAL')
            if c is not None:validate_coverage(c)

def validate_summary(s):
    if s['outcome']=='READY':
        need(s['coverage'] is not None and s['coverage']['phase']=='RESULTS' and s['skip_code']is None)
        validate_coverage(s['coverage'])
        need(s['query_result_count']==s['candidate_count'])
        need(0<=s['selected_count']<=s['candidate_count'])
        need(s['status']==status_for(s['coverage'],s['query_result_count']))
    else:
        need(s['query_result_count']is None and s['candidate_count']==0 and s['selected_count']==0)
        need(s['skip_code']is not None)
        expected='NOT_REQUESTED'if s['skip_code']in('EMPTY_QUERY','RECALL_DISABLED')else'PARTIAL'
        need(s['status']==expected)
        if s['coverage']is not None:validate_coverage(s['coverage'])
