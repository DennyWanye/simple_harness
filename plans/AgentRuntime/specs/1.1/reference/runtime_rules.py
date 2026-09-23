"""ARP 1.1 executable specification rules. Not an installed SDK implementation.
No model, network, product database, or production authorisation is created here.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib,json,math,re,sqlite3,struct,unicodedata
from pathlib import PurePosixPath
from typing import Iterable

class RuleError(ValueError):
    """Stable code in args[0]; callers must not expose arbitrary exception text."""

def canonical(value: object)->bytes:
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
def digest(value: object)->str:return hashlib.sha256(canonical(value)).hexdigest()
def parse_strict(raw:bytes,max_bytes:int=262144)->object:
    if len(raw)>max_bytes:raise RuleError('DOCUMENT_TOO_LARGE')
    def pairs(items):
        out={}
        for k,v in items:
            if k in out:raise RuleError('DUPLICATE_JSON_KEY')
            out[k]=v
        return out
    def bad(_):raise RuleError('NONFINITE_JSON')
    try:v=json.loads(raw.decode('utf8'),object_pairs_hook=pairs,parse_constant=bad)
    except (UnicodeError,json.JSONDecodeError,RecursionError) as e:raise RuleError('INVALID_JSON') from e
    def walk(x,n):
        if n>24:raise RuleError('JSON_TOO_DEEP')
        if type(x)is float and not math.isfinite(x):raise RuleError('NONFINITE_JSON')
        if type(x)is dict:
            for a in x.values():walk(a,n+1)
        elif type(x)is list:
            for a in x:walk(a,n+1)
    walk(v,0);return v

def nn(x:object,code='INVALID_BUDGET')->int:
    if type(x)is not int or x<0:raise RuleError(code)
    return x

@dataclass(frozen=True)
class Budget:
    configured_total:int
    model_total:int|None
    model_input:int
    model_output:int
    output:int
    safety:int
    headroom:int
    recent_floor:int
    recall_ceiling:int
    prior:int=0
    input_scope:str='WIRE_ONLY'
    def capacity(self)->int:
        for x in (self.configured_total,self.model_input,self.model_output,self.output,self.safety,self.headroom,self.recent_floor,self.recall_ceiling,self.prior):nn(x)
        if min(self.configured_total,self.model_input,self.model_output,self.output)<=0 or self.output>self.model_output:raise RuleError('INVALID_OUTPUT_RESERVE')
        if self.input_scope not in ('WIRE_ONLY','WIRE_PLUS_PRIOR'):raise RuleError('INVALID_BUDGET')
        caps=[self.configured_total-self.prior-self.output-self.safety-self.headroom,
              self.model_input-self.safety-self.headroom-(self.prior if self.input_scope=='WIRE_PLUS_PRIOR' else 0)]
        if self.model_total is not None:
            if nn(self.model_total)==0:raise RuleError('INVALID_MODEL_TOTAL')
            caps.append(self.model_total-self.prior-self.output-self.safety-self.headroom)
        c=min(caps)
        if c<=0:raise RuleError('NO_INPUT_BUDGET')
        return c

@dataclass(frozen=True)
class Group:
    id:str
    turn_id:str
    seq:int
    tokens:int
    kind:str='CLOSED_TOOL'
    mandatory:bool=False
    closed:bool=True
    call_ids:tuple[str,...]=()
    result_call_ids:tuple[str,...]=()

@dataclass(frozen=True)
class Turn:
    id:str
    completed:bool
    required_groups:frozenset[str]

@dataclass(frozen=True)
class Recall:
    id:str
    groups:frozenset[str]
    tokens:int

@dataclass(frozen=True)
class Selection:
    recent:tuple[Group,...]
    recalled:tuple[Recall,...]
    used:int
    input_budget:int
    complete_turn_ids:tuple[str,...]
    count_complete:bool
    @property
    def recent_complete_turns(self):return len(self.complete_turn_ids)

def validate_groups(groups:tuple[Group,...])->None:
    if len({g.id for g in groups})!=len(groups):raise RuleError('DUPLICATE_GROUP')
    if len({g.seq for g in groups})!=len(groups):raise RuleError('STRUCTURE_INVALID')
    for g in groups:
        nn(g.tokens,'BAD_GROUP_SIZE')
        if g.kind not in ('USER_ANCHOR','CLOSED_TOOL','OPEN_TAIL','TERMINAL_ANSWER','OPAQUE_REQUIRED','HISTORY_MESSAGE'):raise RuleError('ENUM')
        if g.kind in ('OPEN_TAIL','OPAQUE_REQUIRED') and not g.mandatory:raise RuleError('STATE_COMBINATION_INVALID')
        if g.kind=='CLOSED_TOOL':
            if not g.closed or len(set(g.call_ids))!=len(g.call_ids) or len(set(g.result_call_ids))!=len(g.result_call_ids) or set(g.call_ids)!=set(g.result_call_ids):raise RuleError('STATE_COMBINATION_INVALID')

def allocate(budget:Budget,fixed:int,groups:tuple[Group,...],candidates:tuple[Recall,...],*,turns:tuple[Turn,...],current_turn_id:str,enumeration_complete:bool=True)->Selection:
    cap=budget.capacity();nn(fixed,'INVALID_FIXED');validate_groups(groups)
    if len({r.id for r in candidates})!=len(candidates):raise RuleError('DUPLICATE_RECALL')
    for r in candidates:
        nn(r.tokens,'BAD_RECALL')
        if not r.groups:raise RuleError('BAD_RECALL')
    ordered=tuple(sorted(groups,key=lambda g:g.seq))
    mandatory=[g for g in ordered if g.mandatory]
    if not any(g.turn_id==current_turn_id and g.kind=='USER_ANCHOR' and g.mandatory for g in ordered):raise RuleError('STATE_COMBINATION_INVALID')
    optional=[g for g in ordered if not g.mandatory]
    recent=list(mandatory);used=sum(g.tokens for g in recent)
    if fixed+used>cap:raise RuleError('REQUIRED_CONTEXT_TOO_LARGE')
    start=len(optional); floor=max(used,min(budget.recent_floor,cap-fixed))
    while start>0 and used+optional[start-1].tokens<=floor:
        start-=1;recent.append(optional[start]);used+=optional[start].tokens
    ids={g.id for g in recent};rmax=min(budget.recall_ceiling,max(0,cap-fixed-used));rec=[];ru=0
    for r in candidates:
        if r.groups&ids:continue
        if ru+r.tokens<=rmax:rec.append(r);ru+=r.tokens
    while start>0:
        g=optional[start-1];trial=[r for r in rec if g.id not in r.groups];t=sum(r.tokens for r in trial)
        if fixed+used+g.tokens+t>cap:break
        start-=1;recent.append(g);used+=g.tokens;rec=trial
    ids={g.id for g in recent}
    complete=tuple(sorted(t.id for t in turns if t.completed and t.id!=current_turn_id and t.required_groups and t.required_groups<=ids))
    total=fixed+used+sum(r.tokens for r in rec)
    if total>cap:raise RuleError('INTERNAL_CONTEXT_OVERFLOW')
    return Selection(tuple(sorted(recent,key=lambda g:g.seq)),tuple(rec),total,cap,complete,enumeration_complete)

def verify_final_count(selection:Selection,count:int,request_bytes:int,max_bytes:int)->None:
    nn(count,'BAD_TOKEN_RECEIPT')
    if request_bytes>max_bytes:raise RuleError('REQUEST_BYTES_LIMIT')
    if count>selection.input_budget:raise RuleError('FINAL_CONTEXT_OVERFLOW')

def validate_vector(v:Iterable[float],dim:int)->tuple[float,...]:
    x=tuple(v)
    if type(dim)is not int or dim<1 or len(x)!=dim:raise RuleError('EMBEDDING_DIM_MISMATCH')
    if any(type(a)not in (float,int) or not math.isfinite(a) for a in x):raise RuleError('INVALID_EMBEDDING')
    scale=max(abs(a) for a in x)
    if scale==0:raise RuleError('ZERO_EMBEDDING')
    scaled=[a/scale for a in x];norm=math.sqrt(math.fsum(a*a for a in scaled))
    raw=struct.pack('<'+str(dim)+'f',*(a/norm for a in scaled))
    out=struct.unpack('<'+str(dim)+'f',raw)
    if not all(math.isfinite(a) for a in out) or not any(a!=0 for a in out):raise RuleError('INVALID_EMBEDDING')
    return out

def utf8_slice(text:str,start:int,max_bytes:int)->tuple[str,int,bool]:
    raw=text.encode('utf8')
    if type(start)is not int or not 0<=start<=len(raw) or type(max_bytes)is not int or max_bytes<4:raise RuleError('HISTORY_BYTE_RANGE_INVALID')
    try:raw[:start].decode('utf8')
    except UnicodeError as e:raise RuleError('HISTORY_BYTE_RANGE_INVALID')from e
    end=min(start+max_bytes,len(raw))
    while end>start:
        try:s=raw[start:end].decode('utf8');return s,end,end==len(raw)
        except UnicodeError:end-=1
    raise RuleError('HISTORY_BYTE_RANGE_INVALID')

def rrf(channels:dict[str,list[tuple[str,float]]],max_candidates:int=128)->list[tuple[str,float]]:
    weights={'exact':1.0,'words':0.6,'trigram':0.5,'vector':0.7};scores={}
    for name,hits in channels.items():
        if name not in weights:raise RuleError('ENUM')
        seen=set()
        for rank,(key,_) in enumerate(sorted(hits,key=lambda x:(-x[1],x[0]))[:max_candidates],1):
            if key in seen:raise RuleError('DUPLICATE_ITEM')
            seen.add(key);scores[key]=scores.get(key,0)+weights[name]/(60+rank)
    return sorted(scores.items(),key=lambda kv:(-kv[1],kv[0]))[:max_candidates]

def check_cursor(stored:dict,current:dict)->None:
    for key in ('owner','session','control_generation','purpose','query_hash','request_hash'):
        if stored[key]!=current[key]:raise RuleError('CURSOR_SCOPE_MISMATCH' if key in ('owner','session','purpose')else'CURSOR_REQUEST_MISMATCH')
    if current['now_ms']>=stored['expires_at_ms']:raise RuleError('CURSOR_EXPIRED')
    for key in ('index_generation','authority_hash','root_incarnation'):
        if stored[key]!=current[key]:raise RuleError('CURSOR_STALE')
    # New append commit is intentionally not compared; frozen upper_commit is stable.

def disposition(phase:str,*,safety_changed:bool=False,settings_changed:bool=False,no_send_final:bool=False)->str:
    if phase in ('PREPARED','RESERVED','WAITING_FOR_SLOT'):
        if safety_changed:return 'CANCEL_UNSENT' if no_send_final else 'REQUEST_UNSENT_TERMINATION_REQUIRED'
        return 'KEEP_FROZEN'
    if phase in ('HANDED_OFF','UNKNOWN'):return 'RECONCILE_ORIGINAL'
    if phase in ('SUCCEEDED','FAILED'):return 'COLLECT_ORIGINAL'
    raise RuleError('REQUEST_PHASE_UNMAPPED')

def embedding_mode(required:bool,degrade:bool,*,creating:bool,available:bool)->str:
    if type(required)is not bool or type(degrade)is not bool or not(required or degrade):raise RuleError('STATE_COMBINATION_INVALID')
    if available:return 'HYBRID'
    if creating and required:return 'REJECT_CREATION'
    return 'LEXICAL_ONLY' if degrade else 'UNAVAILABLE'

def safe_skill_path(path:str)->str:
    if not isinstance(path,str)or not path or '\\'in path or '\x00'in path or ':'in path:raise RuleError('SKILL_PATH_INVALID')
    if path!=unicodedata.normalize('NFC',path):raise RuleError('SKILL_PATH_NONCANONICAL')
    parts=path.split('/')
    if any(p in ('','.','..')or p.endswith((' ','.'))for p in parts)or PurePosixPath(path).is_absolute():raise RuleError('SKILL_PATH_INVALID')
    if len(path.encode())>1024 or any(len(p.encode())>255 for p in parts):raise RuleError('SKILL_PATH_INVALID')
    reserved={'CON','PRN','AUX','NUL'}|{f'COM{i}'for i in range(1,10)}|{f'LPT{i}'for i in range(1,10)}
    if any(p.split('.')[0].upper()in reserved for p in parts):raise RuleError('SKILL_PATH_RESERVED')
    return path

def validate_bundle_paths(paths:Iterable[str])->None:
    seen=set()
    for p in paths:
        k=safe_skill_path(p).casefold()
        if k in seen:raise RuleError('SKILL_PATH_COLLISION')
        seen.add(k)

def effective_permissions(*sets:frozenset[str])->frozenset[str]:
    if not sets:raise RuleError('AUTHORITY_SOURCE_MISSING')
    out=sets[0]
    for s in sets[1:]:out=out&s
    return out

def choose_provider(candidates:list[dict],required:frozenset[str])->str:
    necessary=('registered','configured','healthy','authorized','compatible')
    eligible=[c for c in candidates if all(c.get(k)is True for k in necessary)and required<=frozenset(c.get('features',()))]
    if not eligible:raise RuleError('CAPABILITY_UNAVAILABLE')
    for c in eligible:
        if type(c.get('priority'))is not int:raise RuleError('TYPE')
    return sorted(eligible,key=lambda c:(-c['priority'],c['id']))[0]['id']

def tool_route(effect_class:str,exposed:bool,actual_grant:bool)->str:
    if not exposed:raise RuleError('TOOL_NOT_EXPOSED')
    if not actual_grant:raise RuleError('AUTHORIZATION_REQUIRED')
    routes={'READ_ONLY':'ORIGINAL_TOOL_EXECUTOR','SANDBOX_WRITE':'ORIGINAL_SANDBOX_EXECUTOR','EXTERNAL_EFFECT':'ORIGINAL_OPERATION_INTENT'}
    if effect_class not in routes:raise RuleError('EFFECT_CLASS_UNKNOWN')
    return routes[effect_class]

def dependency_lock(nodes:dict[str,tuple[int,str]],edges:tuple[tuple[str,str],...],max_depth=16)->None:
    if len(nodes)>128:raise RuleError('ARRAY_LIMIT')
    adj={k:[]for k in nodes}
    for a,b in edges:
        if a not in nodes or b not in nodes:raise RuleError('DEPENDENCY_UNRESOLVED')
        adj[a].append(b)
    def walk(n,path):
        if n in path:raise RuleError('DEPENDENCY_CYCLE')
        if len(path)>=max_depth:raise RuleError('ARRAY_LIMIT')
        for child in adj[n]:walk(child,path+(n,))
    for n in nodes:walk(n,())

def complete_disposal(collections:list[dict])->bool:
    required={'TURNS','CALLS','UNKNOWN','PENDING_IMPORTS','INDEX_WRITERS','READ_HANDLES','TEMP_ROOTS'}
    if len(collections)!=7 or {c['kind']for c in collections}!=required:raise RuleError('RETENTION_PROOF_INCOMPLETE')
    if any(c['complete']is not True or c['count']!=len(c['refs'])or c['set_hash']!=digest(c['refs'])for c in collections):raise RuleError('RETENTION_PROOF_INCOMPLETE')
    return all(c['count']==0 for c in collections)

def sql_statements(ddl:str,allow_unterminated_final:bool=False):
    buf=''
    for ch in ddl:
        buf+=ch
        if ch==';'and sqlite3.complete_statement(buf):yield buf;buf=''
    if buf.strip()and not re.fullmatch(r'(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*',buf,re.S):
        if allow_unterminated_final and sqlite3.complete_statement(buf+';'):yield buf+';'
        else:raise RuleError('SQL_INCOMPLETE')
