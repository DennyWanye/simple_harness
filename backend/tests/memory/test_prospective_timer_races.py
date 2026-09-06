"""New timer invalidation/lease/corruption controls, not the prior success cases."""
import json
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from simple_harness.runtime import (ExistingMemoryTarget, MemoryActionAuthorityRef, ProspectiveSignalAuthorityRef,
    MemoryMutationKind, ProspectiveLifecycleState, ProspectiveMemoryPayload,
    ProspectiveTimeTrigger, issue_memory_action_authority)
from simple_harness_memory import MemoryScope
from simple_harness_memory.core.mutations import InformationClassificationPolicy
from simple_harness_memory.backends.sqlite_v5 import SQLiteHumanMemoryBackend
from deskpet.memory.evidence_authority import HostEvidenceAuthority
from deskpet.memory.human_memory_service import HOST_PUBLIC_TURN_FILTER_POLICY
from deskpet.memory.prospective_registration_source import PublicRegistrationAuthoritySource
from deskpet.memory.prospective_scheduler import ProspectiveScheduler
from deskpet.memory.prospective_signal_store import ProspectiveSignalStore, TimerConflict
from deskpet.memory.prospective_time_source import PublicTimeAuthoritySource
from deskpet.memory.s5c_consumer import ProspectiveRegistrationConsumer
from deskpet.memory.s5c_store import HostProspectiveSignalAuthority, S5cStore
from deskpet.memory.s5c_timer_schema import initialize_s5c_timer_state_db
from deskpet.task_scope.protocol import canonical_hash, canonical_json
from tests.memory.test_s5c_consumer_sdk import fixture
from tests.memory.test_s5c_store import P


@asynccontextmanager
async def setup(tmp_path):
    clock=[20.0]
    class Action:
        authority=None
        async def resolve_memory_action_authority(self,ref):
            assert ref==MemoryActionAuthorityRef.from_authority(self.authority)
            return self.authority
    action=Action()
    path,memory,_,plan=await fixture(tmp_path,clock,action_authority=action,return_plan=True)
    await initialize_s5c_timer_state_db(path)
    registrations=S5cStore(path,P)
    await ProspectiveRegistrationConsumer(registrations,memory,
        PublicRegistrationAuthoritySource(store=registrations,memory=memory,clock=lambda:clock[0])).run_once()
    await memory.close()
    signals=ProspectiveSignalStore(path,P)
    old=HostProspectiveSignalAuthority(path,P)
    class Authority:
        async def resolve_prospective_signal_authority(self,ref):
            return await (signals if ref.authority_id.startswith('host:time-authority:') else old).resolve_prospective_signal_authority(ref)
    memory=SQLiteHumanMemoryBackend(tmp_path/'memory.db',now=lambda:clock[0],
        supported_filter_policies=frozenset({HOST_PUBLIC_TURN_FILTER_POLICY}),
        evidence_authority=HostEvidenceAuthority(path),prospective_signal_authority=Authority(),memory_action_authority=action,
        classification_policy=InformationClassificationPolicy(policy_id='s5c-test-classification',
            policy_version='1',authority_ref='host:classification/v1',required_privacy_class='personal',
            required_information_attributes=()))
    await memory.initialize()
    try:
        source=PublicTimeAuthoritySource(registrations=registrations,signals=signals)
        clock[0]=30.0
        prepared=(await source.prepare_due(now=clock[0],limit=10))[0]
        yield path,memory,registrations,signals,source,prepared,clock,plan,action
    finally:
        await memory.close()


def rows(path):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT * FROM prospective_timer_events ORDER BY sequence').fetchall()


@pytest.mark.asyncio
async def test_real_invalidation_between_claim_and_second_host_check(tmp_path):
    async with setup(tmp_path) as (path,memory,registrations,signals,source,prepared,clock,plan,action):
        original_claim=signals.claim
        async def invalidate_after_claim(**kwargs):
            claim=await original_claim(**kwargs)
            if claim is not None:
                op=replace(plan.operations[0],operation_id='late-reschedule',kind=MemoryMutationKind.REVISE,
                    target=ExistingMemoryTarget(prepared.authority.intent.target_memory_id,1),
                    payload=ProspectiveMemoryPayload('later report',ProspectiveTimeTrigger(90.0,'UTC')),
                    lifecycle_state=ProspectiveLifecycleState.RESCHEDULED)
                changed=replace(plan,plan_id='late-reschedule-plan',idempotency_key='late-reschedule-plan',
                    base_revision=2,operations=(op,))
                action.authority=issue_memory_action_authority(changed.action_intent(op.operation_id),
                    authority_id='late-reschedule-grant',issued_at=30,expires_at=60,nonce='late',issuer_ref='host:test-action')
                changed=replace(changed,operations=(replace(op,action_authority_ref=MemoryActionAuthorityRef.from_authority(action.authority)),))
                result=await memory.apply_memory_mutation_plan(principal=P,scope=MemoryScope.personal(P.actor_id),plan=changed)
                assert result.receipt_ref is not None
                await ProspectiveRegistrationConsumer(registrations,memory,
                    PublicRegistrationAuthoritySource(store=registrations,memory=memory,clock=lambda:clock[0])).run_once()
                i=prepared.authority.intent
                assert await registrations.accepted_invalidation(memory_id=i.target_memory_id,revision=i.target_revision,
                    registration_revision=i.registration_revision,registration_ref=i.scheduler_registration_ref) is not None
            return claim
        signals.claim=invalidate_after_claim
        class NeverSend:
            async def apply_prospective_signal(self,**kwargs):
                pytest.fail('invalidated fresh timer reached SDK apply')
        assert await ProspectiveScheduler(store=signals,source=source,memory=NeverSend(),clock=lambda:clock[0]).tick(claim_owner='worker')==0
        assert [r[3] for r in rows(path)]==['prepared','claimed','invalidated']
        assert (await memory.read_occurrence_inbox(principal=P)).entries==()


@pytest.mark.asyncio
async def test_stale_handed_off_owner_cannot_settle_after_lease_takeover(tmp_path):
    async with setup(tmp_path) as (path,memory,_,signals,_,prepared,clock,*_rest):
        first=await signals.claim(owner='old',now=30,lease_seconds=2)
        first=await signals.handoff(first,now=30)
        clock[0]=33
        reopened=ProspectiveSignalStore(path,P)
        second=await reopened.claim(owner='new',now=33,lease_seconds=10)
        assert second.epoch==first.epoch+1 and second.handed_off
        assert second.reference==first.reference
        before=rows(path)
        for operation in (signals.assert_claim,signals.handoff,signals.invalidate):
            with pytest.raises(TimerConflict,match='stale_claim'):
                await operation(first,now=33)
            assert rows(path)==before
        second=await reopened.handoff(second,now=33)
        result=await memory.apply_prospective_signal(principal=P,scope=MemoryScope.personal(P.actor_id),reference=second.reference)
        before=rows(path)
        with pytest.raises(TimerConflict,match='stale_claim'):
            await signals.applied(first,result,now=33)
        assert rows(path)==before
        await reopened.applied(second,result,now=33)
        assert len((await memory.read_occurrence_inbox(principal=P)).entries)==1
        assert await reopened.claim(owner='later',now=50,lease_seconds=10) is None


@pytest.mark.asyncio
async def test_copied_durable_observation_rehashed_tamper_rejected(tmp_path):
    async with setup(tmp_path) as (path,_memory,_,signals,_,prepared,*_rest):
        original=rows(path)
        for field,value in [('observed_at',31.0),('memory_id','foreign-memory'),('scheduler_registration_ref','foreign-registration')]:
            # Explicit corruption copy of the timer journal only; S1 and the
            # original database are never modified or deleted.
            copy_path=tmp_path/(field+'.db')
            with sqlite3.connect(path) as src,sqlite3.connect(copy_path) as db:
                src.backup(db)
                trigger=db.execute("SELECT sql FROM sqlite_master WHERE name='prospective_timer_events_no_update'").fetchone()[0]
                db.execute('DROP TRIGGER prospective_timer_events_no_update')
                row=list(db.execute('SELECT * FROM prospective_timer_events').fetchone())
                body=json.loads(row[7]);body['observation'][field]=value
                body['observation_hash']=canonical_hash(body['observation'])
                row[7]=canonical_json(body);row[8]=canonical_hash(body)
                row[10]=canonical_hash([*row[:7],body,row[9]])
                db.execute('UPDATE prospective_timer_events SET body_json=?,body_hash=?,record_hash=?',(row[7],row[8],row[10]))
                db.execute(trigger);db.commit()
            corrupt=ProspectiveSignalStore(copy_path,P)
            with pytest.raises(TimerConflict,match='observation_binding_differs'):
                await corrupt.get_prepared(prepared.authority.intent.signal_id)
            with pytest.raises(TimerConflict,match='observation_binding_differs'):
                await corrupt.resolve_prospective_signal_authority(ProspectiveSignalAuthorityRef.from_authority(prepared.authority))
        assert rows(path)==original
        assert await signals.get_prepared(prepared.authority.intent.signal_id)==prepared
