"""Executable specification of pure 1.1 rules. No authority producer or SDK implementation.
Production uses the SDK's canonical_json and exact receipt readers, not these fixtures.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections.abc import Mapping, Sequence
import hashlib, json, re
from .contract_constants import INTERNAL_REF_KINDS
from .semantics import ContractError, Grade, Expr, evaluate_success

def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')

def sha(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()

@dataclass(frozen=True)
class CheckResult:
    check_key: str
    execution_state: str
    assertion_grade: Grade
    receipt_ref: str
    source_valid: bool
    def __post_init__(self):
        if self.execution_state not in {'SUCCEEDED','ERROR','CANCELLED','NOT_RUN','RUNNING'}:
            raise ContractError('CHECK_STATE')
        if not isinstance(self.assertion_grade, Grade) or type(self.source_valid) is not bool:
            raise ContractError('CHECK_RESULT_TYPE')
        if not self.check_key or not self.receipt_ref:
            raise ContractError('CHECK_RESULT_ID')
    def effective(self) -> Grade:
        if not self.source_valid or self.execution_state != 'SUCCEEDED':
            return Grade.UNKNOWN
        return self.assertion_grade

@dataclass(frozen=True)
class CriterionCheckPolicy:
    criterion_id: str
    mode: str
    any_check_sets: tuple[tuple[str,...], ...]
    def __post_init__(self):
        if self.mode == 'SEMANTIC':
            if self.any_check_sets: raise ContractError('SEMANTIC_HAS_CHECKS')
        elif self.mode == 'CHECKED':
            if not self.any_check_sets or any(not g or len(set(g))!=len(g) for g in self.any_check_sets):
                raise ContractError('CHECKED_GROUPS_REQUIRED')
        else: raise ContractError('CHECK_POLICY_MODE_UNKNOWN')

def tri_all(grades: Sequence[Grade]) -> Grade:
    if not grades: raise ContractError('EMPTY_CHECK_SET')
    if Grade.FAIL in grades: return Grade.FAIL
    return Grade.PASS if all(g is Grade.PASS for g in grades) else Grade.UNKNOWN

@dataclass(frozen=True)
class CheckGate:
    grade: Grade | None  # None = NOT_APPLICABLE, NEVER an invented PASS receipt
    consumed_receipts: tuple[str,...]
    reason: str

def evaluate_check_gate(policy: CriterionCheckPolicy, results: Mapping[str,CheckResult]) -> CheckGate:
    if policy.mode == 'SEMANTIC': return CheckGate(None, (), 'CHECK_NOT_APPLICABLE')
    evaluated=[]
    for group in policy.any_check_sets:
        rows=[]
        for key in group:
            row=results.get(key)
            if row is not None and (not isinstance(row, CheckResult) or row.check_key!=key):
                raise ContractError('CHECK_RESULT_IDENTITY')
            rows.append(row)
        grade=tri_all([r.effective() if r else Grade.UNKNOWN for r in rows])
        receipts=tuple(sorted({r.receipt_ref for r in rows if r is not None}))
        evaluated.append((grade, receipts))
    for grade, receipts in evaluated:
        if grade is Grade.PASS: return CheckGate(Grade.PASS, receipts, 'CHECK_GROUP_SATISFIED')
    grade=Grade.FAIL if all(g is Grade.FAIL for g,_ in evaluated) else Grade.UNKNOWN
    return CheckGate(grade,tuple(sorted({r for _,rs in evaluated for r in rs})),
                     'CHECK_GROUPS_FAILED' if grade is Grade.FAIL else 'CHECK_EVIDENCE_INCOMPLETE')

@dataclass(frozen=True)
class ReviewDecision:
    acceptable: bool
    effective_grades: Mapping[str,Grade]
    success_witness: frozenset[str]
    consumed_receipts: tuple[str,...]
    reasons: tuple[str,...]

def decide_review(data: dict, expr: Expr, mandatory: tuple[str,...],
                  policies: Mapping[str,CriterionCheckPolicy],
                  results: Mapping[str,CheckResult]) -> ReviewDecision:
    grades={}; gates={}; reasons=[]
    for assessment in data['assessments']:
        c=assessment['criterion_id']; model=Grade(assessment['verdict'])
        if c in grades: raise ContractError('DUPLICATE_CRITERION')
        policy=policies.get(c)
        if not isinstance(policy,CriterionCheckPolicy) or policy.criterion_id!=c:
            raise ContractError('CHECK_POLICY_UNRESOLVED')
        gate=evaluate_check_gate(policy,results);gates[c]=gate
        grades[c]=model if gate.grade is None else tri_all((model,gate.grade))
        reasons.append(c+':'+gate.reason)
    if set(grades)!=set(policies):raise ContractError('POLICY_CATALOGUE_MISMATCH')
    for f in data['findings']:
        if f['criterion_id'] not in grades:raise ContractError('FINDING_SCOPE')
        if f['severity']=='BLOCKER':grades[f['criterion_id']]=Grade.FAIL
    grade, witness=evaluate_success(expr,grades,mandatory)
    ok=data['verdict']=='ACCEPT' and grade is Grade.PASS
    used=witness if ok else grades.keys()
    receipts=tuple(sorted({r for c in used for r in gates[c].consumed_receipts}))
    return ReviewDecision(ok,grades,witness if ok else frozenset(),receipts,tuple(reasons))

def validate_ref(ref: dict) -> None:
    if not isinstance(ref,dict) or set(ref)!={'kind','pin'} or ref['kind'] not in INTERNAL_REF_KINDS:
        raise ContractError('REF_KIND_UNSUPPORTED')
    p=ref['pin']
    if not isinstance(p,dict) or set(p)!={'id','revision','content_hash'}:
        raise ContractError('REF_SHAPE')
    if not isinstance(p['id'],str) or not p['id'] or type(p['revision']) is not int or p['revision']<0:
        raise ContractError('REF_IDENTITY')
    if not isinstance(p['content_hash'],str) or re.fullmatch('[0-9a-f]{64}',p['content_hash']) is None:
        raise ContractError('REF_HASH')

def evidence_label(review_key: str, exact_ref: dict) -> str:
    validate_ref(exact_ref)
    if not review_key:raise ContractError('REVIEW_KEY')
    return 'ev-'+sha({'kind':'assurance-evidence-label-v1','review_key':review_key,'ref':exact_ref})

def build_catalogue(review_key: str, refs: Sequence[dict]) -> tuple[dict,...]:
    entries={}
    for ref in refs:
        label=evidence_label(review_key,ref)
        if label in entries and canonical(entries[label])!=canonical(ref):raise ContractError('LABEL_COLLISION')
        entries[label]=ref
    return tuple({'label':label,'ref':entries[label]} for label in sorted(entries))

def merge_catalogues(review_key: str, batches: Sequence[Sequence[dict]]) -> tuple[dict,...]:
    entries={}
    for batch in batches:
        for entry in batch:
            if set(entry)!={'label','ref'} or entry['label']!=evidence_label(review_key,entry['ref']):
                raise ContractError('CATALOGUE_LABEL_BINDING')
            key=entry['label']
            if key in entries and canonical(entries[key])!=canonical(entry['ref']):raise ContractError('LABEL_COLLISION')
            entries[key]=entry['ref']
    return tuple({'label':k,'ref':entries[k]} for k in sorted(entries))

def resolve_evidence_ids(review_key: str, labels: Sequence[str], catalogue: Sequence[dict],
                         actually_disclosed: frozenset[str]) -> tuple[dict,...]:
    frozen={e['label']:e['ref'] for e in merge_catalogues(review_key,[catalogue])}
    if len(labels)!=len(set(labels)):raise ContractError('DUPLICATE_EVIDENCE_LABEL')
    if any(label not in frozen or label not in actually_disclosed for label in labels):
        raise ContractError('UNEXPOSED_EVIDENCE')
    return tuple(frozen[label] for label in labels)

def canonical_read_set(rows: Sequence[dict]) -> tuple[dict,...]:
    seen=set();out=[]
    for row in rows:
        if set(row)!={'channel','key','fingerprint','coverage'} or row['coverage']!='COMPLETE':
            raise ContractError('READSET_INCOMPLETE')
        if row['channel'] not in {'OBJECT','QUERY_SET','ACCESS','POLICY'}:raise ContractError('READSET_CHANNEL')
        if not isinstance(row['key'],str) or not row['key'] or not isinstance(row['fingerprint'],str) or re.fullmatch('[0-9a-f]{64}',row['fingerprint']) is None:
            raise ContractError('READSET_IDENTITY')
        k=(row['channel'],row['key'])
        if k in seen: raise ContractError('DUPLICATE_READ_KEY')
        seen.add(k);out.append(row)
    return tuple(sorted(out,key=lambda x:(x['channel'],x['key'])))

def determine_lane(creation_lane: str|None, binding_present: bool) -> str:
    if creation_lane not in {'LEGACY','COMPLETION_V1','ASSURANCE_1_1'}:
        raise ContractError('CREATION_CONTRACT_UNRESOLVED')
    if creation_lane=='ASSURANCE_1_1' and not binding_present:raise ContractError('ASSURANCE_PROFILE_UNBOUND')
    return creation_lane

def may_disclose_restored(*, root_incarnation: str, grant: dict|None, ref: dict,
                         now_ms: int, current_authenticated: bool) -> bool:
    if not current_authenticated or grant is None: return False
    return (grant['root_incarnation_id']==root_incarnation
        and grant['mode']=='READ_ONLY_REAUTHORIZED'
        and grant['not_before_ms']<=now_ms<grant['expires_at_ms']
        and any(canonical(r)==canonical(ref) for r in grant['authorized_exact_refs']))


def normalize_check_result(check_key: str, assertion_key: str, receipt: dict,
                           receipt_ref: str, source_valid: bool) -> CheckResult:
    """After exact producer/source/subject verification, normalize actual assertion output.
    ERROR with no assertion is UNKNOWN, never a fabricated execution PASS.
    """
    state=receipt['execution_state']
    if state not in {'SUCCEEDED','ERROR','CANCELLED'}:raise ContractError('CHECK_STATE')
    values={}
    for row in receipt['assertions']:
        if row['assertion_key'] in values:raise ContractError('DUPLICATE_ASSERTION')
        values[row['assertion_key']]=Grade(row['verdict'])
    grade=values.get(assertion_key,Grade.UNKNOWN) if state=='SUCCEEDED' else Grade.UNKNOWN
    return CheckResult(check_key,state,grade,receipt_ref,source_valid)
