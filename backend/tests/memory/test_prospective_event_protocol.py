"""Protocol fixtures only: no real publication receipt or default event provider.

Real SQLite journal + public SDK authority DTO; transport below is a controlled
consumer used solely to distinguish durable handoff/replay from invalidation.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from simple_harness.runtime import ProspectiveEventTrigger, issue_prospective_signal_authority
from deskpet.memory.prospective_event_codec import (
    DOMAIN, EventRead, PreparedEvent, check_cut, event_signal_id,
)
from deskpet.memory.prospective_event_source import PublicEventAuthoritySource
from deskpet.memory.prospective_scheduler import PreparedTimer, ProspectiveScheduler
from deskpet.memory.prospective_signal_store import ProspectiveSignalStore, TimerConflict
from deskpet.memory.s5c_timer_schema import initialize_s5c_timer_state_db
from deskpet.task_scope.protocol import canonical_hash
from tests.memory.test_s5c_store import P, ready, registration


def prepared(owner):
    _, a = registration()
    trigger = ProspectiveEventTrigger('fixture:publication', 'published', canonical_hash('published'))
    r = replace(a.intent, trigger=trigger)
    confirmation = dict(sdk_run_id='publication-run', effect_id='effect-1', raw_call_id='call-1',
        tool_name='fixture_publish', arguments_hash='a'*64, result_hash='b'*64,
        event_authority_ref=trigger.event_authority_ref, condition_hash=trigger.condition_hash,
        destination_id='fixture-destination', configuration_hash='c'*64, artifact_hash='d'*64,
        confirmation_id='fixture-confirmation', confirmation_hash='e'*64)
    cut = dict(kind='host_effect_admission/v1', namespace='fixture:primary_effect_identities',
        registration_authority_hash=a.authority_hash, cut_sequence=3, admission_sequence=4,
        cut_record_hash='f'*64, admission_record_hash='0'*64)
    observation = dict(schema_version=1, kind='host_event_observation', owner_key=owner,
        subject=r.subject, memory_id=r.target_memory_id, target_revision=r.target_revision,
        scheduler_registration_ref=r.scheduler_registration_ref, registration_revision=r.registration_revision,
        trigger_hash=r.trigger_hash, observed_at=20.0, run_id=r.run_id, operation_id=r.operation_id,
        confirmation=confirmation, causal_cut=cut)
    digest = canonical_hash(observation)
    i = replace(r, signal_id=event_signal_id(owner,r), signal_kind='event_occurred',
        transition_to='triggered', observed_at=20.0, outbox_id=None, outbox_payload_hash=None,
        signal_receipt_id='host:event-observation:'+digest, signal_receipt_hash=digest)
    a = issue_prospective_signal_authority(i, authority_id='host:event-authority:'+i.signal_id,
        issued_at=20, expires_at=25, nonce=i.signal_id, issuer_ref=DOMAIN)
    return PreparedEvent(a,observation)


async def store(tmp_path):
    path, _ = await ready(tmp_path)
    await initialize_s5c_timer_state_db(path)
    return ProspectiveSignalStore(path,P)


@pytest.mark.asyncio
async def test_event_codec_reopen_exact_domain_and_version(tmp_path):
    s = await store(tmp_path)
    p = prepared(s.owner)
    await s.prepare(p)
    reopened = ProspectiveSignalStore(s.path,P)
    assert await reopened.get_prepared(p.authority.intent.signal_id) == p
    assert (await reopened.resolve_prospective_signal_authority(
        __import__('simple_harness.runtime',fromlist=['ProspectiveSignalAuthorityRef']).ProspectiveSignalAuthorityRef.from_authority(p.authority))) == p.authority
    for version in (True, 1.0, 2):
        body = dict(p.observation, schema_version=version)
        digest = canonical_hash(body)
        intent = replace(p.authority.intent, signal_receipt_hash=digest,
            signal_receipt_id='host:event-observation:'+digest)
        with pytest.raises(ValueError, match='version|binding'):
            await s.prepare(PreparedEvent(replace(p.authority,intent=intent),body))
    with pytest.raises(ValueError):
        await s.prepare(PreparedTimer(p.authority,p.observation))
    with pytest.raises(ValueError,match='order'):
        check_cut(dict(p.observation['causal_cut'],admission_sequence=3))
    with pytest.raises(ValueError,match='order'):
        check_cut(dict(p.observation['causal_cut'],cut_sequence=True))


class Source:
    live = True
    async def registration_is_live(self, authority):return self.live
    async def prepare_due(self, **kwargs):return ()


@pytest.mark.asyncio
async def test_event_handoff_lost_ack_reopen_replays_before_invalidation(tmp_path):
    s = await store(tmp_path); p = prepared(s.owner); await s.prepare(p)
    source = Source(); calls=[]; clock=[20.0]
    class Transport:
        committed = None
        async def apply_prospective_signal(self, **kwargs):
            calls.append(kwargs['reference'])
            if self.committed is None:
                i=p.authority.intent
                self.committed=SimpleNamespace(signal_id=i.signal_id,memory_id=i.target_memory_id,
                    base_revision=i.target_revision,result_hash='a'*64,to_json=lambda:{'fixture':'consumed'})
                raise TimeoutError('fixture lost ACK after commit')
            return self.committed
    transport=Transport()
    scheduler=ProspectiveScheduler(store=s,source=Source(),event_source=source,
        memory=transport,clock=lambda:clock[0],lease_seconds=2)
    with pytest.raises(TimeoutError):await scheduler.tick(claim_owner='first')
    clock[0]=40.0;source.live=False
    s=ProspectiveSignalStore(s.path,P)
    assert await ProspectiveScheduler(store=s,source=Source(),event_source=source,
        memory=transport,clock=lambda:clock[0]).tick(claim_owner='restart') == 1
    assert len(calls)==2 and calls[0]==calls[1]
    assert await s.claim(owner='next',now=80,lease_seconds=1) is None


@pytest.mark.asyncio
async def test_event_fresh_invalidation_and_missing_source_do_not_apply(tmp_path):
    s=await store(tmp_path);p=prepared(s.owner);await s.prepare(p)
    class Never:
        async def apply_prospective_signal(self,**kwargs):pytest.fail('unexpected apply')
    with pytest.raises(ValueError,match='source_unavailable'):
        await ProspectiveScheduler(store=s,source=Source(),memory=Never(),clock=lambda:20).tick(claim_owner='missing')
    source=Source();source.live=False
    assert await ProspectiveScheduler(store=s,source=Source(),event_source=source,
        memory=Never(),clock=lambda:60).tick(claim_owner='recovery') == 0
    assert await s.claim(owner='later',now=100,lease_seconds=1) is None
    class Registrations:
        owner=s.owner;principal=P
        async def page_accepted_registrations(self,**kwargs):pytest.fail('missing reader scanned')
    missing=PublicEventAuthoritySource(registrations=Registrations(),signals=s)
    assert await missing.prepare_due(now=60,limit=1)==()
    assert missing.last_read==EventRead('unverifiable','production_event_source_unavailable')


@pytest.mark.asyncio
async def test_event_unverifiable_keeps_cursor_and_wrong_registration_cut_rejected(tmp_path):
    s=await store(tmp_path)
    p=prepared(s.owner)
    _, original=registration()
    reg=SimpleNamespace(authority=replace(original,intent=replace(original.intent,trigger=p.authority.intent.trigger)))
    class Registrations:
        owner=s.owner;principal=P
        calls=[]
        async def page_accepted_registrations(self,**kwargs):
            self.calls.append(kwargs)
            return (reg,),1,2
        async def accepted_registration(self,**kwargs):return reg
        async def accepted_invalidation(self,**kwargs):return None
    class Reader:
        result=EventRead('unverifiable','host_registration_cut_not_captured')
        async def read_confirmed_event(self,**kwargs):return self.result
    registrations=Registrations();reader=Reader()
    source=PublicEventAuthoritySource(registrations=registrations,signals=s,reader=reader)
    assert await source.prepare_due(now=20,limit=1)==()
    assert await source.prepare_due(now=21,limit=1)==()
    assert [c['after'] for c in registrations.calls]==[0,0]
    reader.result=EventRead('confirmed','fixture-only',p.observation['confirmation'],p.observation['causal_cut'])
    with pytest.raises(ValueError,match='registration_binding_differs'):
        await source.prepare_due(now=22,limit=1)
    assert await s.get_prepared(p.authority.intent.signal_id) is None
    good_cut=dict(p.observation['causal_cut'],registration_authority_hash=reg.authority.authority_hash)
    reader.result=EventRead('confirmed','fixture-only',p.observation['confirmation'],good_cut)
    first=(await source.prepare_due(now=23,limit=1))[0]
    assert type(first) is PreparedEvent
    assert first.observation['causal_cut']==good_cut
    # Durable first grant survives later observation time and does not call reader.
    reader.result=EventRead('unverifiable','fixture later unavailable')
    assert (await source.prepare_due(now=40,limit=1))[0]==first
