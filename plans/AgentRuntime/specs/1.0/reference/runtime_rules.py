"""Executable specification oracle, NOT a replacement for SimpleHarness runtime.
Token charges are nonnegative exact adapter charges or certified upper bounds.
Production must additionally count and freeze the complete provider request.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib, json, math, re, sqlite3, unicodedata
from pathlib import PurePosixPath
from typing import Iterable

class RuleError(ValueError):
    pass

def canonical(value: object) -> bytes:
    # Reference JSON only. SDK integration must call the EXISTING canonical_json.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

def parse_strict(raw: bytes, max_bytes: int = 262144) -> object:
    if len(raw)>max_bytes: raise RuleError('DOCUMENT_TOO_LARGE')
    def pairs(items):
        d={}
        for k,v in items:
            if k in d: raise RuleError('DUPLICATE_JSON_KEY')
            d[k]=v
        return d
    def bad(x): raise RuleError('NONFINITE_JSON')
    try: value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=bad)
    except (UnicodeError,json.JSONDecodeError,RecursionError) as e: raise RuleError('INVALID_JSON') from e
    def walk(v,level):
        if level>24:raise RuleError('JSON_TOO_DEEP')
        if isinstance(v,float) and not math.isfinite(v):raise RuleError('NONFINITE_JSON')
        if isinstance(v,dict):
            for x in v.values():walk(x,level+1)
        elif isinstance(v,list):
            for x in v:walk(x,level+1)
    walk(value,0)
    return value

@dataclass(frozen=True)
class Budget:
    configured_total: int
    model_total: int | None
    model_input: int
    model_output: int
    output: int
    safety: int
    headroom: int
    recent_floor: int
    recall_ceiling: int
    def capacity(self) -> int:
        vals=(self.configured_total,self.model_input,self.model_output,self.output,self.safety,self.headroom,self.recent_floor,self.recall_ceiling)
        if any(type(x) is not int or x<0 for x in vals):raise RuleError('INVALID_BUDGET')
        if min(vals[:4])==0 or self.output>self.model_output:raise RuleError('INVALID_OUTPUT_RESERVE')
        if self.model_total is not None and (type(self.model_total) is not int or self.model_total<=0):raise RuleError('INVALID_MODEL_TOTAL')
        caps=[self.configured_total-self.output-self.safety-self.headroom,self.model_input-self.safety-self.headroom]
        if self.model_total is not None:caps.append(self.model_total-self.output-self.safety-self.headroom)
        result=min(caps)
        if result<=0:raise RuleError('NO_INPUT_BUDGET')
        return result

@dataclass(frozen=True)
class Group:
    id: str
    tokens: int
    complete_turns: int=1

@dataclass(frozen=True)
class Recall:
    id: str
    groups: frozenset[str]
    tokens: int

@dataclass(frozen=True)
class Selection:
    recent: tuple[Group,...]
    recalled: tuple[Recall,...]
    used: int
    input_budget: int
    recent_complete_turns: int

def allocate(budget: Budget, fixed: int, groups: tuple[Group,...], candidates: tuple[Recall,...], *, required_tail: int=1) -> Selection:
    cap=budget.capacity()
    if type(fixed) is not int or fixed<0:raise RuleError('INVALID_FIXED')
    if type(required_tail) is not int or required_tail<0 or required_tail>len(groups):raise RuleError('BAD_REQUIRED_TAIL')
    if len({g.id for g in groups})!=len(groups):raise RuleError('DUPLICATE_GROUP')
    if any(type(g.tokens) is not int or g.tokens<0 for g in groups):raise RuleError('BAD_GROUP_SIZE')
    if len({r.id for r in candidates})!=len(candidates):raise RuleError('DUPLICATE_RECALL')
    if any(type(r.tokens) is not int or r.tokens<0 or not r.groups for r in candidates):raise RuleError('BAD_RECALL')
    start=len(groups)-required_tail
    selected=list(groups[start:]); used_recent=sum(g.tokens for g in selected)
    if fixed+used_recent>cap:raise RuleError('REQUIRED_CONTEXT_TOO_LARGE')
    target=max(used_recent,min(budget.recent_floor,cap-fixed))
    # Protect a contiguous suffix inside the recent-floor allocation. No gaps.
    while start>0 and used_recent+groups[start-1].tokens<=target:
        start-=1;selected.insert(0,groups[start]);used_recent+=groups[start].tokens
    protected={g.id for g in selected}
    rcap=max(0,min(budget.recall_ceiling,cap-fixed-used_recent))
    recalled=[];ru=0
    for r in candidates:
        if r.groups & protected:continue
        if ru+r.tokens<=rcap:
            recalled.append(r);ru+=r.tokens
    # Expand recent; remove overlap before the fit check. Never refill recall.
    while start>0:
        g=groups[start-1]
        trial=[r for r in recalled if g.id not in r.groups]
        trial_ru=sum(r.tokens for r in trial)
        if fixed+used_recent+g.tokens+trial_ru>cap:break
        start-=1;selected.insert(0,g);used_recent+=g.tokens
        recalled=trial;ru=trial_ru
    used=fixed+used_recent+sum(r.tokens for r in recalled)
    if used>cap:raise RuleError('INTERNAL_CONTEXT_OVERFLOW')
    return Selection(tuple(selected),tuple(recalled),used,cap,sum(g.complete_turns for g in selected))

def verify_final_count(selection: Selection, actual_or_upper_input_tokens: int, request_bytes: int, max_bytes: int) -> None:
    if type(actual_or_upper_input_tokens) is not int or actual_or_upper_input_tokens<0:raise RuleError('BAD_TOKEN_RECEIPT')
    if request_bytes>max_bytes:raise RuleError('REQUEST_BYTES_LIMIT')
    if actual_or_upper_input_tokens>selection.input_budget:raise RuleError('FINAL_CONTEXT_OVERFLOW')

def retrieval_scope(session: str, generation: int, observed_session: str, observed_generation: int, live_state: str) -> None:
    if session!=observed_session or generation!=observed_generation:raise RuleError('SESSION_IDENTITY_MISMATCH')
    if live_state!='ACTIVE':raise RuleError('SESSION_NOT_READABLE')

def validate_vector(v: Iterable[float], dim: int) -> tuple[float,...]:
    x=tuple(v)
    if len(x)!=dim or dim<1:raise RuleError('EMBEDDING_DIM_MISMATCH')
    if any(type(a) not in (float,int) or not math.isfinite(a) for a in x):raise RuleError('INVALID_EMBEDDING')
    norm=math.sqrt(sum(a*a for a in x))
    if norm==0:raise RuleError('ZERO_EMBEDDING')
    return tuple(float(a)/norm for a in x)

def safe_skill_path(path: str) -> str:
    if not isinstance(path,str) or not path or '\\' in path or '\x00' in path or ':' in path:raise RuleError('SKILL_PATH_INVALID')
    if path!=unicodedata.normalize('NFC',path):raise RuleError('SKILL_PATH_NONCANONICAL')
    parts=path.split('/')
    if any(p in ('','.','..') or p.endswith((' ','.')) for p in parts):raise RuleError('SKILL_PATH_INVALID')
    if PurePosixPath(path).is_absolute():raise RuleError('SKILL_PATH_INVALID')
    reserved={'CON','PRN','AUX','NUL'}|{f'COM{i}' for i in range(1,10)}|{f'LPT{i}' for i in range(1,10)}
    if any(p.split('.')[0].upper() in reserved for p in parts):raise RuleError('SKILL_PATH_RESERVED')
    return path

def validate_bundle_paths(paths: Iterable[str]) -> None:
    seen=set()
    for p in paths:
        key=safe_skill_path(p).casefold()
        if key in seen:raise RuleError('SKILL_PATH_COLLISION')
        seen.add(key)

def effective_permissions(*sets: frozenset[str]) -> frozenset[str]:
    if not sets:raise RuleError('AUTHORITY_SOURCE_MISSING')
    result=sets[0]
    for s in sets[1:]: result=result&s
    return result

def choose_provider(candidates: list[dict], required: frozenset[str]) -> str:
    eligible=[c for c in candidates if c.get('registered') is True and c.get('configured') is True and c.get('healthy') is True and c.get('authorized') is True and c.get('compatible') is True and required<=frozenset(c.get('features',()))]
    if not eligible:raise RuleError('CAPABILITY_UNAVAILABLE')
    return sorted(eligible,key=lambda c:(c['priority'],c['id']))[0]['id']

def tool_route(effect_class: str, exposed: bool, actual_grant: bool) -> str:
    if not exposed:raise RuleError('TOOL_NOT_EXPOSED')
    if not actual_grant:raise RuleError('AUTHORIZATION_REQUIRED')
    if effect_class=='READ_ONLY':return 'ORIGINAL_TOOL_EXECUTOR'
    if effect_class=='SANDBOX_WRITE':return 'ORIGINAL_SANDBOX_EXECUTOR'
    if effect_class=='EXTERNAL_EFFECT':return 'ORIGINAL_OPERATION_INTENT'
    raise RuleError('TOOL_EFFECT_CLASS_UNKNOWN')

def can_purge(*,closed: bool, active_calls: int, unknown_calls: int, pending_imports: int, live_roots: int, active_index_writers: int, read_leases: int) -> bool:
    counts=(active_calls,unknown_calls,pending_imports,live_roots,active_index_writers,read_leases)
    if type(closed) is not bool or any(type(n) is not int or n<0 for n in counts):raise RuleError('INCOMPLETE_DELETE_PROOF')
    return closed and all(n==0 for n in counts)

def sql_statements(ddl: str):
    buf=''
    for ch in ddl:
        buf+=ch
        if ch==';' and sqlite3.complete_statement(buf):
            yield buf;buf=''
    if buf.strip() and not re.fullmatch(r'(?:\s|--[^\n]*(?:\n|$)|/\*.*?\*/)*',buf,re.S):
        raise RuleError('INCOMPLETE_SQL_TAIL')
