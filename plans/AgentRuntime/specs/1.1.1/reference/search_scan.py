"""Frozen rank scan reference. Full stable key is shared by channel and RRF ties.
This is not an SDK storage or authorization adapter.
"""
from dataclasses import dataclass
from .runtime_rules import RuleError,digest,rrf
CHANNELS=('exact','words','trigram','vector')

@dataclass(frozen=True)
class ScoredRow:
    rowid:int
    chunk_id:str
    channel_scores:tuple[tuple[str,float],...]
    record_id:str
    source_hash:str
    utf8_start:int
    utf8_end:int
    @property
    def stable_key(self):
        return (self.record_id,self.source_hash,self.utf8_start,self.utf8_end,self.chunk_id)

@dataclass(frozen=True)
class ScanState:
    snapshot_hash:str
    row_count:int
    after_offset:int
    heaps:tuple[tuple[str,tuple[tuple[str,float],...]],...]
    phase:str
    results:tuple[tuple[str,float],...]

def freeze(rows:tuple[ScoredRow,...])->ScanState:
    import math
    if any(type(r.rowid)is not int or r.rowid<1 for r in rows)or tuple(sorted(r.rowid for r in rows))!=tuple(r.rowid for r in rows):
        raise RuleError('STATE_COMBINATION_INVALID')
    if len({r.rowid for r in rows})!=len(rows)or len({r.chunk_id for r in rows})!=len(rows):raise RuleError('DUPLICATE_ITEM')
    for r in rows:
        if not r.record_id or len(r.source_hash)!=64 or any(c not in '0123456789abcdef'for c in r.source_hash)or type(r.utf8_start)is not int or type(r.utf8_end)is not int or not 0<=r.utf8_start<r.utf8_end:raise RuleError('STATE_COMBINATION_INVALID')
        if len({n for n,_ in r.channel_scores})!=len(r.channel_scores):raise RuleError('DUPLICATE_ITEM')
        if any(n not in CHANNELS or type(v)not in(int,float)or not math.isfinite(v)for n,v in r.channel_scores):raise RuleError('STATE_COMBINATION_INVALID')
    return ScanState(digest([[r.rowid,r.stable_key,r.channel_scores]for r in rows]),len(rows),0,(),'SCANNING',())

def advance(state,rows,*,page_rows:int,top_k:int,max_candidates:int|None=None):
    if type(page_rows)is not int or not 1<=page_rows<=256 or type(top_k)is not int or not 1<=top_k<=512:raise RuleError('ARRAY_LIMIT')
    m=top_k if max_candidates is None else max_candidates
    if type(m)is not int or not 1<=m<=512:raise RuleError('ARRAY_LIMIT')
    expected=freeze(rows)
    if state.snapshot_hash!=expected.snapshot_hash or state.row_count!=expected.row_count:raise RuleError('CURSOR_STALE')
    if state.phase=='RESULTS':return state
    if state.phase!='SCANNING'or not 0<=state.after_offset<=len(rows):raise RuleError('STATE_COMBINATION_INVALID')
    keys={r.chunk_id:r.stable_key for r in rows};heaps={name:list(items)for name,items in state.heaps}
    end=min(len(rows),state.after_offset+page_rows)
    for row in rows[state.after_offset:end]:
        for channel,score in row.channel_scores:heaps.setdefault(channel,[]).append((row.chunk_id,score))
    for channel in heaps:heaps[channel]=sorted(heaps[channel],key=lambda x:(-x[1],keys[x[0]]))[:top_k]
    done=end==len(rows)
    return ScanState(state.snapshot_hash,len(rows),end,tuple((k,tuple(heaps[k]))for k in sorted(heaps)),
                     'RESULTS'if done else'SCANNING',tuple(rrf(heaps,m,stable_keys=keys,channel_limit=top_k))if done else())
