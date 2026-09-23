"""ASSURANCE-EXEC-1.0 reference semantics, not an SDK producer or authority.
No network, model, local checkout or production DB access is performed here.
Production must feed these algorithms only normalized, source-checked snapshots.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
from collections.abc import Mapping, Sequence

class ContractError(ValueError):
    pass

class StaleProof(ContractError):
    pass

class IncompleteEvidence(ContractError):
    pass

class Grade(StrEnum):
    PASS = 'PASS'
    FAIL = 'FAIL'
    UNKNOWN = 'UNKNOWN'

class Truth(StrEnum):
    TRUE = 'TRUE'
    FALSE = 'FALSE'
    UNKNOWN = 'UNKNOWN'
    CONFLICT = 'CONFLICT'

def strict_json(raw: bytes | str, *, max_bytes: int = 262144, max_depth: int = 64) -> object:
    """Duplicate keys are rejected before they can be folded into a dict."""
    if isinstance(raw, bytes):
        try:
            text = raw.decode('utf-8', errors='strict')
        except UnicodeDecodeError as exc:
            raise ContractError('BAD_UTF8') from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise ContractError('JSON_INPUT_TYPE')
    try:
        if len(text.encode('utf-8', errors='strict')) > max_bytes:
            raise ContractError('BYTE_LIMIT')
    except UnicodeEncodeError as exc:
        raise ContractError('BAD_UNICODE') from exc
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ContractError('DUPLICATE_KEY')
            result[key] = value
        return result
    def reject(_):
        raise ContractError('NON_FINITE')
    def finite(s):
        value = float(s)
        if not math.isfinite(value):
            raise ContractError('NON_FINITE')
        return value
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=reject,
                           parse_float=finite)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ContractError('JSON_INVALID') from exc
    def walk(node, depth):
        if depth > max_depth:
            raise ContractError('DEPTH_LIMIT')
        if isinstance(node, str):
            try:
                node.encode('utf-8', errors='strict')
            except UnicodeEncodeError as exc:
                raise ContractError('BAD_UNICODE') from exc
        elif type(node) is int and abs(node) > 9007199254740991:
            raise ContractError('UNSAFE_INTEGER')
        elif isinstance(node, dict):
            for key, val in node.items():
                walk(key, depth + 1); walk(val, depth + 1)
        elif isinstance(node, list):
            for val in node:
                walk(val, depth + 1)
    walk(value, 0)
    return value

def exact_fields(value: object, required: set[str]) -> dict:
    if type(value) is not dict or set(value) != required:
        raise ContractError('FIELD_SET_MISMATCH')
    return value

def integer(value: object, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= 9007199254740991:
        raise ContractError('INTEGER_REQUIRED')
    return value

def unique_strings(value: object, *, nonempty: bool = False, limit: int = 256) -> tuple[str, ...]:
    if type(value) is not list or len(value) > limit or (nonempty and not value):
        raise ContractError('STRING_SET_INVALID')
    if any(type(v) is not str or not v.strip() for v in value):
        raise ContractError('STRING_SET_INVALID')
    if len(value) != len(set(value)):
        raise ContractError('DUPLICATE_ID')
    return tuple(value)

def digest(value: object) -> str:
    """REFERENCE canonicalization only. SDK must retain its own canonical_json."""
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                     allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()

@dataclass(frozen=True, slots=True)
class Expr:
    op: str
    criterion: str | None = None
    children: tuple['Expr', ...] = ()

def parse_expr(raw: object, catalog: frozenset[str], *, node_limit: int = 512, depth_limit: int = 16) -> Expr:
    count = 0
    def visit(node, depth):
        nonlocal count
        count += 1
        if count > node_limit or depth > depth_limit:
            raise ContractError('FORMULA_BOUND')
        if type(node) is not dict:
            raise ContractError('FORMULA_TYPE')
        if set(node) == {'criterion'}:
            key = node['criterion']
            if type(key) is not str or key not in catalog:
                raise ContractError('UNKNOWN_CRITERION')
            return Expr('criterion', key)
        if set(node) not in ({'all'}, {'any'}):
            raise ContractError('FORMULA_FIELDS')
        op = next(iter(node))
        values = node[op]
        if type(values) is not list or not values:
            raise ContractError('EMPTY_FORMULA')
        return Expr(op, children=tuple(visit(c, depth + 1) for c in values))
    return visit(raw, 0)

def evaluate_expr(expr: Expr, grades: Mapping[str, Grade]) -> tuple[Grade, frozenset[str]]:
    if expr.op == 'criterion':
        grade = grades.get(expr.criterion, Grade.UNKNOWN)
        if not isinstance(grade, Grade):
            raise ContractError('GRADE_TYPE')
        return grade, frozenset({expr.criterion}) if grade is Grade.PASS else frozenset()
    children = [evaluate_expr(c, grades) for c in expr.children]
    if not children:
        raise ContractError('EMPTY_FORMULA')
    if expr.op == 'all':
        if any(g is Grade.FAIL for g, _ in children):
            return Grade.FAIL, frozenset()
        if all(g is Grade.PASS for g, _ in children):
            return Grade.PASS, frozenset().union(*(s for _, s in children))
        return Grade.UNKNOWN, frozenset()
    if expr.op == 'any':
        for grade, witness in children:  # Stable formula-order witness, never model score.
            if grade is Grade.PASS:
                return Grade.PASS, witness
        return (Grade.FAIL if all(g is Grade.FAIL for g, _ in children) else Grade.UNKNOWN), frozenset()
    raise ContractError('FORMULA_OP')

def evaluate_success(expr: Expr, grades: Mapping[str, Grade], mandatory: Sequence[str]) -> tuple[Grade, frozenset[str]]:
    hard = [grades.get(c, Grade.UNKNOWN) for c in mandatory]
    if any(g is Grade.FAIL for g in hard):
        return Grade.FAIL, frozenset()
    if any(g is not Grade.PASS for g in hard):
        return Grade.UNKNOWN, frozenset()
    grade, proof = evaluate_expr(expr, grades)
    return grade, proof | frozenset(mandatory) if grade is Grade.PASS else frozenset()

def parse_review_reply(raw: bytes | str, *, catalog: frozenset[str], visible_evidence: frozenset[str]) -> dict:
    data = exact_fields(strict_json(raw), {'schema_version', 'verdict', 'assessments', 'findings'})
    if integer(data['schema_version']) != 2:
        raise ContractError('REVIEW_VERSION')
    if data['verdict'] not in ('ACCEPT','REWORK','INCONCLUSIVE','REJECTED'):
        raise ContractError('REVIEW_VERDICT')
    if type(data['assessments']) is not list or len(data['assessments']) > 256:
        raise ContractError('ASSESSMENTS_INVALID')
    seen = set()
    for a in data['assessments']:
        exact_fields(a, {'criterion_id','verdict','evidence_ids','reason','limitations'})
        c = a['criterion_id']
        if type(c) is not str or c not in catalog or c in seen:
            raise ContractError('CRITERION_COVERAGE')
        seen.add(c)
        if a['verdict'] not in ('PASS','FAIL','UNKNOWN'):
            raise ContractError('CRITERION_VERDICT')
        refs = unique_strings(a['evidence_ids'], limit=64)
        if not set(refs).issubset(visible_evidence):
            raise ContractError('UNEXPOSED_EVIDENCE')
        if type(a['reason']) is not str or not a['reason'].strip() or len(a['reason']) > 2000:
            raise ContractError('REASON_INVALID')
        unique_strings(a['limitations'], limit=16)
    if seen != catalog:
        raise ContractError('CRITERION_COVERAGE')
    if type(data['findings']) is not list or len(data['findings']) > 128:
        raise ContractError('FINDINGS_INVALID')
    for f in data['findings']:
        exact_fields(f, {'criterion_id','severity','reason'})
        if type(f['criterion_id']) is not str or f['criterion_id'] not in catalog or f['severity'] not in ('BLOCKER','WARNING','INFO'):
            raise ContractError('FINDING_SCOPE')
        if type(f['reason']) is not str or not f['reason'].strip() or len(f['reason']) > 2000:
            raise ContractError('FINDING_REASON')
    return data

def review_can_accept(data, expr, mandatory, policies, results):
    """1.1 requires typed criterion policies and real check result adapters; no bool map."""
    from .protocol_v11 import decide_review
    return decide_review(data, expr, mandatory, policies, results).acceptable

Literal = tuple[str, bool]
@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    premises: tuple[Literal, ...]
    conclusion: Literal
    def __post_init__(self):
        if not self.premises or len(set(self.premises)) != len(self.premises):
            raise ContractError('RULE_PREMISES')
        for key, polarity in (*self.premises, self.conclusion):
            if type(key) is not str or not key or type(polarity) is not bool:
                raise ContractError('LITERAL_TYPE')

@dataclass(frozen=True, slots=True)
class Closure:
    supported: frozenset[Literal]
    clean: frozenset[Literal]
    def truth(self, key: str) -> Truth:
        p, n = (key,True) in self.supported, (key,False) in self.supported
        return Truth.CONFLICT if p and n else Truth.TRUE if p else Truth.FALSE if n else Truth.UNKNOWN
    def usable(self, key: str) -> bool:
        return self.truth(key) is Truth.TRUE and (key, True) in self.clean

def derive(anchors: frozenset[Literal], rules: tuple[Rule,...], *, max_literals: int = 10000,
           max_rules: int = 20000) -> Closure:
    ids = [r.rule_id for r in rules]
    if len(ids) != len(set(ids)):
        raise ContractError('DUPLICATE_RULE_ID')
    universe = set(anchors)
    for r in rules:
        universe.update(r.premises); universe.add(r.conclusion)
    if len(universe) > max_literals or len(rules) > max_rules:
        raise IncompleteEvidence('EVIDENCE_EVALUATION_INCOMPLETE')
    def closure(seed, excluded):
        result = {a for a in seed if a[0] not in excluded}
        while True:
            old = len(result)
            for r in rules:
                if r.conclusion[0] not in excluded and all(p in result for p in r.premises):
                    result.add(r.conclusion)
            if len(result) == old:
                return frozenset(result)
    supported = closure(anchors, set())
    conflicts = {k for k, p in supported if (k, not p) in supported}
    return Closure(supported, closure(anchors, conflicts))

@dataclass(frozen=True, slots=True)
class ReadCertificate:
    mission_id: str
    consumer_id: str
    purpose: str
    scope_id: str
    issued_at_ms: int
    not_after_ms: int | None
    object_versions: tuple[tuple[str,str], ...]
    set_digests: tuple[tuple[str,str], ...]
    complete: bool
    usable: bool
    def verify(self, *, context: tuple[str,str,str,str], now_ms: int,
               objects: Mapping[str,str], sets: Mapping[str,str]) -> None:
        if context != (self.mission_id,self.consumer_id,self.purpose,self.scope_id):
            raise StaleProof('PROOF_CONTEXT_MISMATCH')
        if not self.complete:
            raise IncompleteEvidence('SOURCE_INCOMPLETE')
        if not self.usable:
            raise StaleProof('PROOF_NOT_USABLE')
        if now_ms < self.issued_at_ms:
            raise StaleProof('CLOCK_ROLLBACK_RECHECK')
        if self.not_after_ms is not None and now_ms >= self.not_after_ms:
            raise StaleProof('PROOF_EXPIRED')
        if len(dict(self.object_versions)) != len(self.object_versions) or len(dict(self.set_digests)) != len(self.set_digests):
            raise ContractError('DUPLICATE_READ_PIN')
        if any(objects.get(k) != v for k,v in self.object_versions):
            raise StaleProof('OBJECT_CHANGED')
        if any(sets.get(k) != v for k,v in self.set_digests):
            raise StaleProof('CANDIDATE_SET_CHANGED')

@dataclass(frozen=True, slots=True)
class EffectProof:
    effect_key: str
    spec_hash: str
    outcome_binding_id: str
    operation_id: str
    criterion_ids: frozenset[str]
    milestone: str
    milestone_policy_hash: str
    chain_checked: bool
    current: bool

def effects_ready(required: Mapping[str,tuple[str,str]], spec_hash: str, proofs: tuple[EffectProof,...]) -> bool:
    """Only checks normalized proof claims; production must build each claim from the exact chain."""
    by_key = {}
    used_bindings = set()
    used_operations = set()
    for p in proofs:
        if p.effect_key not in required:
            continue
        if p.effect_key in by_key or p.outcome_binding_id in used_bindings or p.operation_id in used_operations:
            raise ContractError('EFFECT_PROOF_AMBIGUOUS')
        by_key[p.effect_key] = p
        used_bindings.add(p.outcome_binding_id); used_operations.add(p.operation_id)
    for key,(milestone,policy) in required.items():
        p = by_key.get(key)
        if p is None or not p.chain_checked or not p.current or p.spec_hash != spec_hash:
            return False
        if p.milestone != milestone or p.milestone_policy_hash != policy:
            return False
    return True


def review_slot_key(*, mission: str, purpose: str, subject_hash: str,
                    requirements_hash: str, scope_hash: str, round_no: int) -> str:
    """Reference derivation. Transport event/command IDs intentionally not input."""
    integer(round_no, 1)
    values=(mission,purpose,subject_hash,requirements_hash,scope_hash)
    if any(type(x) is not str or not x for x in values):
        raise ContractError('REVIEW_SLOT_IDENTITY')
    return digest({'kind':'assurance-review-slot','mission':mission,'purpose':purpose,
                   'subject_hash':subject_hash,'requirements_hash':requirements_hash,
                   'scope_hash':scope_hash,'round_no':round_no})


def validate_subject_shape(subject: dict) -> None:
    """Shape/combination only. Actual IDs and authority require production readers."""
    exact_fields(subject, {'purpose','target','owner_task_ref','occurrence_id','completion_scope_ref',
                          'method_instance_ref','input_manifest_hash','output_manifest_hash'})
    purpose=subject['purpose']
    allowed={'TASK_CONTENT':{'result'},'METHOD_PLAN':{'method'},'COMPOSITION':{'task'},
             'ACTION_PROPOSAL':{'artifact'},'OPERATION_OUTCOME':{'operation','tool_receipt'},
             'MISSION_FINAL':{'task'}}
    if purpose not in allowed or subject['target']['kind'] not in allowed[purpose]:
        raise ContractError('SUBJECT_BINDING_INVALID')
    if purpose=='METHOD_PLAN':
        if (subject['occurrence_id'] is None)!=(subject['completion_scope_ref'] is None):
            raise ContractError('SUBJECT_BINDING_INVALID')
        if subject['method_instance_ref'] is not None or subject['output_manifest_hash'] is not None:
            raise ContractError('SUBJECT_BINDING_INVALID')
    else:
        if subject['occurrence_id'] is None or subject['completion_scope_ref'] is None:
            raise ContractError('SUBJECT_BINDING_INVALID')
    if purpose in ('TASK_CONTENT','COMPOSITION','MISSION_FINAL') and subject['output_manifest_hash'] is None:
        raise ContractError('SUBJECT_BINDING_INVALID')
    if purpose in ('ACTION_PROPOSAL','OPERATION_OUTCOME') and subject['output_manifest_hash'] is not None:
        raise ContractError('SUBJECT_BINDING_INVALID')
    if purpose=='COMPOSITION' and subject['method_instance_ref'] is None:
        raise ContractError('SUBJECT_BINDING_INVALID')
