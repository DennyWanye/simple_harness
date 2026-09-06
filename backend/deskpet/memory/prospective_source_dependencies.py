"""Occurrence text inherits real mutation evidence, never a synthetic recall ID.

SDK reads happen outside Host writer transactions. The existing terminal S1
commits the resulting bindings; no second ledger or old evidence backfill.
"""
from __future__ import annotations

import aiosqlite

from deskpet.memory.primary_visibility import PrimaryHistoryPolicy, read_evidence_pair
from deskpet.memory.prospective_occurrence import decode_snapshot
from deskpet.memory.s5c_store import S5cConflict
from deskpet.memory.trusted_disclosure import resolve_current_disclosure
from deskpet.task_scope.protocol import canonical_hash


class ProspectiveSourceDependencies:
    def __init__(self, *, store, runtime_getter):
        self.store, self.runtime_getter = store, runtime_getter

    async def _manager(self):
        runtime = self.runtime_getter()
        if runtime is None or runtime.principal() != self.store.principal:
            raise S5cConflict('s5c_occurrence_source_principal_differs')
        return await runtime.manager()

    async def _evidence_ids(self, manager, memory_id, revision, seen=None):
        from simple_harness_memory import (
            MutationTargetSource, ProspectiveSignalTargetSource,
            ProspectiveOutboxSourceViewV2, MemoryMutationReceiptView,
        )
        seen = set() if seen is None else seen
        key = (memory_id, revision)
        if key in seen or len(seen) >= 64:
            raise S5cConflict('s5c_occurrence_source_cycle_or_limit')
        seen.add(key)
        registered = await self.store.accepted_registration(memory_id=memory_id, revision=revision)
        if registered is None:
            raise S5cConflict('s5c_occurrence_source_registration_missing')
        entry, intent = registered.entry, registered.authority.intent
        source = await manager.read_prospective_outbox_source_v2(
            principal=self.store.principal, outbox_id=entry.outbox_id, payload_hash=entry.payload_hash)
        if type(source) is not ProspectiveOutboxSourceViewV2:
            raise S5cConflict('s5c_occurrence_source_type_invalid')
        source.target_scope.authorize(self.store.principal)
        expected = dict(schema_version=1, command='registration', memory_id=memory_id,
            prospective_revision=revision, registration_revision=source.registration_revision,
            trigger=source.trigger.to_json(), trigger_hash=source.trigger_hash)
        if (source.subject != self.store.principal.actor_id or source.command != 'registration'
                or source.outbox_id != entry.outbox_id or source.outbox_payload_hash != entry.payload_hash
                or source.outbox_created_at != entry.created_at or dict(entry.payload or {}) != expected
                or canonical_hash(expected) != entry.payload_hash
                or (source.target_memory_id, source.target_revision, source.registration_revision,
                    source.trigger_hash, source.target_scope.kind.value, source.target_scope.owner_id,
                    source.target_source.run_id, source.target_source.operation_id) !=
                   (intent.target_memory_id, intent.target_revision, intent.registration_revision,
                    intent.trigger_hash, intent.scope.kind.value, intent.scope.owner_id,
                    intent.run_id, intent.operation_id)):
            raise S5cConflict('s5c_occurrence_source_binding_differs')
        target = source.target_source
        if type(target) is MutationTargetSource:
            ref = target.mutation_receipt_ref
            if (ref.receipt_id, ref.receipt_hash) != (intent.signal_receipt_id, intent.signal_receipt_hash):
                raise S5cConflict('s5c_occurrence_source_receipt_differs')
            view = await manager.get_memory_mutation_receipt_view(principal=self.store.principal, receipt_ref=ref)
            if (type(view) is not MemoryMutationReceiptView or
                    (view.receipt_id, view.receipt_hash, view.plan_id, view.plan_hash) !=
                    (ref.receipt_id, ref.receipt_hash, target.plan_id, target.plan_hash)):
                raise S5cConflict('s5c_occurrence_source_receipt_differs')
            operations = [op for op in view.operations if op.operation_id == target.operation_id]
            if len(operations) != 1 or (operations[0].memory_id, operations[0].revision,
                    operations[0].memory_type) != (memory_id, revision, 'prospective'):
                raise S5cConflict('s5c_occurrence_source_operation_differs')
            return operations[0].evidence_ids
        if type(target) is ProspectiveSignalTargetSource:
            result = target.apply_result
            if ((result.result_id, result.result_hash) != (intent.signal_receipt_id, intent.signal_receipt_hash)
                    or result.memory_id != memory_id or result.committed_revision != revision
                    or not 0 < result.base_revision < revision):
                raise S5cConflict('s5c_occurrence_source_signal_differs')
            return await self._evidence_ids(manager, memory_id, result.base_revision, seen)
        raise S5cConflict('s5c_occurrence_source_type_invalid')

    async def for_group(self, group, *, check_current):
        if group.owner != self.store.owner:
            raise S5cConflict('s5c_occurrence_source_owner_differs')
        if not group.items:
            return []
        manager = await self._manager()
        ids = set()
        for item in group.items:
            ids.update(await self._evidence_ids(manager, item.entry.memory_id, item.entry.prospective_revision))
        if not ids or len(ids) > 256:
            raise S5cConflict('s5c_occurrence_source_evidence_missing_or_limit')
        async with aiosqlite.connect(self.store.path) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute('SELECT r.subject,r.primary_conversation_id FROM foreground_runs r '
                'JOIN foreground_run_sdk_bindings b ON b.host_run_id=r.host_run_id WHERE b.sdk_run_id=?',
                (group.sdk_run_id,))).fetchone()
            if row is None or row['subject'] != self.store.principal.actor_id:
                raise S5cConflict('s5c_occurrence_source_run_differs')
            bindings = []
            for evidence_id in sorted(ids):
                envelope, _ = await read_evidence_pair(db=db, subject=row['subject'],
                    primary_ref=row['primary_conversation_id'], evidence_id=evidence_id)
                bindings.append(dict(evidence_id=evidence_id, envelope_hash=envelope.envelope_hash))
            if check_current:
                context = await resolve_current_disclosure(db_path=self.store.path, subject=row['subject'],
                    run_id=group.sdk_run_id, request_id='host:prospective-source:'+group.sdk_run_id)
                async def checker(*, subject, disclosure_context, bindings):
                    if subject != self.store.principal.actor_id:
                        raise S5cConflict('s5c_occurrence_source_principal_differs')
                    return await manager.check_history_visibility(principal=self.store.principal,
                        disclosure_context=disclosure_context, bindings=bindings)
                policy = PrimaryHistoryPolicy(self.store.path, row['subject'], checker)
                if not await policy.check_dependencies(db=db, primary_ref=row['primary_conversation_id'],
                        dependencies=dict(schema_version=1, evidence=bindings, recall=[]), disclosure_context=context):
                    raise S5cConflict('s5c_occurrence_source_not_visible')
                # The slow Memory check evaluates the supplied original context.
                # Revalidate its Host binding after that await; a replacement
                # configuration must not authorize the already-built request.
                checked = await resolve_current_disclosure(db_path=self.store.path, subject=row['subject'],
                    run_id=group.sdk_run_id, request_id='host:prospective-source:'+group.sdk_run_id)
                if checked.to_json() != context.to_json():
                    raise S5cConflict('s5c_occurrence_source_disclosure_changed')
        return bindings

    async def for_run(self, coordinator, sdk_run_id):
        async with aiosqlite.connect(self.store.path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute('SELECT * FROM run_context_snapshot_receipts '
                'WHERE sdk_run_id=? ORDER BY snapshot_revision LIMIT 257', (sdk_run_id,))).fetchall()
        if len(rows) > 256:
            raise S5cConflict('s5c_occurrence_source_snapshot_limit')
        manifest = []
        for row in rows:
            _, group = decode_snapshot(row)
            if group is None or not group.items:
                continue
            group = await coordinator.restore_snapshot(sdk_run_id=sdk_run_id,
                provider_turn_ordinal=row['provider_turn_ordinal'],
                prior_context_revision=row['prior_context_revision'], snapshot_id=row['snapshot_id'])
            bindings = await self.for_group(group, check_current=False)
            manifest.append(dict(snapshot_id=row['snapshot_id'], receipt_hash=row['receipt_hash'], evidence=bindings))
        return manifest


async def verify_terminal_sources_tx(db, *, sdk_run_id, manifest, evidence):
    """Require every nonempty A7 snapshot; legacy missing proof stays unreadable."""
    version = (await (await db.execute('PRAGMA user_version')).fetchone())[0]
    if version < 45 and manifest is None:
        return
    rows = await (await db.execute('SELECT * FROM run_context_snapshot_receipts '
        'WHERE sdk_run_id=? ORDER BY snapshot_revision LIMIT 257', (sdk_run_id,))).fetchall()
    if len(rows) > 256:
        raise S5cConflict('s5c_occurrence_source_snapshot_limit')
    required = []
    for row in rows:
        _, group = decode_snapshot(row)
        if group is not None and group.items:
            required.append((row['snapshot_id'], row['receipt_hash']))
    if not required and manifest is None:
        return
    if not isinstance(manifest, (tuple, list)) or len(manifest) != len(required):
        raise S5cConflict('s5c_occurrence_history_source_missing')
    for item, expected in zip(manifest, required):
        if (set(item) != {'snapshot_id', 'receipt_hash', 'evidence'}
                or (item['snapshot_id'], item['receipt_hash']) != expected
                or not isinstance(item['evidence'], (list, tuple)) or not item['evidence']):
            raise S5cConflict('s5c_occurrence_history_source_differs')
        for binding in item['evidence']:
            if (set(binding) != {'evidence_id', 'envelope_hash'}
                    or evidence.get(binding['evidence_id']) != binding['envelope_hash']):
                raise S5cConflict('s5c_occurrence_history_source_binding_missing')
