"""局部协议参考：不是 SimpleHarness 生产代码或完整安全检查器。

输入视为已完成身份、权限、来源真实性及版本解析的抽象快照。
不调用模型、网络、数据库、执行器；不提供实际权限或 exactly-once 保证。
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

class Verdict(str, Enum):
    PASS = 'PASS'
    FAIL = 'FAIL'
    UNKNOWN = 'UNKNOWN'

@dataclass(frozen=True)
class Criterion:
    key: str

@dataclass(frozen=True)
class All:
    children: tuple['Expr', ...]
    def __post_init__(self) -> None:
        if not self.children:
            raise ValueError('empty all is not an authorized success condition')

@dataclass(frozen=True)
class AnyOf:
    children: tuple['Expr', ...]
    def __post_init__(self) -> None:
        if not self.children:
            raise ValueError('empty any is not an authorized success condition')

Expr = Criterion | All | AnyOf

def evaluate(expr: Expr, values: Mapping[str, Verdict]) -> Verdict:
    if isinstance(expr, Criterion):
        if expr.key not in values:
            raise ValueError(f'missing criterion: {expr.key}')
        value = values[expr.key]
        if not isinstance(value, Verdict):
            raise TypeError('values must be parsed Verdict objects')
        return value
    results = tuple(evaluate(c, values) for c in expr.children)
    if isinstance(expr, All):
        if Verdict.FAIL in results:
            return Verdict.FAIL
        return Verdict.PASS if all(r is Verdict.PASS for r in results) else Verdict.UNKNOWN
    if isinstance(expr, AnyOf):
        if Verdict.PASS in results:
            return Verdict.PASS
        return Verdict.FAIL if all(r is Verdict.FAIL for r in results) else Verdict.UNKNOWN
    raise TypeError('unsupported expression')

def may_accept(*, expression_result: Verdict, hard_constraints_pass: bool,
               mandatory_checks_pass: bool, semantic_review_required: bool,
               semantic_review_pass: bool, current_bindings: bool,
               critical_obligations_accounted_for: bool) -> bool:
    """纯布尔门；各输入须由真实、受权、范围匹配的证据计算，不能由模型直填。"""
    return (expression_result is Verdict.PASS and hard_constraints_pass
            and mandatory_checks_pass and current_bindings
            and critical_obligations_accounted_for
            and (not semantic_review_required or semantic_review_pass))

# Atom=(命题身份, 正支持=True/负支持=False)。这不是Python任意谓词执行。
Atom = tuple[str, bool]
@dataclass(frozen=True)
class Rule:
    premises: tuple[Atom, ...]
    conclusion: Atom
    def __post_init__(self) -> None:
        if not self.premises:
            raise ValueError('derived rules need premises; observations are explicit anchors')

def grounded_closure(anchors: frozenset[Atom], rules: Sequence[Rule],
                     *, max_atoms: int = 10000) -> frozenset[Atom]:
    """从当次合法anchor重算最小不动点；不能将旧派生TRUE当作新anchor。
    这里只计算存在何种有根的极性支持；实际用途还须检查冲突、权限、时效和assurance。
    """
    reached = set(anchors)
    if len(reached) > max_atoms:
        raise RuntimeError('EVALUATION_INCOMPLETE')
    while True:
        additions = {r.conclusion for r in rules
                     if all(p in reached for p in r.premises)} - reached
        if not additions:
            return frozenset(reached)
        if len(reached) + len(additions) > max_atoms:
            raise RuntimeError('EVALUATION_INCOMPLETE')
        reached.update(additions)

class Truth(str, Enum):
    TRUE = 'TRUE'
    FALSE = 'FALSE'
    UNKNOWN = 'UNKNOWN'
    CONFLICT = 'CONFLICT'

def truth_for(key: str, reached: frozenset[Atom]) -> Truth:
    return {(False, False): Truth.UNKNOWN, (True, False): Truth.TRUE,
            (False, True): Truth.FALSE, (True, True): Truth.CONFLICT}[
                ((key, True) in reached, (key, False) in reached)]

def usable_for_execution(*, truth: Truth, current: bool, readable: bool,
                         authorized: bool, assurance_sufficient: bool,
                         premises_consistent: bool, witness_epoch: int,
                         current_epoch: int, now_ms: int,
                         not_after_ms: int | None) -> bool:
    return (truth is Truth.TRUE and current and readable and authorized
            and assurance_sufficient and premises_consistent
            and witness_epoch == current_epoch
            and (not_after_ms is None or now_ms < not_after_ms))

class Outcome(str, Enum):
    NOT_HANDED_OFF = 'NOT_HANDED_OFF'
    APPLIED = 'APPLIED'
    NOT_APPLIED_FINAL = 'NOT_APPLIED_FINAL'
    PENDING = 'PENDING'
    PARTIAL = 'PARTIAL'
    UNKNOWN = 'UNKNOWN'

class RetryDecision(str, Enum):
    RETURN_RECORDED_RESULT = 'RETURN_RECORDED_RESULT'
    SEND_SAME_OPERATION = 'SEND_SAME_OPERATION'
    RECONCILE = 'RECONCILE'
    CONFLICT = 'CONFLICT'
    DENY_NEW_HANDOFF = 'DENY_NEW_HANDOFF'

@dataclass(frozen=True)
class RetryFacts:
    outcome: Outcome
    same_payload: bool
    current_authorization: bool
    current_target_condition: bool
    budget_available: bool
    native_atomic_dedupe: bool = False
    authoritative_contract: bool = False
    dedupe_until_ms: int | None = None
    old_request_cannot_apply: bool = False
    final_negative_witness: bool = False
    retry_permitted_by_policy: bool = False

def retry_decision(f: RetryFacts, *, now_ms: int) -> RetryDecision:
    """决策模型，不实施网络调用。RETURN不豁免实际结果读取权限。"""
    if not f.same_payload:
        return RetryDecision.CONFLICT
    if f.outcome is Outcome.APPLIED:
        return RetryDecision.RETURN_RECORDED_RESULT
    if not (f.current_authorization and f.current_target_condition and f.budget_available):
        return RetryDecision.DENY_NEW_HANDOFF
    if f.outcome is Outcome.NOT_HANDED_OFF:
        return RetryDecision.SEND_SAME_OPERATION
    if not f.retry_permitted_by_policy:
        return RetryDecision.RECONCILE
    if (f.outcome is Outcome.NOT_APPLIED_FINAL and f.final_negative_witness
            and f.old_request_cannot_apply):
        return RetryDecision.SEND_SAME_OPERATION
    if (f.outcome in {Outcome.PENDING, Outcome.UNKNOWN}
            and f.native_atomic_dedupe and f.authoritative_contract
            and f.dedupe_until_ms is not None and now_ms < f.dedupe_until_ms):
        return RetryDecision.SEND_SAME_OPERATION
    return RetryDecision.RECONCILE
