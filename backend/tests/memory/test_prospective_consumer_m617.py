"""Host52 / installed M617 consumer controls; no models or Memory SQL.

Legacy setup selects the frozen 7.2 initializer/catalog only, then all facts
come from public Manager mutations/signals. It is not an M616 binary run.
"""
from dataclasses import replace
import json

import pytest
import simple_harness_memory as m
from simple_harness.runtime import (EvidenceRef, MemoryMutationPlan, MemoryActionAuthorityRef,
    ExistingMemoryTarget, MemoryMutationKind, ProspectiveMemoryPayload,
    ProspectiveTimeTrigger, ProspectiveLifecycleState, issue_memory_action_authority)
from simple_harness_memory.core.mutations import InformationClassificationPolicy
from deskpet.memory.analysis_proposal import admitted_item, compile_operation, derive_span
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_program import HumanMemoryProgramStore
from deskpet.memory.human_memory_service import HOST_PUBLIC_TURN_FILTER_POLICY, build_foreground_turn_evidence
from deskpet.memory.s5c_terminal_schema import initialize_s5c_terminal_state_db
from deskpet.memory.s5c_store import S5cStore, HostProspectiveSignalAuthority, S5cConflict
from deskpet.memory.s5c_consumer import ProspectiveRegistrationConsumer
from deskpet.memory.prospective_registration_source import PublicRegistrationAuthoritySource
from deskpet.memory.prospective_signal_store import ProspectiveSignalStore
from deskpet.memory.prospective_time_source import PublicTimeAuthoritySource
from deskpet.memory.prospective_scheduler import ProspectiveScheduler
from tests.memory.test_s5c_store import P


class World:
    def __init__(self, root):
        self.root, self.path, self.clock = root, root/'state.db', [20.0]
        self.action = None
        self.base = 1
        self.calls = []
        self.applied_outboxes = []
        self.fault = None

    async def resolve_memory_action_authority(self, ref):
        assert ref == MemoryActionAuthorityRef.from_authority(self.action)
        return self.action

    async def resolve_prospective_signal_authority(self, ref):
        if ref.authority_id.startswith('host:time-authority:'):
            return await ProspectiveSignalStore(self.path,P).resolve_prospective_signal_authority(ref)
        return await HostProspectiveSignalAuthority(self.path,P).resolve_prospective_signal_authority(ref)

    async def open(self):
        self.manager = await m.build_human_memory_v7(self.root/'memory.db', clock=lambda:self.clock[0],
            evidence_authority=HostEvidenceAuthority(self.path), memory_action_authority=self,
            prospective_signal_authority=self,
            supported_filter_policies=getattr(self,'filter_policies',frozenset({HOST_PUBLIC_TURN_FILTER_POLICY})),
            **getattr(self,'history_options',{}),
            classification_policy=InformationClassificationPolicy(policy_id='s5c-test-classification',
                policy_version='1',authority_ref='host:classification/v1',required_privacy_class='personal',
                required_information_attributes=()))

    async def setup(self):
        program=HumanMemoryProgramStore(self.path)
        await program.initialize_subject(P.actor_id)
        text='Remind me to send the report at the specified time.'
        envelope,receipt=build_foreground_turn_evidence(subject=P.actor_id,
            authority_ref='host:test-local-owner',delivery_key='m617-source',text=text)
        await program.append_evidence(envelope,receipt)
        await initialize_s5c_terminal_state_db(self.path)
        item=admitted_item(envelope,receipt)
        self.operation=compile_operation(dict(operation_id='create',memory_type='prospective',
            prospective=dict(action='send the report',trigger_at_iso='1970-01-01T00:00:30+00:00',timezone='UTC')),
            derive_span(item,text,span_id='m617-span'),item=item,now=20.0)
        self.template=MemoryMutationPlan(plan_id='initial',run_id=envelope.run_id,turn_id='turn',
            subject=P.actor_id,base_revision=1,outcome='mutate',operations=(self.operation,),
            disclosure_context=envelope.disclosure_context,
            evidence_refs=(EvidenceRef(envelope.evidence_id,envelope.envelope_hash,1),),idempotency_key='initial')
        await self.open()
        await self.manager.ingest_committed_evidence(envelope,receipt)

    async def mutate(self, label, target=None):
        operation=replace(self.operation,operation_id=label)
        if target is not None:
            operation=replace(operation,kind=MemoryMutationKind.REVISE,
                target=ExistingMemoryTarget(*target),lifecycle_state=ProspectiveLifecycleState.RESCHEDULED,
                payload=ProspectiveMemoryPayload('send later',ProspectiveTimeTrigger(40.0,'UTC')))
        plan=replace(self.template,plan_id=label,idempotency_key=label,base_revision=self.base,
            operations=(operation,))
        if target is not None:
            self.action=issue_memory_action_authority(plan.action_intent(label),authority_id='action:'+label,
                issued_at=self.clock[0],expires_at=self.clock[0]+100,nonce=label,issuer_ref='host:test-action')
            plan=replace(plan,operations=(replace(operation,
                action_authority_ref=MemoryActionAuthorityRef.from_authority(self.action)),))
        result=await self.manager.apply_memory_mutation_plan(principal=P,scope=m.MemoryScope.personal(P.actor_id),plan=plan)
        assert result.receipt_ref is not None
        self.base+=1
        view=await self.manager.get_memory_mutation_receipt_view(principal=P,receipt_ref=result.receipt_ref)
        return view.operations[0].memory_id

    async def apply_prospective_signal(self, **kwargs):
        self.calls.append(kwargs['reference'])
        authority=await self.resolve_prospective_signal_authority(kwargs['reference'])
        self.last_outbox=authority.intent.outbox_id
        self.applied_outboxes.append(self.last_outbox)
        result=await self.manager.apply_prospective_signal(**kwargs)
        if self.fault: self.fault(result)
        return result

    def __getattr__(self,name):
        return getattr(self.manager,name)

    def consumer(self, fault=None):
        store=S5cStore(self.path,P,fault_inject=fault)
        source=PublicRegistrationAuthoritySource(store=store,memory=self,clock=lambda:self.clock[0],lifetime_seconds=10)
        return ProspectiveRegistrationConsumer(store,self,source)

    async def entries(self):
        return (await self.manager.read_outbox(principal=P,states=('pending','claimed','applied','dead_letter'),limit=100)).entries


async def dependency_world(tmp_path):
    w=World(tmp_path)
    await w.setup()
    # Exact public IDs, no identifier algorithm or fabricated outbox. Select a
    # genuine ordering case from a bounded set of independently created entries.
    for i in range(8): await w.mutate(f'create-{i}')
    registrations=sorted([e for e in await w.entries() if e.topic.endswith('registration.requested')],key=lambda e:e.outbox_id)
    original=registrations[-1]
    await w.mutate('revise-last',(original.payload['memory_id'],1))
    invalid=next(e for e in await w.entries() if e.topic.endswith('invalidation.requested'))
    middle=[e for e in registrations if invalid.outbox_id<e.outbox_id<original.outbox_id]
    assert middle, 'public fixture must actually exercise invalidation < other < registration'
    assert invalid.created_at==middle[0].created_at==original.created_at
    return w,original,invalid,middle[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('point',['host_ack','sdk_ack'])
async def test_dependency_order_and_expired_reopen(tmp_path,point):
    w,original,invalid,middle=await dependency_world(tmp_path)
    fired=False
    def fault(value):
        nonlocal fired
        match=(getattr(w,'last_outbox',None)==original.outbox_id and
            (point=='sdk_ack' or value=='s5c.registration_result.after_commit'))
        if match and not fired:
            fired=True
            raise ConnectionError('lost dependency ACK')
    # Limit the first physical failure to the dependency, not an unrelated row.
    if point=='sdk_ack': w.fault=fault
    consumer=w.consumer(fault if point=='host_ack' else None)
    try:
        with pytest.raises(ConnectionError,match='lost dependency ACK'):
            await consumer.run_once(page_size=1,max_pages=30)
        assert fired
        before=await consumer.store.registration(original.outbox_id)
        assert before is not None
        reference=before.reference
        w.fault=None
        await w.manager.close(); w.clock[0]=1000.; await w.open()
        consumer=w.consumer()
        await consumer.run_once(page_size=1,max_pages=30)
        # A full pending page is deliberately one bounded recovery tick.
        if point=='sdk_ack': await consumer.run_once(page_size=1,max_pages=30)
        for entry in (original,invalid,middle):
            assert (await consumer.store.registration(entry.outbox_id)).result is not None
        assert (await consumer.store.registration(original.outbox_id)).reference==reference
        count=len(w.calls)
        await w.consumer().run_once(page_size=1,max_pages=30)
        assert len(w.calls)==count
        assert (await w.manager.read_occurrence_inbox(principal=P)).entries==()
    finally: await w.manager.close()


async def legacy_world(tmp_path, monkeypatch):
    from simple_harness_memory.backends import schema_v5 as old, sqlite_v5
    from simple_harness_memory.migrations import schema_upgrade, settlement_upgrade
    w=World(tmp_path)
    # Catalog-only fixture per SDK legacy test; no SQL editing of SDK facts.
    with monkeypatch.context() as patch:
        for key in ('REQUIRED_TABLES','SCHEMA_CHECKSUM','SCHEMA_VERSION_LABEL','InitializationReceipt'):
            patch.setattr(sqlite_v5,key,getattr(old,key))
        patch.setattr(sqlite_v5,'_DDL',old.ddl_statements())
        patch.setattr(settlement_upgrade,'probe_existing_root',schema_upgrade.probe_existing_root)
        patch.setattr(settlement_upgrade,'inspect_root',schema_upgrade.inspect_root)
        await w.setup()
        memory_id=await w.mutate('legacy-create')
        await w.consumer().run_once()
        w.clock[0]=30.
        registrations=S5cStore(w.path,P)
        signals=ProspectiveSignalStore(w.path,P)
        timer=ProspectiveScheduler(store=signals,source=PublicTimeAuthoritySource(
            registrations=registrations,signals=signals),memory=w,clock=lambda:w.clock[0])
        assert await timer.tick(claim_owner='legacy-timer')==1
        assert len((await w.manager.read_occurrence_inbox(principal=P)).entries)==1
        await w.mutate('legacy-revise',(memory_id,2))
        invalid=next(e for e in await w.entries() if e.topic.endswith('invalidation.requested'))
        assert invalid.payload['prospective_revision']==2
        await w.manager.close()
    upgrade=await m.migrate_human_memory_v7_2_to_v7_3(w.root/'memory.db',backup_path=w.root/'memory.backup')
    assert upgrade.source_schema_checksum==old.SCHEMA_CHECKSUM
    await w.open()
    return w,invalid


@pytest.mark.asyncio
@pytest.mark.parametrize('point',['s5c.terminal.before_commit','s5c.terminal.after_commit','sdk_settle_ack'])
async def test_legacy_terminal_recovery_and_manager_audit(tmp_path,monkeypatch,point):
    w,entry=await legacy_world(tmp_path,monkeypatch)
    fired=False
    captured=[]
    original=w.manager.settle_prospective_invalidation
    async def settle(**kwargs):
        nonlocal fired
        receipt=await original(**kwargs)
        captured.append(receipt)
        if point=='sdk_settle_ack' and not fired:
            fired=True
            raise ConnectionError('SDK committed terminal ACK lost')
        return receipt
    monkeypatch.setattr(w.manager,'settle_prospective_invalidation',settle)
    def fault(value):
        nonlocal fired
        if value==point and not fired:
            fired=True
            raise ConnectionError('Host terminal ACK lost')
    count=len(w.calls)
    consumer=w.consumer(fault)
    try:
        with pytest.raises(ConnectionError,match='ACK lost'):
            await consumer.run_once(page_size=1,max_pages=30)
        assert fired and captured
        receipt=captured[0]
        assert type(receipt) is m.ProspectiveInvalidationNotRequiredReceipt
        assert await consumer.store.registration(entry.outbox_id) is None
        assert (await consumer.store.terminal(entry.outbox_id) is not None)==(point=='s5c.terminal.after_commit')
        assert len(w.calls)==count, 'not_required must never mint/apply a signal'
        await w.manager.close(); w.clock[0]=1000.; await w.open()
        consumer=w.consumer()
        await consumer.run_once(page_size=1,max_pages=30)
        assert await consumer.store.terminal(entry.outbox_id)==receipt
        assert await consumer.store.registration(entry.outbox_id) is None
        registrations=[e for e in await w.entries() if e.topic.endswith('registration.requested')]
        restored=[await consumer.store.registration(e.outbox_id) for e in registrations]
        assert all(r.result is not None for r in restored)
        assert entry.outbox_id not in w.applied_outboxes, 'terminal must have zero physical apply'
        remaining=[e for e in await w.entries() if e.topic.startswith('memory.prospective.') and e.outbox_id!=entry.outbox_id]
        # TIME_DUE also emitted the real r1 invalidation; both that ACK and
        # the later registration must continue, never a fabricated r2 signal.
        for e in remaining:
            assert w.applied_outboxes.count(e.outbox_id)==1
        page=await consumer.source.operation_audit.page(principal=P,operation='settle_prospective_invalidation')
        bound=[row for row in page['items'] if row['observation_status']=='captured_bound']
        assert bound
        if point=='s5c.terminal.before_commit':
            await reject_terminal_variants(consumer.store, entry, receipt)
        observed=receipt.operation_observation
        # Exact returned public DTO is kept separately from the Host row hash.
        if point!='sdk_settle_ack':
            assert any(json.loads(row['observation_json'])==observed.to_json() and
                row['observation_hash']==observed.observation_hash and row['result_hash']==receipt.receipt_hash
                for row in bound)
        assert all(json.loads(row['observation_json'])['source_hash']==receipt.receipt_hash for row in bound)
        calls=len(w.calls)
        await w.consumer().run_once()
        assert len(w.calls)==calls
    finally: await w.manager.close()


async def reject_terminal_variants(store, entry, receipt):
    """Wrong bindings must fail before a duplicate is accepted, over real DTOs."""
    cursor=await store.cursor()
    changes=(dict(subject='foreign'),dict(target_source_hash='0'*64),
        dict(outbox_payload_hash='0'*64),dict(signal_result_hash='0'*64))
    for change in changes:
        bad=replace(receipt,**change)
        with pytest.raises((S5cConflict,ValueError)):
            await store.commit_not_required(entry,bad,
                expected_source_hash=receipt.target_source_hash,expected_cursor=cursor)
    with pytest.raises((S5cConflict,ValueError)):
        await store.commit_not_required(replace(entry,payload_hash='0'*64),receipt,
            expected_source_hash=receipt.target_source_hash,expected_cursor=cursor)
    with pytest.raises(TypeError,match='typed'):
        await store.commit_not_required(entry,receipt.to_json(),
            expected_source_hash=receipt.target_source_hash,expected_cursor=cursor)
    assert await store.terminal(entry.outbox_id)==receipt
    assert await store.cursor()==cursor


@pytest.mark.asyncio
async def test_source_typed_union_and_owner_payload_fail_closed(tmp_path,monkeypatch):
    w=World(tmp_path)
    await w.setup()
    try:
        await w.mutate('source-negative')
        entry=next(e for e in await w.entries() if e.topic.endswith('registration.requested'))
        consumer=w.consumer()
        source=await w.manager.read_prospective_outbox_source_v2(principal=P,
            outbox_id=entry.outbox_id,payload_hash=entry.payload_hash)
        with pytest.raises(S5cConflict,match='principal_differs'):
            await consumer.source.prepare_registration(principal=replace(P,actor_id='foreign'),entry=entry)
        with pytest.raises(m.MemoryValidationError):
            await consumer.source.prepare_registration(principal=P,entry=replace(entry,payload_hash='0'*64))
        for returned in (source.to_json(), replace(source,subject='foreign')):
            async def wrong(**kwargs): return returned
            with monkeypatch.context() as patch:
                patch.setattr(w.manager,'read_prospective_outbox_source_v2',wrong)
                with pytest.raises(S5cConflict,match='public_target_source_differs'):
                    await consumer.source.prepare_registration(principal=P,entry=entry)
        assert await consumer.store.cursor() is None
        assert await consumer.store.registration(entry.outbox_id) is None
        assert w.calls==[]
    finally: await w.manager.close()
