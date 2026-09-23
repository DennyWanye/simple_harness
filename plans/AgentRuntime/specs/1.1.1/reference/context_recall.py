"""Small executable oracle for the composer -> automatic recall seam (R3).

Persisted dictionaries and provided call facts model external ports in these
reference tests. They are NOT production Store/authorization/embedding adapters.
No model, SDK, network, filesystem source or billing system is invoked here.
"""
from dataclasses import asdict,dataclass
import hashlib
from .runtime_rules import RuleError,digest,Budget,Group,Turn,Recall,allocate
from .search_scan import ScoredRow,ScanState,freeze,advance

QUERY_PART_LIMITS={'CURRENT_INPUT':(1536,512),'TASK_GOAL':(1024,0),'LATEST_FEEDBACK':(768,0)}

def build_query(parts):
    """Stable input order; empty text is omitted. Slicing affects search only."""
    unknown=set(parts)-set(QUERY_PART_LIMITS)
    if unknown:raise RuleError('RECALL_BINDING_INVALID')
    chunks=[];coverage=[]
    for kind,(head,tail)in QUERY_PART_LIMITS.items():
        raw=parts.get(kind,'')
        if not isinstance(raw,str):raise RuleError('RECALL_BINDING_INVALID')
        normalized=' '.join(raw.split())
        if not normalized:continue
        n=head+tail
        if len(normalized)<=n:
            selected=normalized;used_head=len(normalized);used_tail=0;truncated=False
        else:
            selected=normalized[:head]+(' … '+normalized[-tail:]if tail else'')
            used_head=head;used_tail=tail;truncated=True
        chunks.append(f'[{kind}]\n{selected}')
        coverage.append({'source_kind':kind,'head_chars':used_head,'tail_chars':used_tail,
                         'text_hash':hashlib.sha256(selected.encode()).hexdigest(),'truncated':truncated})
    query='\n'.join(chunks)
    if len(query)>4096 or len(query.encode())>16384:raise RuleError('RECALL_QUERY_LIMIT')
    return query,tuple(coverage)

@dataclass(frozen=True)
class RecallRow:
    scored:ScoredRow
    group_id:str
    record_seq:int
    token_charge:int

class ReferenceOriginalCallPort:
    """Controlled test double with stable call-key records; no actual paid calls."""
    def __init__(self,records=None,status='SUCCEEDED'):
        self.records={}if records is None else records
        self.default_status=status
    def get_or_submit(self,key):
        if key not in self.records:self.records[key]={'status':self.default_status,'submissions':1}
        return self.records[key]

class ReferenceRecallCoordinator:
    def __init__(self,rows_store,calls):
        self.store=rows_store;self.calls=calls
    def begin(self,key,request,rows):
        rh=digest(request)
        if key in self.store:
            if self.store[key]['request_hash']!=rh:raise RuleError('RECALL_BINDING_INVALID')
            return self.store[key]
        exclusions=set(request['exclusions'])
        frozen=tuple(r for r in rows if r.record_seq<=request['highwater'] and r.group_id not in exclusions)
        if not request['query_text']or request['disabled']:
            status='SKIPPED';reason='EMPTY_QUERY'if not request['query_text']else'RECALL_DISABLED'
        else:status='WAITING_EMBEDDING';reason=None
        state={'request_hash':rh,'request':request,'rows':[asdict(r)for r in frozen],
               'status':status,'reason':reason,'scan':None,'candidates':[],'page_count':0,
               'call_key':key+'/query/'+request['embedding_fingerprint'],'pages':[],'mode':None}
        self.store[key]=state;return state
    @staticmethod
    def _rows(state):
        return tuple(RecallRow(ScoredRow(**dict(x['scored'],channel_scores=tuple(tuple(v)for v in x['scored']['channel_scores']))),x['group_id'],x['record_seq'],x['token_charge'])for x in state['rows'])
    def step(self,key,*,now_ms,current_source_hash):
        state=self.store[key];r=state['request']
        if current_source_hash!=r['source_hash']:
            state['status']='STALE';raise RuleError('RECALL_SOURCE_STALE')
        if state['status']in('READY','SKIPPED'):return state
        if state['status']in('STALE','BLOCKED'):raise RuleError('RECALL_PREPARE_BLOCKED')
        if now_ms>=r['deadline_ms']or state['page_count']>=256:
            if state['status']=='WAITING_EMBEDDING' and not r['allow_lexical_degradation']:
                state.update(status='BLOCKED',reason='EMBEDDING_UNAVAILABLE',candidates=[])
                raise RuleError('EMBEDDING_UNAVAILABLE')
            state.update(status='SKIPPED',reason='INDEX_SCAN_LIMIT',candidates=[]);return state
        if state['status']=='WAITING_EMBEDDING':
            call=self.calls.get_or_submit(state['call_key'])
            if call['status']=='UNKNOWN' and not r['allow_lexical_degradation']:return state
            if call['status']!='SUCCEEDED'and not r['allow_lexical_degradation']:
                state['status']='BLOCKED';raise RuleError('EMBEDDING_UNAVAILABLE')
            state['mode']='HYBRID'if call['status']=='SUCCEEDED'else'LEXICAL_ONLY'
            state['status']='SCANNING'
        rows=self._rows(state)
        scored=tuple(x.scored for x in rows)
        if state['mode']=='LEXICAL_ONLY':
            scored=tuple(ScoredRow(x.rowid,x.chunk_id,tuple((k,v)for k,v in x.channel_scores if k!='vector'),x.record_id,x.source_hash,x.utf8_start,x.utf8_end)for x in scored)
        if state['scan']is None:s=freeze(scored)
        else:
            d=state['scan'];s=ScanState(d['snapshot_hash'],d['row_count'],d['after_offset'],tuple((k,tuple(tuple(x)for x in hits))for k,hits in d['heaps']),d['phase'],tuple(tuple(x)for x in d['results']))
        s=advance(s,scored,page_rows=r['page_rows'],top_k=r['channel_k'],max_candidates=r['global_m'])
        state['scan']=asdict(s);state['page_count']+=1
        state['pages'].append({'ordinal':state['page_count'],'phase':s.phase,'offset':s.after_offset,'hash':digest(asdict(s))})
        if s.phase=='RESULTS':
            by_id={x.scored.chunk_id:x for x in rows}
            state['candidates']=[{'id':cid,'groups':[by_id[cid].group_id],'tokens':by_id[cid].token_charge}for cid,_ in s.results]
            state['status']='READY';state['aggregate_hash']=digest({'request_hash':state['request_hash'],'pages':state['pages'],'candidates':state['candidates']})
        return state

class ReferenceComposer:
    """The test exercises automatic recall through this composer-facing entry."""
    def __init__(self,recalls):self.recalls=recalls
    def prepare(self,key,request,rows,*,now_ms,current_source_hash,budget,fixed,groups,turns,current_turn_id):
        self.recalls.begin(key,request,rows)
        result=self.recalls.step(key,now_ms=now_ms,current_source_hash=current_source_hash)
        if result['status']not in('READY','SKIPPED'):
            return {'state':'PREPARE_PENDING','phase':result['status'],'selection':None}
        candidates=tuple(Recall(x['id'],frozenset(x['groups']),x['tokens'])for x in result['candidates'])
        selection=allocate(budget,fixed,groups,candidates,turns=turns,current_turn_id=current_turn_id)
        return {'state':'CANDIDATE','phase':result['status'],'selection':selection,'recall':result}

def collect_frozen_pages(request, snapshot, pages):
    """Field/identity oracle for R3's aggregate boundary.

    `pages` are exact decoded page bodies returned by the test search service.
    Production additionally resolves each immutable page ref, verifies its actual
    cursor chain/receipt and calls current authorization; this function never
    pretends a dictionary is an authoritative SDK receipt.
    """
    from .retrieval_status import validate_page
    def require(ok):
        if not ok:raise RuleError('RECALL_AGGREGATE_MISMATCH')
    require(bool(pages))
    exclusions=sorted(set(request['mandatory_group_ids'])|set(request['protected_group_ids']))
    items=[];last_scan=-1;seen_results=False;total=None;final_signature=None
    for i,p in enumerate(pages):
        validate_page(p);r=p['receipt'];c=r['coverage']
        require(p['cursor_purpose']=='CONTEXT_RECALL')
        for field in ('session_id','query_hash','journal_highwater','source_snapshot_hash','policy_ref','authority_readset_hash'):
            require(r[field]==request[field])
        require(r['generation']==request['control_generation'])
        require(r['excluded_group_ids']==exclusions)
        require(r['index_snapshot_id']==snapshot['snapshot_id'])
        require(r['index_generation']==snapshot['index_generation'] and r['index_upper_commit']==snapshot['upper_commit'])
        if i<len(pages)-1:require(p['has_more'])
        if c['phase']=='SCANNING':
            require(not seen_results and c['scanned_chunks']>=last_scan)
            last_scan=c['scanned_chunks']
        else:
            signature=(c['index_coverage'],c['mode'],c['snapshot_chunks'],c['expected_groups'],c['indexed_groups'],c['vector_ready_groups'],r['query_result_count'])
            if final_signature is not None:require(final_signature==signature)
            final_signature=signature;seen_results=True;total=r['query_result_count']
            require(r['page_offset']==len(items));items.extend(p['items'])
    require(seen_results and not pages[-1]['has_more'] and len(items)==total)
    require(len({x['chunk_id']for x in items})==len(items))
    return tuple(items)
