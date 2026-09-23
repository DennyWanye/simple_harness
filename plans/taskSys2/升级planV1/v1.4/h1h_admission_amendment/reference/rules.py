"""Executable reference rules for H1H-ADM-1.0; not a production SDK patch.

The adapters which read the real SDK stores are specified in the Markdown.
These functions deliberately do not invent authentication, ledger joins, or plans.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Iterable


class SourceUnavailable(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class Denied(RuntimeError):
    def __init__(self, code: str, reason: str):
        self.code, self.reason = code, reason
        super().__init__(f'{code}: {reason}')


def digest(value: object) -> str:
    # Reference-only serialization. Production must use its existing canonical_json.
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def text(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('nonblank string required')


def number(value: int, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= 9_007_199_254_740_991:
        raise ValueError('bounded integer required')


REPORTS = frozenset({'WAIT', 'NO_CHANGE', 'DECLARE_BLOCKED'})
MUTATIONS = frozenset({'REFINE', 'REPAIR/REPLACE_METHOD'})
ENABLED = REPORTS | MUTATIONS


def check_enabled(kind: str) -> None:
    if kind not in ENABLED:
        raise Denied('DECISION_NOT_ENABLED_IN_PHASE', 'decode_only_or_disabled')


@dataclass(frozen=True)
class Grant:
    grant_id: str
    mission_id: str
    tenant_id: str
    scope_id: str
    grantee_id: str
    revision: int
    active: bool
    allowed: frozenset[str]
    issuer_receipt_id: str
    not_before_ms: int
    expires_at_ms: int
    policy_hash: str

    def __post_init__(self) -> None:
        for name in ('grant_id','mission_id','tenant_id','scope_id','grantee_id',
                     'issuer_receipt_id','policy_hash'):
            text(getattr(self, name))
        number(self.revision, 1)
        number(self.not_before_ms)
        number(self.expires_at_ms)
        if type(self.active) is not bool or self.expires_at_ms <= self.not_before_ms:
            raise ValueError('invalid grant')
        if not isinstance(self.allowed, frozenset) or not self.allowed <= ENABLED:
            raise ValueError('invalid planning-lane capabilities')

    def immutable_hash(self) -> str:
        return digest({**self.__dict__, 'allowed': sorted(self.allowed)})


@dataclass(frozen=True)
class AuthorityBinding:
    request_id: str
    mission_id: str
    tenant_id: str
    scope_id: str
    planner_principal_id: str
    grant_id: str
    grant_revision: int
    grant_hash: str


@dataclass(frozen=True)
class AuthorityProof:
    request_id: str
    grant_id: str
    revision: int
    content_hash: str
    checked_at_ms: int
    not_after_ms: int


def authorize(binding: AuthorityBinding, current: Grant | None, *,
              expected_policy_hash: str, mission_active: bool,
              action: str, now_ms: int) -> AuthorityProof:
    number(now_ms)
    check_enabled(action)
    if current is None:
        raise Denied('AUTHORIZATION_REQUIRED', 'planning_grant_missing')
    expected = (binding.mission_id, binding.tenant_id, binding.scope_id,
                binding.planner_principal_id, binding.grant_id)
    actual = (current.mission_id, current.tenant_id, current.scope_id,
              current.grantee_id, current.grant_id)
    if actual != expected:
        raise Denied('AUTHORIZATION_REQUIRED', 'planning_grant_identity_mismatch')
    if (current.revision != binding.grant_revision or
            current.immutable_hash() != binding.grant_hash):
        raise Denied('REQUEST_BINDING_STALE', 'planning_grant_changed')
    if not current.active or not mission_active:
        raise Denied('AUTHORIZATION_REQUIRED', 'planning_grant_or_mission_inactive')
    if not current.not_before_ms <= now_ms < current.expires_at_ms:
        raise Denied('AUTHORIZATION_REQUIRED', 'planning_grant_expired_or_not_started')
    if current.policy_hash != expected_policy_hash:
        raise Denied('REQUEST_BINDING_STALE', 'planning_policy_changed')
    if action not in current.allowed:
        raise Denied('AUTHORIZATION_REQUIRED', 'planning_action_not_delegated')
    return AuthorityProof(binding.request_id, current.grant_id, current.revision,
                          current.immutable_hash(), now_ms, current.expires_at_ms)


class Effect(str, Enum):
    NOT_HANDED_OFF = 'NOT_HANDED_OFF'
    IN_FLIGHT = 'IN_FLIGHT'
    APPLIED = 'APPLIED'
    CONFIRMED_NOT_APPLIED = 'CONFIRMED_NOT_APPLIED'
    UNRESOLVED = 'UNRESOLVED'


@dataclass(frozen=True)
class BindingRow:
    mission_id: str
    operation_id: str
    occurrence_id: str  # operation occurrence, never an HTN occurrence
    request_hash: str
    envelope_hash: str


@dataclass(frozen=True)
class LinkRow:
    mission_id: str
    operation_id: str
    occurrence_id: str
    request_hash: str
    envelope_hash: str
    action_key: str
    action_id: str
    action_version: int
    params_hash: str
    idempotency_key: str


@dataclass(frozen=True)
class ActionRow:
    mission_id: str
    action_key: str
    action_id: str
    version: int
    params_hash: str
    idempotency_key: str | None
    state: str
    handoffs: int
    matching_success_receipt: bool
    authoritative_not_applied: bool
    all_handoffs_covered: bool
    no_late_apply_proven: bool
    reconciliation_label: str = ''  # intentionally insufficient to authorize anything

    def __post_init__(self) -> None:
        number(self.version, 1)
        number(self.handoffs)
        for name in ('matching_success_receipt','authoritative_not_applied',
                     'all_handoffs_covered','no_late_apply_proven'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('boolean receipt facts required')


OPEN = frozenset({'PROPOSED', 'AWAITING_APPROVAL', 'APPROVED'})
CLOSED = frozenset({'FAILED','REJECTED','REVOKED','EXPIRED','SUPERSEDED','CANCELLED','REFUSED'})


def classify_effect(row: ActionRow) -> Effect:
    if row.state not in OPEN | CLOSED | {'HANDED_OFF', 'UNKNOWN', 'SUCCEEDED'}:
        raise SourceUnavailable('unrecognized_action_state')
    if row.state == 'SUCCEEDED':
        if row.handoffs >= 1 and row.matching_success_receipt:
            return Effect.APPLIED
        raise SourceUnavailable('success_without_matching_receipt')
    if row.state == 'HANDED_OFF':
        return Effect.IN_FLIGHT
    if row.state in OPEN and row.handoffs == 0:
        return Effect.NOT_HANDED_OFF
    if row.state in CLOSED and row.handoffs == 0:
        return Effect.CONFIRMED_NOT_APPLIED
    if (row.authoritative_not_applied and row.all_handoffs_covered and
            row.no_late_apply_proven):
        return Effect.CONFIRMED_NOT_APPLIED
    return Effect.UNRESOLVED


@dataclass(frozen=True)
class OperationProof:
    mission_id: str
    read_digest: str
    effects: tuple[tuple[str, Effect], ...]

    @property
    def unresolved(self) -> tuple[str, ...]:
        return tuple(op for op, state in self.effects
                     if state in {Effect.IN_FLIGHT, Effect.UNRESOLVED})


def operation_snapshot(mission_id: str, bindings: tuple[BindingRow, ...],
                       links: tuple[LinkRow, ...], actions: tuple[ActionRow, ...],
                       *, complete_read: bool) -> OperationProof:
    if complete_read is not True:
        raise SourceUnavailable('operation_read_incomplete')
    # The repository adapter must query the FULL mission domain, including retired work.
    if any(r.mission_id != mission_id for group in (bindings, links, actions) for r in group):
        raise SourceUnavailable('cross_mission_rows')
    bmap = {b.operation_id: b for b in bindings}
    lmap = {l.operation_id: l for l in links}
    # REFUSED with zero handoffs and no idempotency key is an explicit refusal,
    # not an executable operation. Keep it in the digest, never synthesize an op id.
    effect_actions = tuple(a for a in actions if not (
        a.state == 'REFUSED' and a.handoffs == 0 and a.idempotency_key is None))
    amap = {a.action_key: a for a in effect_actions}
    if (len(bmap) != len(bindings) or len(lmap) != len(links) or
            len(amap) != len(effect_actions) or
            len({l.action_key for l in links}) != len(links)):
        raise SourceUnavailable('mapping_not_unique')
    if set(bmap) != set(lmap) or {l.action_key for l in links} != set(amap):
        raise SourceUnavailable('operation_mapping_incomplete')
    pairs: list[tuple[str, Effect]] = []
    for op, link in sorted(lmap.items()):
        bound, action = bmap[op], amap[link.action_key]
        if (link.occurrence_id, link.request_hash, link.envelope_hash) != (
                bound.occurrence_id, bound.request_hash, bound.envelope_hash):
            raise SourceUnavailable('envelope_link_mismatch')
        if (action.action_id, action.version, action.params_hash, action.idempotency_key) != (
                link.action_id, link.action_version, link.params_hash, link.idempotency_key):
            raise SourceUnavailable('action_link_mismatch')
        pairs.append((op, classify_effect(action)))
    # All rows, not just unresolved rows: insertion/removal and positive zero must revalidate.
    raw = {'mission': mission_id,
           'bindings': sorted((b.__dict__ for b in bindings), key=lambda v: v['operation_id']),
           'links': sorted((l.__dict__ for l in links), key=lambda v: v['operation_id']),
           'actions': sorted((a.__dict__ for a in actions), key=lambda v: v['action_key'])}
    return OperationProof(mission_id, digest(raw), tuple(pairs))


def operation_gate(proof: OperationProof) -> None:
    if proof.unresolved:
        raise Denied('OPERATION_UNRESOLVED', 'authoritative_ledger_requires_reconciliation')


DELTA_CODES = {
    'not_reducible': 'STRUCTURE_INVALID',
    'port_unbindable': 'DATA_UNBOUND',
    'precondition_false': 'METHOD_INAPPLICABLE',
    'precondition_unknown': 'EVIDENCE_REQUIRED',
    'precondition_conflict': 'EVIDENCE_CONFLICT',
    'precondition_witness_stale': 'REQUEST_BINDING_STALE',
    'root_coverage_gap': 'COVERAGE_GAP',
    'size_bound': 'PLANNING_BOUND_REACHED',
    'refinement_cycle': 'REFINEMENT_CYCLE',
}
PROJECTION_CODES = {
    'cycle': 'ORDER_CYCLE',
    'dangling_endpoint': 'STRUCTURE_INVALID',
    'missing_edge': 'STRUCTURE_INVALID',
    'no_gating_children': 'COVERAGE_GAP',
    'duplicate_slot': 'STRUCTURE_INVALID',
    'unbound_port': 'DATA_UNBOUND',
    'single_port_overbound': 'DATA_UNBOUND',
    'set_port_unordered': 'DATA_UNBOUND',
    'orphan_obligation': 'COVERAGE_GAP',
    'unreached_required_occurrence': 'COVERAGE_GAP',
    'root_coverage_gap': 'COVERAGE_GAP',
    'resource_conflict': 'STRUCTURE_INVALID',
    'bound_reached': 'PLANNING_BOUND_REACHED',
}


def map_delta(kind: str, *, projection_kinds: Iterable[str] = ()) -> tuple[str, ...]:
    if kind == 'not_checked':
        raise SourceUnavailable('preview_check_incomplete')
    if kind == 'projection_defect':
        details = tuple(projection_kinds)
        if not details:
            raise SourceUnavailable('projection_problem_missing_typed_cause')
        if any(k not in PROJECTION_CODES for k in details):
            raise SourceUnavailable('projection_check_incomplete_or_unsupported')
        return tuple(dict.fromkeys(PROJECTION_CODES[k] for k in details))
    if kind not in DELTA_CODES:
        raise SourceUnavailable('unmapped_delta_problem_kind')
    return (DELTA_CODES[kind],)


@dataclass(frozen=True)
class NoPlanMutation:
    decision_kind: str

    def __post_init__(self) -> None:
        if self.decision_kind not in REPORTS:
            raise ValueError('mutation cannot bypass preview')


@dataclass(frozen=True)
class CheckedPlanShape:
    decision_hash: str
    snapshot_hash: str
    delta_hash: str
    validator_version: str
    all_checks_executed: bool
    codes: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ('decision_hash','snapshot_hash','delta_hash','validator_version'):
            text(getattr(self, name))
        if self.all_checks_executed is not True:
            raise SourceUnavailable('preview_not_checked')


def plan_gate(proof: CheckedPlanShape, *, decision_hash: str,
              snapshot_hash: str, delta_hash: str) -> None:
    if (proof.decision_hash, proof.snapshot_hash, proof.delta_hash) != (
            decision_hash, snapshot_hash, delta_hash):
        raise Denied('REQUEST_BINDING_STALE', 'preview_binding_changed')
    if proof.codes:
        raise Denied(proof.codes[0], 'typed_compiler_or_validator_rejection')
