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
# Host-side run-audit sections (G6). Reads are local operation-audit.db rows,
# never an SDK charge, so one transaction freezes request+read+save together.
HOST_MAX_READS = 32
HOST_SECTIONS = {'runs': 20, 'run_operations': 100, 'memory_calls': 50}
_OPERATION_FIELDS = (
    'operation_id', 'kind', 'record_type', 'operation_name', 'state', 'error_code',
    'created_at', 'settled_at', 'handoff_to_settlement_seconds', 'parent_operation_id',
    'effect_id', 'provider_invocation_id', 'request_hash', 'result_hash', 'source_hash',
)
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
CREATE TABLE IF NOT EXISTS human_audit_host_streams (
 audit_ref TEXT NOT NULL, stream_key TEXT NOT NULL, section TEXT NOT NULL, target_ref TEXT,
 snapshot_at REAL NOT NULL, snapshot_hash TEXT NOT NULL, cursor_ref TEXT, keyset_json TEXT,
 reads INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(audit_ref,stream_key)
);
CREATE TABLE IF NOT EXISTS human_audit_host_deliveries (
 audit_ref TEXT NOT NULL, action_id TEXT NOT NULL, input_hash TEXT NOT NULL,
 status TEXT NOT NULL, started_at REAL NOT NULL, section TEXT NOT NULL,
 response_json TEXT, response_hash TEXT, page_hash TEXT,
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

    async def host_page(self, *, principal, auth, primary_ref, audit_ref, page_action_id,
                        section, cursor_ref, target_ref):
        """Paginate Host run-audit pages / memory-call journal under the same grant.

        Same durable logical delivery rules as ``page``: one action key is one
        response; a changed input under the same key conflicts; a stream never
        restarts inside a grant (a new explicit open selects a new snapshot).
        Rows are payload-free hashes/codes/timestamps; SDK payloads never appear.
        """
        identifier(page_action_id)
        if section not in HOST_SECTIONS:
            raise HumanAuditError('primary_audit_request_invalid')
        if cursor_ref is not None:
            identifier(cursor_ref)
        if (target_ref is None) != (section != 'run_operations'):
            raise HumanAuditError('primary_audit_request_invalid')
        if target_ref is not None:
            identifier(target_ref)
        grant = self._grant(auth=auth, primary_ref=primary_ref, audit_ref=audit_ref)
        if principal.actor_id != grant.subject:
            raise HumanAuditError('primary_audit_subject_mismatch')
        stream_key = digest(['host-audit-stream/v1', section, target_ref])
        query_hash = digest([audit_ref, primary_ref, section, target_ref, cursor_ref])
        async with grant.lock:
            async with human_memory_request_boundary():
                self._check(grant)
                with self._db() as db:
                    row = db.execute('SELECT status FROM human_audit_grants WHERE audit_ref=?', (audit_ref,)).fetchone()
                    delivery = db.execute('SELECT * FROM human_audit_host_deliveries WHERE audit_ref=? AND action_id=?',
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
                    if row is None or row['status'] != 'active':
                        raise HumanAuditError('primary_audit_delivery_unknown')
                    stream = db.execute('SELECT * FROM human_audit_host_streams WHERE audit_ref=? AND stream_key=?',
                                        (audit_ref, stream_key)).fetchone()
                    reads = stream['reads'] if stream else 0
                    if reads >= HOST_MAX_READS:
                        raise HumanAuditError('primary_audit_budget_exhausted')
                    if (stream is None) != (cursor_ref is None) or (stream is not None and stream['cursor_ref'] != cursor_ref):
                        raise HumanAuditError('primary_audit_cursor_invalid')
                    snapshot_at = stream['snapshot_at'] if stream else self.clock()
                    keyset = json.loads(stream['keyset_json']) if stream and stream['keyset_json'] else None
                    self.fault('before_host_page')
                    items, next_keyset, coverage, total = self._host_read(
                        db, section, target_ref, snapshot_at, keyset, principal)
                    snapshot_hash = stream['snapshot_hash'] if stream else digest(
                        ['host-audit-snapshot/v1', section, target_ref, snapshot_at, total])
                    next_ref = uuid.uuid4().hex if next_keyset is not None else None
                    response = {'primary_ref': grant.primary, 'audit_ref': grant.ref,
                                'page_action_id': page_action_id, 'section': section, 'target_ref': target_ref,
                                'snapshot_hash': snapshot_hash, 'page_hash': digest(items), 'items': items,
                                'coverage': coverage, 'next_cursor_ref': next_ref,
                                'enumeration_complete': next_keyset is None,
                                'all_operations_recorded': False, 'reads_used': reads + 1,
                                'max_reads': HOST_MAX_READS, 'expires_at': grant.expires}
                    if len(canonical(response).encode()) > 256*1024:
                        raise HumanAuditError('primary_audit_response_too_large')
                    self._check(grant)
                    db.execute("INSERT INTO human_audit_host_deliveries VALUES(?,?,?,'saved',?,?,?,?,?)",
                               (audit_ref, page_action_id, query_hash, self.clock(), section,
                                canonical(response), digest(response), response['page_hash']))
                    db.execute('INSERT OR REPLACE INTO human_audit_host_streams VALUES(?,?,?,?,?,?,?,?,?)',
                               (audit_ref, stream_key, section, target_ref, snapshot_at, snapshot_hash, next_ref,
                                canonical(next_keyset) if next_keyset is not None else None, reads + 1))
                self.fault('after_host_save_before_ack')
                self._check(grant)
                return response

    def _host_read(self, db, section, target_ref, snapshot_at, keyset, principal):
        limit = HOST_SECTIONS[section]
        if section == 'runs':
            where, params = 'WHERE created_at<=?', [snapshot_at]
            total = db.execute(f'SELECT count(*) FROM audit_jobs {where}', params).fetchone()[0]
            if keyset:
                where += ' AND (created_at<? OR (created_at=? AND job_id<?))'
                params += [keyset['created_at'], keyset['created_at'], keyset['job_id']]
            rows = db.execute(
                'SELECT job_id,host_run_id,sdk_run_id,terminal_state,status,last_code,total_operations,'
                f'processed_operations,total_pages,next_page,rule_version,created_at,updated_at FROM audit_jobs {where} '
                'ORDER BY created_at DESC,job_id DESC LIMIT ?', [*params, limit + 1]).fetchall()
            items = []
            for r in rows[:limit]:
                items.append({
                    'job_ref': r['job_id'], 'run_ref': r['sdk_run_id'], 'host_run_ref': r['host_run_id'],
                    'terminal_state': r['terminal_state'], 'status': r['status'], 'last_code': r['last_code'],
                    'rule_version': r['rule_version'], 'total_operations': r['total_operations'],
                    'processed_operations': r['processed_operations'], 'total_pages': r['total_pages'],
                    'pages_committed': r['next_page'], 'created_at': r['created_at'], 'updated_at': r['updated_at'],
                    'attempts': dict(db.execute('SELECT COALESCE(outcome,\'unsettled\'),count(*) FROM audit_attempts WHERE job_id=? GROUP BY 1', (r['job_id'],)).fetchall()),
                    'findings': dict(db.execute('SELECT rule_id,count(*) FROM audit_findings WHERE job_id=? GROUP BY rule_id', (r['job_id'],)).fetchall()),
                })
            last = rows[limit - 1] if len(rows) > limit else None
            coverage = {
                'producer_scope': 'foreground_terminal_only', 'status_as_of': 'read_time',
                'jobs_by_status': dict(db.execute('SELECT status,count(*) FROM audit_jobs WHERE created_at<=? GROUP BY status', (snapshot_at,)).fetchall()),
                'source_rejections': db.execute('SELECT count(*) FROM audit_source_rejections').fetchone()[0],
            }
            return items, ({'created_at': last['created_at'], 'job_id': last['job_id']} if last else None), coverage, total
        if section == 'memory_calls':
            from deskpet.operation_audit.memory_attempts import owner_ref
            owner = owner_ref(principal)
            where, params = 'WHERE owner_ref=? AND started_at<=?', [owner, snapshot_at]
            total = db.execute(f'SELECT count(*) FROM memory_call_attempts {where}', params).fetchone()[0]
            if keyset:
                where += ' AND (started_at<? OR (started_at=? AND attempt_ref<?))'
                params += [keyset['started_at'], keyset['started_at'], keyset['attempt_ref']]
            rows = db.execute(
                'SELECT attempt_ref,request_ref,caller,state,observation_status,started_at,settled_at,'
                'context_run_ref_hash,context_hash,plan_hash,result_hash,decision_hash,observation_hash '
                f'FROM memory_call_attempts {where} ORDER BY started_at DESC,attempt_ref DESC LIMIT ?',
                [*params, limit + 1]).fetchall()
            items = []
            for r in rows[:limit]:
                finding = db.execute('SELECT reason FROM memory_call_findings WHERE operation_ref=? ORDER BY finding_id LIMIT 1',
                                     (r['attempt_ref'],)).fetchone()
                items.append({**dict(r), 'finding_reason': finding['reason'] if finding else None})
            last = rows[limit - 1] if len(rows) > limit else None
            coverage = {
                'producer_scope': 'host_memory_call_journal', 'status_as_of': 'read_time',
                'callers': dict(db.execute('SELECT caller,count(*) FROM memory_call_attempts WHERE owner_ref=? AND started_at<=? GROUP BY caller', (owner, snapshot_at)).fetchall()),
                'findings': db.execute('SELECT count(*) FROM memory_call_findings WHERE owner_ref=?', (owner,)).fetchone()[0],
            }
            return items, ({'started_at': last['started_at'], 'attempt_ref': last['attempt_ref']} if last else None), coverage, total
        job = db.execute('SELECT * FROM audit_jobs WHERE job_id=?', (target_ref,)).fetchone()
        if job is None:
            raise HumanAuditError('primary_audit_target_unavailable')
        # The stream freezes the committed page count on its first read, so a job
        # that keeps committing pages cannot shift this stream's offset window.
        frozen = int(keyset['pages'] if keyset else (job['next_page'] or 0))
        records = []
        for page in db.execute('SELECT payload_json FROM audit_pages WHERE job_id=? AND page_index<? ORDER BY page_index',
                               (target_ref, frozen)):
            try:
                operations = json.loads(page['payload_json'])['operations']
            except (ValueError, KeyError, TypeError):
                continue
            records.extend(op for op in operations if isinstance(op, dict) and op.get('record_type') in ('head', 'boundary'))
        offset = keyset['offset'] if keyset else 0
        items = []
        for op in records[offset:offset + limit]:
            item = {key: op.get(key) for key in _OPERATION_FIELDS}
            usage = op.get('usage')
            item['usage'] = {k: v for k, v in usage.items() if isinstance(v, (int, float)) and not isinstance(v, bool)} if isinstance(usage, dict) else None
            items.append(item)
        next_keyset = {'offset': offset + limit, 'pages': frozen} if offset + limit < len(records) else None
        coverage = {
            'producer_scope': 'foreground_terminal_only', 'status_as_of': 'read_time',
            'job_status': job['status'], 'last_code': job['last_code'], 'run_ref': job['sdk_run_id'],
            'total_operations': job['total_operations'], 'processed_operations': job['processed_operations'],
            'pages_frozen': frozen,
            'record_types': ['head', 'boundary'],
            'findings': dict(db.execute('SELECT rule_id,count(*) FROM audit_findings WHERE job_id=? GROUP BY rule_id', (target_ref,)).fetchall()),
        }
        return items, next_keyset, coverage, len(records)

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
