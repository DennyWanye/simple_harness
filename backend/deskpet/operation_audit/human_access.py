"""Explicit signed HUMAN metadata grants and durable logical page deliveries.

SDK cursors/authority nonce never go to the UI. No SDK private tables, Provider
calls, live fallback, business effect replay or inferred zero usage/cost.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import build_host_typed_evidence
from deskpet.memory.writer_fence import (
    human_memory_request_boundary, require_human_audit_request,
)
from deskpet.operation_audit.memory_reader import validated
from deskpet.operation_audit.store import canonical, digest

MAX_READS = 32
PAGE_LIMIT = 100
TTL = 300
_SCHEMA = """
CREATE TABLE IF NOT EXISTS human_audit_grants (
 audit_ref TEXT PRIMARY KEY, lease_ref TEXT NOT NULL, action_id TEXT NOT NULL,
 subject TEXT NOT NULL, primary_ref TEXT NOT NULL, evidence_id TEXT,
 issued_at REAL, expires_at REAL, status TEXT NOT NULL, receipt_hash TEXT,
 snapshot_hash TEXT, cursor_ref TEXT, sdk_cursor TEXT, reads INTEGER NOT NULL DEFAULT 0,
 UNIQUE(lease_ref,action_id)
);
CREATE TABLE IF NOT EXISTS human_audit_deliveries (
 audit_ref TEXT NOT NULL, action_id TEXT NOT NULL, input_hash TEXT NOT NULL,
 status TEXT NOT NULL, started_at REAL NOT NULL, response_json TEXT, response_hash TEXT,
 page_hash TEXT, access_event_hash TEXT,
 PRIMARY KEY(audit_ref,action_id)
);
"""


class HumanAuditError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def identifier(value):
    if type(value) is not str or not 1 <= len(value) <= 128 or any(
        c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_:.' for c in value
    ):
        raise HumanAuditError('primary_audit_request_invalid')
    return value


@dataclass
class _Grant:
    ref: str
    lease: str
    subject: str
    primary: str
    action_id: str
    expires: float = 0
    receipt: object = None
    decision: object = None
    authority: object = None
    closed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class HumanAuditAccess:
    def __init__(self, state_path, *, clock=time.time, fault=None):
        self.state_path = Path(state_path)
        self.path = self.state_path.with_name('operation-audit.db')
        self.clock = clock
        self.fault = fault or (lambda _: None)
        self._grants: dict[str, _Grant] = {}
        self._open_lock = asyncio.Lock()

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.executescript(_SCHEMA)
            with db:
                yield db
        finally:
            db.close()

    def _check(self, grant):
        if require_human_audit_request() != grant.lease:
            raise HumanAuditError('primary_audit_connection_changed')
        if grant.closed:
            raise HumanAuditError('primary_audit_closed')
        if self.clock() >= grant.expires:
            raise HumanAuditError('primary_audit_expired')

    def invalidate_all(self):
        # Closing the runtime revokes capabilities even if the same runtime
        # object is subsequently reopened. Durable unknown/delivery rows remain.
        for grant in self._grants.values():
            grant.closed = True
        self._grants.clear()

    async def resolve_audit_access(self, reference):
        # This public authority port resolves only a server-minted, current request.
        for grant in self._grants.values():
            if grant.authority == reference:
                self._check(grant)
                return grant.decision
        raise HumanAuditError('primary_audit_authority_unavailable')

    async def _primary(self, auth, principal, primary):
        require_human_audit_request()
        identifier(primary)
        if principal.actor_id != auth.subject:
            raise HumanAuditError('primary_audit_subject_mismatch')
        await HumanMemoryProgramStore(self.state_path).open_primary_conversation(
            auth.subject, requested_conversation_id=primary)

    def _open_result(self, grant):
        return {'primary_ref': grant.primary, 'audit_ref': grant.ref,
                'open_action_id': grant.action_id,
                'expires_at': grant.expires, 'max_reads': MAX_READS,
                'page_limit': PAGE_LIMIT, 'purpose': 'operation_metadata'}

    async def open(self, *, manager, principal, auth, primary_ref, open_action_id):
        lease = require_human_audit_request()
        identifier(open_action_id)
        if any(not callable(getattr(manager, name, None)) for name in
               ('authorize_audit_access', 'read_operation_audit')):
            raise HumanAuditError('primary_audit_capability_unavailable')
        await self._primary(auth, principal, primary_ref)
        async with self._open_lock:
            async with human_memory_request_boundary():
                if require_human_audit_request() != lease:
                    raise HumanAuditError('primary_audit_connection_changed')
                with self._db() as db:
                    row = db.execute('SELECT * FROM human_audit_grants WHERE lease_ref=? AND action_id=?',
                                     (lease, open_action_id)).fetchone()
                    if row:
                        if row['subject'] != auth.subject or row['primary_ref'] != primary_ref:
                            raise HumanAuditError('primary_audit_action_conflict')
                        grant = self._grants.get(row['audit_ref'])
                        if grant is None or row['status'] != 'active':
                            raise HumanAuditError('primary_audit_open_unconfirmed')
                        self._check(grant)
                        return self._open_result(grant)
                    # Bound live capability memory; durable archives are not deleted.
                    self._grants = {key: value for key, value in self._grants.items()
                                    if not value.closed and value.expires > self.clock()}
                    if len(self._grants) >= 64:
                        raise HumanAuditError('primary_audit_active_limit')
                    grant = _Grant(uuid.uuid4().hex, lease, auth.subject, primary_ref, open_action_id)
                    db.execute('INSERT INTO human_audit_grants(audit_ref,lease_ref,action_id,subject,primary_ref,status) VALUES(?,?,?,?,?,?)',
                               (grant.ref, lease, open_action_id, auth.subject, primary_ref, 'requested'))
                try:
                    await self._authorize(grant, manager, principal, auth, open_action_id)
                    self._check(grant)
                    with self._db() as db:
                        db.execute("UPDATE human_audit_grants SET status='active',receipt_hash=? WHERE audit_ref=?",
                                   (grant.receipt.receipt_hash, grant.ref))
                except BaseException:
                    grant.closed = True
                    with self._db() as db:
                        db.execute("UPDATE human_audit_grants SET status='unknown' WHERE audit_ref=?", (grant.ref,))
                    raise
                return self._open_result(grant)

    async def _authorize(self, grant, manager, principal, auth, action_id):
        import simple_harness as h
        import simple_harness_memory as m

        key = digest(['host.audit.action.v1', grant.lease, action_id])
        envelope, receipt = build_host_typed_evidence(
            subject=auth.subject, authority_ref=auth.authority_ref,
            payload={'schema': 'primary-audit-access/v1', 'primary_ref': grant.primary,
                     'purpose': 'operation_metadata', 'scope': 'subject', 'max_reads': MAX_READS,
                     'ttl_seconds': TTL},
            idempotency_key='audit-access:'+key, source_ref='host-audit-action/access/v1:'+key)
        committed = await HumanMemoryProgramStore(self.state_path).append_evidence(envelope, receipt)
        if committed.envelope_sha256 != envelope.envelope_hash or committed.subject != auth.subject:
            raise HumanAuditError('primary_audit_action_invalid')
        issued = committed.committed_at
        grant.expires = issued + TTL
        disclosure = h.DisclosureContext(
            grant.ref, principal.actor_id, h.DeliveryRecipient.AUDIT_REVIEWER,
            principal.actor_id, h.IntendedAudience.AUDITOR, h.DisclosurePurpose.AUDIT,
            h.DisclosureSource.AUDIT_ACCESS_DECISION, h.DisclosureTrust.TRUSTED_AUTHORITY,
            h.DisclosureGeneration.CURRENT, 'host:human-audit/v1',
            (h.DisclosureReasonCode.MINIMUM_NECESSARY,))
        grant.decision = m.SealedAuditAccessDecision(
            'human-audit:'+grant.ref, principal.actor_id, m.SuppressionScopeKind.SUBJECT,
            principal.actor_id, 'user_review', disclosure, MAX_READS, issued, grant.expires,
            # 0.6.12 exposes only this sealed SDK purpose. Host narrows the
            # capability to operation metadata; the receipt never leaves Host.
            purpose=m.SealedAuditPurpose.EVIDENCE_AUDIT)
        grant.authority = m.AuditAccessAuthorityRefV1(
            authority_id='host-human-audit-v1', issuer_ref=committed.evidence_id,
            nonce=uuid.uuid4().hex, replay_identity=grant.ref,
            requester_deployment_id=principal.deployment_id, requester_household_id=principal.household_id,
            requester_actor_id=principal.actor_id, requester_session_id=principal.session_id,
            target_deployment_id=principal.deployment_id, target_household_id=principal.household_id,
            target_actor_id=principal.actor_id, target_subject=principal.actor_id,
            decision_id=grant.decision.decision_id, decision_hash=grant.decision.decision_hash,
            scope_kind=grant.decision.scope_kind, scope_ref=grant.decision.scope_ref,
            issued_at=issued, expires_at=grant.expires)
        self._grants[grant.ref] = grant
        with self._db() as db:
            db.execute('UPDATE human_audit_grants SET evidence_id=?,issued_at=?,expires_at=? WHERE audit_ref=?',
                       (committed.evidence_id, issued, grant.expires, grant.ref))
        self.fault('before_authorize')
        self._check(grant)
        grant.receipt = await manager.authorize_audit_access(principal=principal, authority_ref=grant.authority)
        self.fault('after_authorize')

    def _grant(self, *, auth, primary_ref, audit_ref, allow_closed=False):
        lease = require_human_audit_request()
        identifier(audit_ref)
        grant = self._grants.get(audit_ref)
        if grant is None:
            raise HumanAuditError('primary_audit_session_unavailable')
        if grant.subject != auth.subject or grant.primary != primary_ref:
            raise HumanAuditError('primary_audit_subject_mismatch')
        if grant.lease != lease:
            raise HumanAuditError('primary_audit_connection_changed')
        if not allow_closed:
            self._check(grant)
        return grant

    async def page(self, *, manager, principal, auth, primary_ref, audit_ref, page_action_id, cursor_ref):
        import simple_harness_memory as m

        identifier(page_action_id)
        if cursor_ref is not None:
            identifier(cursor_ref)
        grant = self._grant(auth=auth, primary_ref=primary_ref, audit_ref=audit_ref)
        if principal.actor_id != grant.subject:
            raise HumanAuditError('primary_audit_subject_mismatch')
        query_hash = digest([audit_ref, primary_ref, cursor_ref])
        async with grant.lock:
            async with human_memory_request_boundary():
                self._check(grant)
                with self._db() as db:
                    row = db.execute('SELECT * FROM human_audit_grants WHERE audit_ref=?', (audit_ref,)).fetchone()
                    delivery = db.execute('SELECT * FROM human_audit_deliveries WHERE audit_ref=? AND action_id=?',
                                          (audit_ref, page_action_id)).fetchone()
                    if delivery:
                        if delivery['input_hash'] != query_hash:
                            raise HumanAuditError('primary_audit_action_conflict')
                        if delivery['status'] != 'saved':
                            raise HumanAuditError('primary_audit_delivery_unknown')
                        response = json.loads(delivery['response_json'])
                        if digest(response) != delivery['response_hash']:
                            raise HumanAuditError('primary_audit_delivery_corrupt')
                        return response
                    if row['status'] != 'active':
                        raise HumanAuditError('primary_audit_delivery_unknown')
                    if row['reads'] >= MAX_READS:
                        raise HumanAuditError('primary_audit_budget_exhausted')
                    if row['cursor_ref'] != cursor_ref or (row['reads'] and row['sdk_cursor'] is None):
                        raise HumanAuditError('primary_audit_cursor_invalid')
                    db.execute("INSERT INTO human_audit_deliveries VALUES(?,?,?,'requested',?,NULL,NULL,NULL,NULL)",
                               (audit_ref, page_action_id, query_hash, self.clock()))
                    db.execute('UPDATE human_audit_grants SET reads=reads+1 WHERE audit_ref=?', (audit_ref,))
                try:
                    self.fault('before_page')
                    self._check(grant)
                    page = await manager.read_operation_audit(
                        requester=principal, target_principal=principal, access_receipt=grant.receipt,
                        limit=PAGE_LIMIT, cursor=m.OperationAuditCursor(row['sdk_cursor']) if row['sdk_cursor'] else None)
                    _, page_hash, access_hash = validated(page)
                    if len(page.items) > PAGE_LIMIT or (row['snapshot_hash'] and row['snapshot_hash'] != page.snapshot_hash):
                        raise HumanAuditError('primary_audit_snapshot_invalid')
                    next_ref = uuid.uuid4().hex if page.next_cursor else None
                    response = self._projection(grant, page, page_action_id, next_ref, row['reads']+1)
                    self.fault('after_page_before_save')
                    self._check(grant)
                    with self._db() as db:
                        db.execute("UPDATE human_audit_deliveries SET status='saved',response_json=?,response_hash=?,page_hash=?,access_event_hash=? WHERE audit_ref=? AND action_id=?",
                                   (canonical(response), digest(response), page_hash, access_hash, audit_ref, page_action_id))
                        db.execute('UPDATE human_audit_grants SET snapshot_hash=?,cursor_ref=?,sdk_cursor=? WHERE audit_ref=?',
                                   (page.snapshot_hash, next_ref, page.next_cursor.token if page.next_cursor else None, audit_ref))
                except BaseException:
                    with self._db() as db:
                        db.execute("UPDATE human_audit_deliveries SET status='unknown' WHERE audit_ref=? AND action_id=? AND status='requested'", (audit_ref, page_action_id))
                        db.execute("UPDATE human_audit_grants SET status='unknown' WHERE audit_ref=? AND status='active'", (audit_ref,))
                    raise
                self.fault('after_save_before_ack')
                self._check(grant)
                return response

    def _projection(self, grant, page, action_id, next_ref, reads):
        response = {'primary_ref': grant.primary, 'audit_ref': grant.ref, 'page_action_id': action_id,
                    'snapshot_hash': page.snapshot_hash, 'page_hash': page.page_hash,
                    'access_event_hash': page.access_event_hash,
                    'items': [dict(family=i.family, event_kind=i.event_kind, outcome=i.outcome,
                                   occurred_at=i.occurred_at, cognitive_effect=i.cognitive_effect,
                                   operation_ref_hash=i.operation_ref_hash, item_hash=i.item_hash) for i in page.items],
                    'coverage': [dict(family=c.family, row_count=c.row_count,
                                      unresolved_count=len(c.unresolved_ref_hashes),
                                      missing_count=len(c.missing_event_ref_hashes),
                                      exclusions=list(c.exclusions[:16]),
                                      exclusions_truncated=len(c.exclusions)>16) for c in page.coverage],
                    'next_cursor_ref': next_ref, 'enumeration_complete': page.enumeration_complete,
                    'all_operations_recorded': False, 'reads_used': reads, 'max_reads': MAX_READS,
                    'expires_at': grant.expires}
        if len(canonical(response).encode()) > 256*1024:
            raise HumanAuditError('primary_audit_response_too_large')
        return response

    async def close(self, *, auth, primary_ref, audit_ref):
        # No page lock: an admitted SDK read may finish, but must not disclose.
        async with human_memory_request_boundary():
            grant = self._grant(auth=auth, primary_ref=primary_ref, audit_ref=audit_ref, allow_closed=True)
            grant.closed = True
            with self._db() as db:
                db.execute("UPDATE human_audit_grants SET status='closed' WHERE audit_ref=?", (audit_ref,))
        return {'primary_ref': primary_ref, 'audit_ref': audit_ref, 'status': 'closed'}

    def final_check(self, *, auth, primary_ref, audit_ref, allow_closed=False):
        grant = self._grant(auth=auth, primary_ref=primary_ref, audit_ref=audit_ref, allow_closed=allow_closed)
        if allow_closed and not grant.closed:
            raise HumanAuditError('primary_audit_close_unconfirmed')
